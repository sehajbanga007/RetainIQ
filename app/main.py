"""RetainIQ web server: REST API + web dashboard."""
import csv
import io
import json
import os
import secrets
import threading
from datetime import datetime, date
from functools import wraps

from flask import Flask, jsonify, request, session, send_from_directory, Response, abort
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_conn, init_db, get_meta, jload, BASE_DIR, DB_PATH
from .ingest import ingest_csv, ingest_order_event, resolve_person, ensure_relationship, SAMPLES
from .agents.coordinator import CoordinatorAgent, AGENTS, current_as_of
from .agents.preprocessing import FEATURE_LABELS, load_raw
from .agents.behavior import monthly_series

STATIC = os.path.join(BASE_DIR, "app", "static")
app = Flask(__name__, static_folder=None)
coordinator = CoordinatorAgent()
_run_state = {"running": False, "last": None, "error": None, "started": None}

BUSINESS_CATEGORIES = ["Fashion E-commerce", "Electronics", "Grocery Delivery", "OTT Streaming", "Food Delivery",
                       "Fitness", "Other"]


# ---------------------------------------------------------------- helpers
def ok(data=None, **kw):
    if data is None:
        data = {}
    data.update(kw)
    return jsonify(data)


def err(msg, code=400):
    return jsonify(error=msg), code


def login_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if not session.get("bid"):
            return err("Not logged in", 401)
        return fn(*a, **kw)
    return wrap


def api_key_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if not key:
            return err("Missing X-API-Key header", 401)
        with get_conn() as conn:
            b = conn.execute("SELECT * FROM businesses WHERE api_key=?", (key,)).fetchone()
        if not b:
            return err("Invalid API key", 401)
        request.business = dict(b)
        return fn(*a, **kw)
    return wrap


def clean(o):
    """Replace NaN/inf with None recursively so responses are always valid JSON."""
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


# ---------------------------------------------------------------- automatic agent runs
# The agents run by themselves: once at start-up, on a fixed schedule, and shortly after new
# data arrives (orders / one-tap logins / uploads through the API). Businesses never trigger them.
AUTO_INTERVAL_MIN = int(os.environ.get("RETAINIQ_INTERVAL_MIN", 30))
_refresh_timer = {"t": None}


def request_refresh(reason, delay=60):
    """Debounced: many events in a short time lead to a single agent run."""
    if _refresh_timer["t"]:
        _refresh_timer["t"].cancel()
    t = threading.Timer(delay, lambda: run_pipeline_async(None, f"auto:{reason}"))
    t.daemon = True
    _refresh_timer["t"] = t
    t.start()


def _scheduler():
    import time
    time.sleep(5)
    run_pipeline_async(None, "auto:startup")
    while True:
        time.sleep(AUTO_INTERVAL_MIN * 60)
        run_pipeline_async(None, "auto:scheduled")


def bid():
    return session.get("bid")


def business(conn, b_id):
    r = conn.execute("SELECT id, name, category, price_level, is_subscription, email, api_key, created_at "
                     "FROM businesses WHERE id=?", (b_id,)).fetchone()
    return dict(r) if r else None


def score_row(r):
    d = dict(r)
    for k in ("explanation", "actions", "features"):
        if k in d:
            d[k] = jload(d[k], [])
    return d


