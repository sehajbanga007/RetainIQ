"""Data ingestion: CSV uploads and real-time API events from businesses.

Every incoming record is matched to a person in the network by email or phone
(identity resolution). New people are created; existing people get linked to the
business, which is how the shared persona network grows.
"""
import csv
import io
import re
from datetime import date, datetime

from .db import get_conn, get_meta, set_meta

SAMPLES = {
    "orders": "email,phone,name,city,order_date,amount,category,discount_used,returned\n"
              "riya.sharma@example.com,9876500011,Riya Sharma,Mumbai,2026-10-02,1499,Western Wear,1,0\n"
              "riya.sharma@example.com,9876500011,Riya Sharma,Mumbai,2026-10-20,899,Accessories,0,0\n"
              "arjun.mehta@example.com,9876500012,Arjun Mehta,Pune,2026-09-14,2599,Footwear,0,1\n",
    "customers": "email,phone,name,city,address,age,joined_at,plan\n"
                 "riya.sharma@example.com,9876500011,Riya Sharma,Mumbai,\"Flat 402, Lotus Heights, Andheri West, Mumbai\",27,2026-03-10,Member\n"
                 "arjun.mehta@example.com,9876500012,Arjun Mehta,Pune,\"Flat 1203, Green Enclave, Baner, Pune\",31,2026-05-22,Regular\n",
    "sessions": "email,phone,week_start,count\n"
                "riya.sharma@example.com,9876500011,2026-10-19,4\n"
                "arjun.mehta@example.com,9876500012,2026-10-19,1\n",
    "tickets": "email,phone,created_at,issue,resolved,csat\n"
               "arjun.mehta@example.com,9876500012,2026-10-15,Refund not processed,0,2\n",
}


def _clean_phone(p):
    if not p:
        return None
    d = re.sub(r"\D", "", str(p))
    return d[-10:] if len(d) >= 10 else None


def _clean_email(e):
    e = (e or "").strip().lower()
    return e if "@" in e else None


def _date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def resolve_person(conn, email=None, phone=None, name=None, city=None, address=None, age=None, when=None):
    """Find a person by email/phone or create one. Returns (person_id, created?)."""
    email, phone = _clean_email(email), _clean_phone(phone)
    if not email and not phone:
        return None, False
    row = None
    if email:
        row = conn.execute("SELECT * FROM persons WHERE email=?", (email,)).fetchone()
    if not row and phone:
        row = conn.execute("SELECT * FROM persons WHERE phone=?", (phone,)).fetchone()
    if row:
        # enrich missing identity fields
        updates = {}
        for k, v in (("email", email), ("phone", phone), ("city", city), ("address", address)):
            if v and not row[k]:
                updates[k] = v
        if updates:
            conn.execute("UPDATE persons SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?",
                         (*updates.values(), row["id"]))
        return row["id"], False
    cur = conn.execute("INSERT INTO persons(name, email, phone, city, address, age, created_at) VALUES (?,?,?,?,?,?,?)",
                       ((name or "Unknown").strip().title(), email, phone, city, address,
                        int(age) if str(age or "").isdigit() else None, (when or date.today()).isoformat()))
    return cur.lastrowid, True


def ensure_relationship(conn, business_id, person_id, joined, plan=None, source="upload"):
    row = conn.execute("SELECT id, joined_at FROM relationships WHERE business_id=? AND person_id=?",
                       (business_id, person_id)).fetchone()
    if row:
        if joined and joined.isoformat() < row["joined_at"]:
            conn.execute("UPDATE relationships SET joined_at=? WHERE id=?", (joined.isoformat(), row["id"]))
        if plan:
            conn.execute("UPDATE relationships SET plan=? WHERE id=?", (plan, row["id"]))
        return False
    conn.execute("INSERT INTO relationships(business_id, person_id, joined_at, plan, source) VALUES (?,?,?,?,?)",
                 (business_id, person_id, (joined or date.today()).isoformat(), plan or "Regular", source))
    return True


