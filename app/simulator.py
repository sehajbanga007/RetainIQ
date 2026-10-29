"""Live activity simulator.

Keeps the demo network alive. Every tick (default 2 minutes of real time) the simulated
clock moves forward one day and every active customer behaves like a real shopper would:
they order, open the app, sometimes raise a support ticket. Some customers start to drift
away (activity fades until they stop), some drifting customers come back, and new shoppers
join brands. Customers who received a retention action are more likely to come back.
After each simulated day the agents rescore everyone, so the dashboard changes live.

Disable with RETAINIQ_SIMULATOR=0, change speed with RETAINIQ_TICK_SEC (seconds per day).
"""
import os
import random
import threading
import time
from datetime import date, datetime, timedelta

from .db import get_conn, get_meta
from .data_gen import BUSINESSES, FIRST, LAST, CITIES, TICKET_ISSUES

TICK_SEC = int(os.environ.get("RETAINIQ_TICK_SEC", 120))
P_START_FADING = 0.006      # chance per day that a healthy customer starts drifting away
FADE_RATE = 0.85            # activity multiplier per day while drifting
P_RECOVER = 0.03            # chance per day a drifting customer comes back
P_RECOVER_WITH_ACTION = 0.12
ACTION_EVAL_TICKS = 6       # judge an action's outcome after this many simulated days