def run_pipeline_async(business_id, trigger, retrain=False):
    if _run_state["running"]:
        return False

    def work():
        _run_state.update(running=True, error=None, started=datetime.now().isoformat(timespec="seconds"))
        try:
            _run_state["last"] = coordinator.run(business_id, trigger, retrain)
        except Exception as e:  # pragma: no cover
            _run_state["error"] = str(e)
        finally:
            _run_state["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return True


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/checkout-demo")
def checkout_demo():
    return send_from_directory(STATIC, "checkout.html")


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


# ---------------------------------------------------------------- auth
@app.post("/api/auth/login")
def login():
    d = request.get_json(force=True, silent=True) or {}
    with get_conn() as conn:
        b = conn.execute("SELECT * FROM businesses WHERE email=?", ((d.get("email") or "").strip().lower(),)).fetchone()
    if not b or not check_password_hash(b["password_hash"], d.get("password") or ""):
        return err("Invalid email or password", 401)
    session["bid"] = b["id"]
    return ok(business=dict(id=b["id"], name=b["name"], category=b["category"]))


@app.post("/api/auth/register")
def register():
    d = request.get_json(force=True, silent=True) or {}
    name, email, pw = (d.get("name") or "").strip(), (d.get("email") or "").strip().lower(), d.get("password") or ""
    cat = d.get("category") if d.get("category") in BUSINESS_CATEGORIES else "Other"
    if not name or "@" not in email or len(pw) < 6:
        return err("Business name, valid email and a password of 6+ characters are required")
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM businesses WHERE email=?", (email,)).fetchone():
            return err("An account with this email already exists")
        cur = conn.execute("INSERT INTO businesses(name, category, price_level, is_subscription, email, password_hash, api_key, created_at)"
                           " VALUES (?,?,?,?,?,?,?,?)",
                           (name, cat, int(d.get("price_level") or 2), int(cat in ("OTT Streaming", "Fitness")), email,
                            generate_password_hash(pw), "riq_live_" + secrets.token_hex(12), date.today().isoformat()))
        session["bid"] = cur.lastrowid
    return ok(business=dict(id=session["bid"], name=name, category=cat))


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return ok()


@app.get("/api/auth/me")
@login_required
def me():
    with get_conn() as conn:
        b = business(conn, bid())
    if not b:
        session.clear()
        return err("Not logged in", 401)
    b["as_of"] = str(current_as_of().date())
    with get_conn() as conn:
        r = conn.execute("SELECT finished_at FROM pipeline_runs WHERE status='Completed' ORDER BY finished_at DESC LIMIT 1").fetchone()
    b["last_scored"] = r["finished_at"] if r else None
    from . import simulator
    b["live"] = simulator.status()
    return ok(business=b, categories=BUSINESS_CATEGORIES)


@app.get("/api/meta/demo-accounts")
def demo_accounts():
    with get_conn() as conn:
        rows = conn.execute("SELECT name, category, email FROM businesses ORDER BY id LIMIT 6").fetchall()
    return ok(accounts=[dict(r) for r in rows], password="demo123")


# ---------------------------------------------------------------- dashboard
@app.get("/api/dashboard")
@login_required
def dashboard():
    b_id = bid()
    with get_conn() as conn:
        b = business(conn, b_id)
        k = conn.execute("""SELECT COUNT(*) n, AVG(churn_prob) avg_p,
                SUM(risk_tier='High') high, SUM(risk_tier='Medium') medium, SUM(risk_tier='Low') low,
                SUM(revenue_at_risk) rar, SUM(clv) clv, SUM(priority='P1 - Critical') critical
                FROM scores WHERE business_id=?""", (b_id,)).fetchone()
        total_rel = conn.execute("SELECT COUNT(*) c FROM relationships WHERE business_id=?", (b_id,)).fetchone()["c"]
        bins = [0] * 10
        for r in conn.execute("SELECT churn_prob FROM scores WHERE business_id=?", (b_id,)):
            bins[min(int(r["churn_prob"] * 10), 9)] += 1
        seg = {r["segment"]: r["c"] for r in conn.execute(
            "SELECT segment, COUNT(*) c FROM scores WHERE business_id=? GROUP BY 1 ORDER BY 2 DESC", (b_id,))}
        seg_risk = [dict(r) for r in conn.execute(
            "SELECT segment, AVG(churn_prob) p, COUNT(*) c FROM scores WHERE business_id=? GROUP BY 1 ORDER BY 2 DESC", (b_id,))]
        eng = {r["engagement"]: r["c"] for r in conn.execute(
            "SELECT engagement, COUNT(*) c FROM scores WHERE business_id=? GROUP BY 1", (b_id,))}
        trend = {r["trend"]: r["c"] for r in conn.execute(
            "SELECT trend, COUNT(*) c FROM scores WHERE business_id=? GROUP BY 1", (b_id,))}
        top = [score_row(r) for r in conn.execute(
            """SELECT s.person_id, p.name, p.email, s.churn_prob, s.risk_tier, s.priority, s.clv, s.revenue_at_risk,
                      s.top_reason, s.actions, s.segment FROM scores s JOIN persons p ON p.id=s.person_id
               WHERE s.business_id=? AND s.risk_tier!='Low' ORDER BY s.priority, s.revenue_at_risk DESC LIMIT 8""", (b_id,))]
        acts = {r["status"]: r["c"] for r in conn.execute(
            "SELECT status, COUNT(*) c FROM retention_actions WHERE business_id=? GROUP BY 1", (b_id,))}
        tiers = {r["risk_tier"]: dict(c=r["c"], rar=r["rar"]) for r in conn.execute(
            "SELECT risk_tier, COUNT(*) c, SUM(revenue_at_risk) rar FROM scores WHERE business_id=? GROUP BY 1", (b_id,))}
        hist = [dict(r) for r in conn.execute(
            "SELECT scored_at d, AVG(churn_prob) p, SUM(churn_prob>=0.6) high FROM score_history WHERE business_id=? "
            "GROUP BY 1 ORDER BY 1", (b_id,))]
        fac = jload((conn.execute("SELECT data FROM insights WHERE key=?", (f"factors:{b_id}",)).fetchone() or {"data": None})["data"], {})
        run = conn.execute("SELECT * FROM pipeline_runs WHERE status='Completed' ORDER BY finished_at DESC LIMIT 1").fetchone()
        model = conn.execute("SELECT id, algorithm, metrics, created_at FROM model_versions WHERE is_active=1").fetchone()
        shared = conn.execute("""SELECT COUNT(*) c FROM relationships r WHERE r.business_id=? AND
                 (SELECT COUNT(*) FROM relationships r2 WHERE r2.person_id=r.person_id) > 1""", (b_id,)).fetchone()["c"]
    closed = acts.get("Retained", 0) + acts.get("Churned", 0)
    return ok(
        business=b,
        kpis=dict(active=k["n"] or 0, total_customers=total_rel, avg_churn=round(k["avg_p"] or 0, 4),
                  high=k["high"] or 0, medium=k["medium"] or 0, low=k["low"] or 0, critical=k["critical"] or 0,
                  revenue_at_risk=k["rar"] or 0, total_value=k["clv"] or 0, shared_with_network=shared,
                  actions_open=acts.get("Sent", 0) + acts.get("Planned", 0), retained=acts.get("Retained", 0),
                  retention_rate=round(acts.get("Retained", 0) / closed, 3) if closed else None),
        distribution=bins, segments=seg, segment_risk=seg_risk, engagement=eng, trend=trend, top=top,
        history=hist, factors=fac, tiers=tiers, last_run=dict(run) if run else None,
        model=dict(id=model["id"], algorithm=model["algorithm"], metrics=jload(model["metrics"]),
                   created_at=model["created_at"]) if model else None,
        as_of=str(current_as_of().date()))


# ---------------------------------------------------------------- customers
@app.get("/api/customers")
@login_required
def customers():
    b_id = bid()
    q = (request.args.get("q") or "").strip().lower()
    risk = request.args.get("risk")
    segment = request.args.get("segment")
    sort = request.args.get("sort", "risk")
    page = max(int(request.args.get("page", 1)), 1)
    per = min(int(request.args.get("per", 25)), 200)
    where, params = ["s.business_id=?"], [b_id]
    if q:
        where.append("(LOWER(p.name) LIKE ? OR p.email LIKE ? OR p.phone LIKE ?)")
        params += [f"%{q}%"] * 3
    if risk in ("High", "Medium", "Low"):
        where.append("s.risk_tier=?")
        params.append(risk)
    if segment:
        where.append("s.segment=?")
        params.append(segment)
    order = {"risk": "s.churn_prob DESC", "value": "s.clv DESC", "rar": "s.revenue_at_risk DESC",
             "name": "p.name ASC", "priority": "s.priority ASC, s.revenue_at_risk DESC"}.get(sort, "s.churn_prob DESC")
    w = " AND ".join(where)
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM scores s JOIN persons p ON p.id=s.person_id WHERE {w}", params).fetchone()["c"]
        rows = [score_row(r) for r in conn.execute(
            f"""SELECT s.person_id, p.name, p.email, p.phone, p.city, s.churn_prob, s.risk_tier, s.priority, s.clv,
                       s.revenue_at_risk, s.engagement, s.trend, s.segment, s.top_reason, s.actions
                FROM scores s JOIN persons p ON p.id=s.person_id WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?""",
            params + [per, (page - 1) * per])]
        segs = [r["segment"] for r in conn.execute("SELECT DISTINCT segment FROM scores WHERE business_id=?", (b_id,))]
    for r in rows:
        r["next_action"] = r["actions"][0]["action"] if r["actions"] else None
        del r["actions"]
    return ok(total=total, page=page, per=per, rows=rows, segments=sorted(segs))


@app.get("/api/customers/<int:pid>")
@login_required
def customer(pid):
    b_id = bid()
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
        rel = conn.execute("SELECT * FROM relationships WHERE business_id=? AND person_id=?", (b_id, pid)).fetchone()
        if not p or not rel:
            return err("Customer not found", 404)
        s = conn.execute("SELECT * FROM scores WHERE business_id=? AND person_id=?", (b_id, pid)).fetchone()
        persona = conn.execute("SELECT data FROM personas WHERE person_id=?", (pid,)).fetchone()
        orders = [dict(r) for r in conn.execute(
            "SELECT order_date, amount, category, discount_used, returned FROM orders WHERE business_id=? AND person_id=? "
            "ORDER BY order_date DESC LIMIT 10", (b_id, pid))]
        tickets = [dict(r) for r in conn.execute(
            "SELECT created_at, issue, resolved, csat FROM tickets WHERE business_id=? AND person_id=? ORDER BY created_at DESC LIMIT 10",
            (b_id, pid))]
        actions = [dict(r) for r in conn.execute(
            "SELECT * FROM retention_actions WHERE business_id=? AND person_id=? ORDER BY id DESC", (b_id, pid))]
        hist = [dict(r) for r in conn.execute(
            "SELECT scored_at, churn_prob FROM score_history WHERE business_id=? AND person_id=? ORDER BY scored_at", (b_id, pid))]
    raw = load_raw()
    series = monthly_series(raw, b_id, pid, current_as_of())
    pers = clean(jload(persona["data"])) if persona else None
    return ok(person=dict(p), relationship=dict(rel), score=score_row(s) if s else None, persona=pers,
              orders=orders, tickets=tickets, actions=actions, history=hist, series=series,
              status="Active" if s else "Churned / inactive", feature_labels=FEATURE_LABELS)


@app.post("/api/customers/<int:pid>/actions")
@login_required
def create_action(pid):
    d = request.get_json(force=True, silent=True) or {}
    if not d.get("action"):
        return err("action is required")
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        s = conn.execute("SELECT churn_prob FROM scores WHERE business_id=? AND person_id=?", (bid(), pid)).fetchone()
        cur = conn.execute("INSERT INTO retention_actions(business_id, person_id, action, channel, status, churn_prob_at_action,"
                           " note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (bid(), pid, d["action"], d.get("channel"), d.get("status") or "Sent",
                            s["churn_prob"] if s else None, d.get("note"), now, now))
    return ok(id=cur.lastrowid)