def _bump_as_of(d):
    cur = get_meta("as_of_date")
    today = date.today()
    if d and cur and d.isoformat() > cur:
        set_meta("as_of_date", min(d, today).isoformat())


def ingest_csv(business_id, kind, file_bytes):
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
    stats = dict(rows=0, inserted=0, skipped=0, new_people=0, new_customers=0, errors=[])
    max_date = None
    with get_conn() as conn:
        for i, r in enumerate(reader, start=2):
            stats["rows"] += 1
            r = {k: (v or "").strip() for k, v in r.items() if k}
            try:
                when = _date(r.get("order_date") or r.get("joined_at") or r.get("week_start") or r.get("created_at"))
                pid, created = resolve_person(conn, r.get("email"), r.get("phone"), r.get("name"), r.get("city"),
                                              r.get("address"), r.get("age"), when)
                if not pid:
                    raise ValueError("needs a valid email or phone")
                stats["new_people"] += int(created)
                if kind == "customers":
                    joined = _date(r.get("joined_at")) or date.today()
                    stats["new_customers"] += int(ensure_relationship(conn, business_id, pid, joined, r.get("plan") or None))
                    stats["inserted"] += 1
                elif kind == "orders":
                    amt = float(r.get("amount") or 0)
                    if not when or amt <= 0:
                        raise ValueError("invalid order_date or amount")
                    stats["new_customers"] += int(ensure_relationship(conn, business_id, pid, when))
                    conn.execute("INSERT INTO orders(business_id, person_id, order_date, amount, category, discount_used, returned)"
                                 " VALUES (?,?,?,?,?,?,?)",
                                 (business_id, pid, when.isoformat(), amt, r.get("category") or "Other",
                                  int(r.get("discount_used") in ("1", "true", "yes", "TRUE")),
                                  int(r.get("returned") in ("1", "true", "yes", "TRUE"))))
                    stats["inserted"] += 1
                elif kind == "sessions":
                    if not when:
                        raise ValueError("invalid week_start")
                    stats["new_customers"] += int(ensure_relationship(conn, business_id, pid, when))
                    conn.execute("INSERT INTO sessions(business_id, person_id, week_start, count) VALUES (?,?,?,?)",
                                 (business_id, pid, when.isoformat(), int(float(r.get("count") or 1))))
                    stats["inserted"] += 1
                elif kind == "tickets":
                    if not when:
                        raise ValueError("invalid created_at")
                    stats["new_customers"] += int(ensure_relationship(conn, business_id, pid, when))
                    conn.execute("INSERT INTO tickets(business_id, person_id, created_at, issue, resolved, csat) VALUES (?,?,?,?,?,?)",
                                 (business_id, pid, when.isoformat(), r.get("issue") or "General",
                                  int(r.get("resolved") in ("1", "true", "yes", "TRUE")),
                                  int(float(r.get("csat"))) if r.get("csat") else None))
                    stats["inserted"] += 1
                else:
                    raise ValueError(f"unknown upload type {kind}")
                if when and (max_date is None or when > max_date):
                    max_date = when
            except Exception as e:
                stats["skipped"] += 1
                if len(stats["errors"]) < 10:
                    stats["errors"].append(f"Row {i}: {e}")
    if kind in ("orders", "sessions"):
        _bump_as_of(max_date)
    return stats


def ingest_order_event(business_id, payload):
    when = _date(payload.get("order_date")) or date.today()
    with get_conn() as conn:
        pid, created = resolve_person(conn, payload.get("email"), payload.get("phone"), payload.get("name"),
                                      payload.get("city"), payload.get("address"), payload.get("age"), when)
        if not pid:
            raise ValueError("email or phone is required")
        new_rel = ensure_relationship(conn, business_id, pid, when, source="api")
        conn.execute("INSERT INTO orders(business_id, person_id, order_date, amount, category, discount_used, returned)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (business_id, pid, when.isoformat(), float(payload.get("amount") or 0), payload.get("category") or "Other",
                      int(bool(payload.get("discount_used"))), int(bool(payload.get("returned")))))
    _bump_as_of(when)
    return dict(person_id=pid, new_person=created, new_customer=new_rel)