_state = {"running": False, "ticks": 0, "last_tick": None}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_state (
    business_id INTEGER, person_id INTEGER, mult REAL DEFAULT 1.0, mode TEXT DEFAULT 'normal',
    PRIMARY KEY (business_id, person_id)
);
"""


def _cats(category):
    for b in BUSINESSES:
        if b["category"] == category:
            return b
    return None


def tick(conn, rng):
    today = date.fromisoformat(get_meta("as_of_date") or date.today().isoformat())
    day = today + timedelta(days=1)
    d90 = (day - timedelta(days=90)).isoformat()
    d30 = (day - timedelta(days=30)).isoformat()
    wk = (day - timedelta(days=day.weekday())).isoformat()
    biz = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM businesses")}

    # recent behaviour per active relationship -> base daily rates
    rows = conn.execute(f"""
        SELECT r.business_id, r.person_id, r.plan,
               (SELECT COUNT(*) FROM orders o WHERE o.business_id=r.business_id AND o.person_id=r.person_id AND o.order_date>?) n90,
               (SELECT AVG(amount) FROM orders o WHERE o.business_id=r.business_id AND o.person_id=r.person_id) aov,
               (SELECT AVG(discount_used) FROM orders o WHERE o.business_id=r.business_id AND o.person_id=r.person_id) disc,
               (SELECT COALESCE(SUM(count),0) FROM sessions s WHERE s.business_id=r.business_id AND s.person_id=r.person_id AND s.week_start>?) s90,
               (SELECT MAX(order_date) FROM orders o WHERE o.business_id=r.business_id AND o.person_id=r.person_id) last_o,
               (SELECT MAX(week_start) FROM sessions s WHERE s.business_id=r.business_id AND s.person_id=r.person_id) last_s,
               r.joined_at
        FROM relationships r""", (d90, d90)).fetchall()
    state = {(r["business_id"], r["person_id"]): (r["mult"], r["mode"]) for r in conn.execute("SELECT * FROM sim_state")}
    helped = {(r["business_id"], r["person_id"]) for r in conn.execute(
        "SELECT business_id, person_id FROM retention_actions WHERE status IN ('Sent','Planned')")}
    top_cat = {(r["business_id"], r["person_id"]): r["category"] for r in conn.execute(
        "SELECT business_id, person_id, category, MAX(c) FROM (SELECT business_id, person_id, category, COUNT(*) c FROM orders "
        "GROUP BY 1,2,3) GROUP BY 1,2")}

    orders, sessions, tickets, upd = [], [], [], []
    stats = dict(orders=0, visits=0, tickets=0, drifting=0, recovered=0, new_customers=0)
    for r in rows:
        key = (r["business_id"], r["person_id"])
        last = max(x for x in [r["last_o"], r["last_s"], r["joined_at"]] if x)
        if last < d30:
            continue  # already churned
        b = biz[r["business_id"]]
        mult, mode = state.get(key, (1.0, "normal"))
        # --- drift dynamics
        if mode == "normal" and rng.random() < P_START_FADING:
            mode = "fading"
            stats["drifting"] += 1
        if mode == "fading":
            mult *= FADE_RATE
            if rng.random() < (P_RECOVER_WITH_ACTION if key in helped else P_RECOVER):
                mode = "recovering"
                stats["recovered"] += 1
            elif mult < 0.04:
                mode, mult = "gone", 0.0
        elif mode == "recovering":
            mult = min(1.0, mult + 0.2)
            if mult >= 1.0:
                mode = "normal"
        upd.append((key[0], key[1], mult, mode))
        if mode == "gone":
            continue

        tenure = max((day - date.fromisoformat(r["joined_at"])).days, 1)
        ord_rate = (r["n90"] or 0) / min(90, tenure)
        sess_rate = (r["s90"] or 0) / min(90, tenure)
        if tenure < 14:  # brand-new customers: assume typical behaviour
            ord_rate, sess_rate = max(ord_rate, 0.05), max(sess_rate, 0.3)
        # orders
        is_sub = b["is_subscription"]
        info = _cats(b["category"])
        if is_sub:
            # subscription renews every 30 days from joining (monthly plans)
            if r["plan"] == "Monthly" and tenure % 30 == 0:
                orders.append((key[0], key[1], day.isoformat(), 1200.0 if info and info["name"] == "FitZone" else 199.0,
                               info["cats"][0] if info else "Subscription", 0, 0))
        elif rng.random() < min(ord_rate * mult, 0.9):
            cat = top_cat.get(key) if rng.random() < 0.6 or not info else rng.choice(info["cats"])
            amt = (r["aov"] or 500) * rng.lognormvariate(0, 0.35)
            disc = int(rng.random() < (r["disc"] or 0.2))
            orders.append((key[0], key[1], day.isoformat(), round(amt * (0.85 if disc else 1), 0), cat or "Other", disc,
                           int(rng.random() < 0.05)))
        # app visits
        n = sum(1 for _ in range(6) if rng.random() < min(sess_rate * mult / 6 * 1.1, 0.95))
        if n:
            sessions.append((key[0], key[1], wk, n))
        # support tickets (more likely while drifting)
        if rng.random() < (0.004 if mode == "normal" else 0.02):
            resolved = int(rng.random() < 0.7)
            tickets.append((key[0], key[1], day.isoformat(), rng.choice(TICKET_ISSUES[is_sub]), resolved,
                            rng.randint(3, 5) if resolved else rng.randint(1, 2)))

    # --- new shoppers join brands
    for bid, b in biz.items():
        info = _cats(b["category"])
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            fn, ln = rng.choice(FIRST), rng.choice(LAST)
            city = rng.choice(list(CITIES))
            email = f"{fn}.{ln}{rng.randint(1000, 99999)}@gmail.com".lower().replace("'", "")
            phone = rng.choice("6789") + "".join(str(rng.randint(0, 9)) for _ in range(9))
            if conn.execute("SELECT 1 FROM persons WHERE email=? OR phone=?", (email, phone)).fetchone():
                continue
            cur = conn.execute("INSERT INTO persons(name, email, phone, city, address, age, created_at) VALUES (?,?,?,?,?,?,?)",
                               (f"{fn} {ln}", email, phone, city,
                                f"Flat {rng.randint(101, 1804)}, {rng.choice(CITIES[city])}, {city}", rng.randint(19, 50),
                                day.isoformat()))
            pid = cur.lastrowid
            plan = ("Monthly" if rng.random() < 0.7 else "Annual") if b["is_subscription"] else "Regular"
            conn.execute("INSERT INTO relationships(business_id, person_id, joined_at, plan, source) VALUES (?,?,?,?,?)",
                         (bid, pid, day.isoformat(), plan, "live"))
            cats = info["cats"] if info else ["Other"]
            aov = info["aov"] if info else 800
            orders.append((bid, pid, day.isoformat(), round(aov * rng.lognormvariate(0, 0.3), 0), rng.choice(cats), 0, 0))
            sessions.append((bid, pid, wk, rng.randint(1, 4)))
            stats["new_customers"] += 1

    conn.executemany("INSERT INTO orders(business_id, person_id, order_date, amount, category, discount_used, returned)"
                     " VALUES (?,?,?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO sessions(business_id, person_id, week_start, count) VALUES (?,?,?,?)", sessions)
    conn.executemany("INSERT INTO tickets(business_id, person_id, created_at, issue, resolved, csat) VALUES (?,?,?,?,?,?)",
                     tickets)
    conn.executemany("INSERT OR REPLACE INTO sim_state(business_id, person_id, mult, mode) VALUES (?,?,?,?)", upd)

    # --- close the feedback loop: judge retention actions after a few simulated days
    cutoff = (datetime.now() - timedelta(seconds=ACTION_EVAL_TICKS * TICK_SEC)).isoformat(timespec="seconds")
    for a in conn.execute("SELECT * FROM retention_actions WHERE status='Sent' AND created_at<?", (cutoff,)).fetchall():
        st = state.get((a["business_id"], a["person_id"]), (1.0, "normal"))[1]
        outcome = "Churned" if st == "gone" else ("Retained" if st in ("normal", "recovering") else None)
        if outcome:
            conn.execute("UPDATE retention_actions SET status=?, note=COALESCE(note,'') || ' (outcome auto-tracked)', updated_at=? "
                         "WHERE id=?", (outcome, datetime.now().isoformat(timespec="seconds"), a["id"]))

    stats.update(orders=len(orders), visits=sum(s[3] for s in sessions), tickets=len(tickets))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('as_of_date', ?)", (day.isoformat(),))
    return day, stats


def _loop(run_agents):
    rng = random.Random()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    _state["running"] = True
    while True:
        time.sleep(TICK_SEC)
        try:
            with get_conn() as conn:
                day, stats = tick(conn, rng)
            _state["ticks"] += 1
            _state["last_tick"] = dict(day=day.isoformat(), **stats)
            print(f"[simulator] {day}: {stats}", flush=True)
            run_agents(f"live:{day.isoformat()}")
        except Exception as e:  # keep the app alive whatever happens
            print("[simulator] error:", e, flush=True)


def start(run_agents):
    if os.environ.get("RETAINIQ_SIMULATOR", "1") == "0" or _state["running"]:
        return
    threading.Thread(target=_loop, args=(run_agents,), daemon=True).start()


def status():
    return dict(_state, tick_seconds=TICK_SEC)