@app.get("/api/actions")
@login_required
def list_actions():
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT a.*, p.name, p.email, s.churn_prob current_prob FROM retention_actions a
               JOIN persons p ON p.id=a.person_id
               LEFT JOIN scores s ON s.business_id=a.business_id AND s.person_id=a.person_id
               WHERE a.business_id=? ORDER BY a.id DESC LIMIT 500""", (bid(),))]
    by = {}
    for r in rows:
        st = by.setdefault(r["action"], dict(action=r["action"], total=0, retained=0, churned=0, open=0))
        st["total"] += 1
        st["retained" if r["status"] == "Retained" else "churned" if r["status"] == "Churned" else "open"] += 1
    return ok(rows=rows, by_action=sorted(by.values(), key=lambda x: -x["total"]))


@app.patch("/api/actions/<int:aid>")
@login_required
def update_action(aid):
    d = request.get_json(force=True, silent=True) or {}
    if d.get("status") not in ("Planned", "Sent", "Retained", "Churned"):
        return err("status must be Planned, Sent, Retained or Churned")
    with get_conn() as conn:
        conn.execute("UPDATE retention_actions SET status=?, note=COALESCE(?, note), updated_at=? WHERE id=? AND business_id=?",
                     (d["status"], d.get("note"), datetime.now().isoformat(timespec="seconds"), aid, bid()))
    return ok()


@app.get("/api/export/customers.csv")
@login_required
def export_customers():
    with get_conn() as conn:
        rows = conn.execute("""SELECT p.name, p.email, p.phone, p.city, s.churn_prob, s.risk_tier, s.priority, s.clv,
                    s.revenue_at_risk, s.engagement, s.trend, s.segment, s.top_reason, s.actions
                    FROM scores s JOIN persons p ON p.id=s.person_id WHERE s.business_id=? ORDER BY s.churn_prob DESC""",
                            (bid(),)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "phone", "city", "churn_probability", "risk_tier", "priority", "est_12m_value",
                "revenue_at_risk", "engagement", "trend", "segment", "top_reason", "recommended_action"])
    for r in rows:
        a = jload(r["actions"], [])
        w.writerow([r["name"], r["email"], r["phone"], r["city"], round(r["churn_prob"], 4), r["risk_tier"], r["priority"],
                    r["clv"], r["revenue_at_risk"], r["engagement"], r["trend"], r["segment"], r["top_reason"],
                    a[0]["action"] + " - " + a[0]["offer"] if a else ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=retainiq_customers.csv"})


# ---------------------------------------------------------------- network / personas
def _persona_for(conn, person_id, requester_id):
    row = conn.execute("SELECT data, updated_at FROM personas WHERE person_id=?", (person_id,)).fetchone()
    if not row:
        return None
    p = clean(jload(row["data"]))
    s = conn.execute("SELECT churn_prob, risk_tier, top_reason FROM scores WHERE business_id=? AND person_id=?",
                     (requester_id, person_id)).fetchone()
    p["with_you"] = dict(s) if s else None
    p["is_your_customer"] = bool(conn.execute("SELECT 1 FROM relationships WHERE business_id=? AND person_id=?",
                                              (requester_id, person_id)).fetchone())
    return p


def _find_person(conn, q):
    q = (q or "").strip()
    if not q:
        return None
    if "@" in q:
        r = conn.execute("SELECT id FROM persons WHERE email=?", (q.lower(),)).fetchone()
    else:
        digits = "".join(c for c in q if c.isdigit())[-10:]
        r = conn.execute("SELECT id FROM persons WHERE phone=?", (digits,)).fetchone() if len(digits) == 10 else None
    return r["id"] if r else None


@app.get("/api/network/stats")
@login_required
def network_stats():
    with get_conn() as conn:
        persons = conn.execute("SELECT COUNT(*) c FROM persons").fetchone()["c"]
        bizs = [dict(r) for r in conn.execute(
            """SELECT b.id, b.name, b.category, COUNT(r.id) customers,
                      (SELECT COUNT(*) FROM scores s WHERE s.business_id=b.id) active
               FROM businesses b LEFT JOIN relationships r ON r.business_id=b.id GROUP BY b.id ORDER BY customers DESC""")]
        multi = conn.execute("SELECT COUNT(*) c FROM (SELECT person_id FROM relationships GROUP BY 1 HAVING COUNT(*)>1)").fetchone()["c"]
        arche, bands = {}, {}
        for r in conn.execute("SELECT data FROM personas"):
            d = jload(r["data"], {})
            arche[d.get("archetype")] = arche.get(d.get("archetype"), 0) + 1
            bands[d.get("spend_band")] = bands.get(d.get("spend_band"), 0) + 1
        lookups = conn.execute("SELECT COUNT(*) c FROM network_lookups WHERE business_id=?", (bid(),)).fetchone()["c"]
        overlap = [dict(r) for r in conn.execute(
            """SELECT b.name, COUNT(*) c FROM relationships r1 JOIN relationships r2 ON r1.person_id=r2.person_id AND r2.business_id!=r1.business_id
               JOIN businesses b ON b.id=r2.business_id WHERE r1.business_id=? GROUP BY b.name ORDER BY c DESC""", (bid(),))]
        recent = [dict(r) for r in conn.execute(
            """SELECT l.at, l.channel, p.name, p.email FROM network_lookups l JOIN persons p ON p.id=l.person_id
               WHERE l.business_id=? ORDER BY l.id DESC LIMIT 8""", (bid(),))]
    return ok(persons=persons, businesses=bizs, multi_brand=multi, archetypes=arche, spend_bands=bands,
              lookups=lookups, overlap=overlap, recent_lookups=recent)


@app.get("/api/network/search")
@login_required
def network_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return ok(rows=[])
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, name, email, phone, city FROM persons WHERE LOWER(name) LIKE ? OR email LIKE ? OR phone LIKE ? LIMIT 12",
            (f"%{q.lower()}%", f"%{q.lower()}%", f"%{q}%"))]
    return ok(rows=rows)


@app.get("/api/network/persona/<int:person_id>")
@login_required
def network_persona(person_id):
    with get_conn() as conn:
        p = _persona_for(conn, person_id, bid())
        if not p:
            return err("No persona for this person yet", 404)
        conn.execute("INSERT INTO network_lookups(business_id, person_id, at, consent, channel) VALUES (?,?,?,?,?)",
                     (bid(), person_id, datetime.now().isoformat(timespec="seconds"), 1, "dashboard"))
    return ok(persona=p)


# ---------------------------------------------------------------- public API (for businesses' apps)
@app.get("/api/v1/persona")
@api_key_required
def api_persona():
    with get_conn() as conn:
        pid = _find_person(conn, request.args.get("email") or request.args.get("phone"))
        if not pid:
            return err("Person not found in RetainIQ network", 404)
        p = _persona_for(conn, pid, request.business["id"])
        conn.execute("INSERT INTO network_lookups(business_id, person_id, at, consent, channel) VALUES (?,?,?,?,?)",
                     (request.business["id"], pid, datetime.now().isoformat(timespec="seconds"), 1, "api"))
    return ok(persona=p)


@app.post("/api/v1/network-login")
@api_key_required
def api_network_login():
    """One-tap login/checkout: returns identity + persona and links the person to the calling business."""
    d = request.get_json(force=True, silent=True) or {}
    if not d.get("consent"):
        return err("User must accept the RetainIQ terms to continue", 403)
    b_id = request.business["id"]
    with get_conn() as conn:
        pid = _find_person(conn, d.get("phone") or d.get("email"))
        new_person = False
        if not pid:
            if not d.get("name"):
                return ok(found=False, message="New user — ask for name to create a RetainIQ profile")
            pid, new_person = resolve_person(conn, d.get("email"), d.get("phone"), d.get("name"), d.get("city"),
                                             d.get("address"))
        ensure_relationship(conn, b_id, pid, date.today(), source="network")
        conn.execute("UPDATE relationships SET consent=1 WHERE business_id=? AND person_id=?", (b_id, pid))
        person = dict(conn.execute("SELECT id, name, email, phone, city, address FROM persons WHERE id=?", (pid,)).fetchone())
        p = _persona_for(conn, pid, b_id)
        conn.execute("INSERT INTO network_lookups(business_id, person_id, at, consent, channel) VALUES (?,?,?,?,?)",
                     (b_id, pid, datetime.now().isoformat(timespec="seconds"), 1, "network-login"))
    request_refresh("network-login")
    return ok(found=True, new_person=new_person, person=person, persona=p)


@app.post("/api/v1/events/order")
@api_key_required
def api_order_event():
    try:
        res = ingest_order_event(request.business["id"], request.get_json(force=True, silent=True) or {})
    except ValueError as e:
        return err(str(e))
    request_refresh("order-event")
    return ok(res)


@app.get("/api/v1/scores")
@api_key_required
def api_scores():
    with get_conn() as conn:
        rows = [score_row(r) for r in conn.execute(
            """SELECT p.email, p.phone, s.churn_prob, s.risk_tier, s.priority, s.top_reason, s.actions
               FROM scores s JOIN persons p ON p.id=s.person_id WHERE s.business_id=? ORDER BY s.churn_prob DESC LIMIT ?""",
            (request.business["id"], min(int(request.args.get("limit", 100)), 1000)))]
    return ok(rows=rows)


# ---------------------------------------------------------------- agents / pipeline / model
@app.get("/api/agents")
@login_required
def agents():
    with get_conn() as conn:
        runs = [dict(r) for r in conn.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 10")]
        last = runs[0]["run_id"] if runs else None
        logs = [dict(r) for r in conn.execute("SELECT * FROM agent_runs WHERE run_id=? ORDER BY stage, id", (last,))] if last else []
    return ok(agents=[dict(name=n, role=r) for n, r in AGENTS], runs=runs, logs=logs, state=_run_state)


@app.post("/api/pipeline/run")
@login_required
def pipeline_run():
    d = request.get_json(force=True, silent=True) or {}
    started = run_pipeline_async(bid(), "retrain" if d.get("retrain") else "manual", bool(d.get("retrain")))
    if not started:
        return err("A pipeline run is already in progress", 409)
    return ok(started=True)


@app.get("/api/pipeline/status")
@login_required
def pipeline_status():
    with get_conn() as conn:
        run = conn.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        logs = [dict(r) for r in conn.execute("SELECT agent, stage, status, duration_ms, summary FROM agent_runs WHERE run_id=? ORDER BY id",
                                              (run["run_id"],))] if run else []
    return ok(state=_run_state, run=dict(run) if run else None, logs=logs)


@app.get("/api/model")
@login_required
def model_info():
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM model_versions ORDER BY id DESC")]
    for r in rows:
        r["metrics"], r["candidates"], r["feature_importance"] = jload(r["metrics"]), jload(r["candidates"]), jload(r["feature_importance"])
        r.pop("path", None)
    return ok(versions=rows, labels=FEATURE_LABELS)


# ---------------------------------------------------------------- data
@app.get("/api/data/summary")
@login_required
def data_summary():
    b_id = bid()
    with get_conn() as conn:
        c = lambda sql: conn.execute(sql, (b_id,)).fetchone()[0]
        return ok(customers=c("SELECT COUNT(*) FROM relationships WHERE business_id=?"),
                  orders=c("SELECT COUNT(*) FROM orders WHERE business_id=?"),
                  revenue=c("SELECT COALESCE(SUM(amount),0) FROM orders WHERE business_id=?"),
                  visits=c("SELECT COALESCE(SUM(count),0) FROM sessions WHERE business_id=?"),
                  tickets=c("SELECT COUNT(*) FROM tickets WHERE business_id=?"),
                  first_order=c("SELECT MIN(order_date) FROM orders WHERE business_id=?"),
                  last_order=c("SELECT MAX(order_date) FROM orders WHERE business_id=?"),
                  from_network=c("SELECT COUNT(*) FROM relationships WHERE business_id=? AND source='network'"),
                  as_of=str(current_as_of().date()))


@app.post("/api/data/upload")
@login_required
def data_upload():
    kind = request.form.get("type", "orders")
    f = request.files.get("file")
    if not f:
        return err("Choose a CSV file to upload")
    stats = ingest_csv(bid(), kind, f.read())
    if stats["inserted"]:
        request_refresh(f"upload:{kind}", delay=5)
        stats["pipeline_started"] = True
    return ok(stats)


@app.get("/api/data/sample/<kind>")
def data_sample(kind):
    if kind not in SAMPLES:
        abort(404)
    return Response(SAMPLES[kind], mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=sample_{kind}.csv"})


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return err("Not found", 404)
    return send_from_directory(STATIC, "index.html")


def create_app():
    init_db()
    from .db import set_meta
    key = os.environ.get("RETAINIQ_SECRET") or get_meta("secret_key")
    if not key:
        key = secrets.token_hex(24)
        set_meta("secret_key", key)
    app.secret_key = key
    if os.environ.get("RETAINIQ_NO_SCHEDULER") != "1":
        threading.Thread(target=_scheduler, daemon=True).start()
        from . import simulator
        simulator.start(lambda trigger: coordinator.run(None, trigger))
    return app
