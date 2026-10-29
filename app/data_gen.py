"""Synthetic multi-business data generator.

Simulates ~18 months of activity for a network of businesses that share customers.
Each person has hidden traits (price sensitivity, loyalty, 'fickleness', interests)
that drive behaviour consistently across businesses, so the cross-business persona
network contains real signal. Churners show declining engagement before they leave,
more support issues, etc. — the patterns the agents must learn to detect.
"""
import random
import secrets
from datetime import date, timedelta, datetime

import numpy as np
from werkzeug.security import generate_password_hash

from .db import get_conn, init_db, set_meta

BUSINESSES = [
    # name, category, price_level, is_subscription, product categories, base AOV, monthly order rate, weekly sessions, monthly churn hazard
    dict(name="StyleKart", category="Fashion E-commerce", price_level=2, sub=0,
         cats=["Ethnic Wear", "Western Wear", "Footwear", "Accessories", "Sportswear"],
         aov=1600, rate=0.7, sess=2.0, hazard=0.035, email="admin@stylekart.in"),
    dict(name="GadgetHub", category="Electronics", price_level=3, sub=0,
         cats=["Smartphones", "Audio", "Laptops & Tablets", "Wearables", "Mobile Accessories"],
         aov=6500, rate=0.25, sess=1.2, hazard=0.03, email="admin@gadgethub.in"),
    dict(name="FreshBasket", category="Grocery Delivery", price_level=1, sub=0,
         cats=["Fruits & Vegetables", "Dairy & Bakery", "Snacks & Beverages", "Staples", "Personal Care"],
         aov=750, rate=3.0, sess=2.5, hazard=0.04, email="admin@freshbasket.in"),
    dict(name="StreamBox", category="OTT Streaming", price_level=2, sub=1,
         cats=["Subscription", "Movie Rentals", "Live Sports Pass"],
         aov=199, rate=0.0, sess=5.0, hazard=0.045, email="admin@streambox.in"),
    dict(name="QuickBite", category="Food Delivery", price_level=1, sub=0,
         cats=["North Indian", "Chinese", "Pizza & Fast Food", "Desserts", "Healthy Bowls"],
         aov=420, rate=2.8, sess=3.0, hazard=0.045, email="admin@quickbite.in"),
    dict(name="FitZone", category="Fitness", price_level=3, sub=1,
         cats=["Membership", "Personal Training", "Supplements"],
         aov=1200, rate=0.0, sess=3.0, hazard=0.04, email="admin@fitzone.in"),
]

FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan",
         "Kabir", "Ayaan", "Dhruv", "Karan", "Nikhil", "Rahul", "Siddharth", "Yash", "Pratik", "Varun",
         "Ananya", "Diya", "Aadhya", "Saanvi", "Pari", "Myra", "Anika", "Navya", "Kiara", "Riya",
         "Isha", "Sneha", "Pooja", "Neha", "Priya", "Tanvi", "Meera", "Simran", "Kavya", "Aditi",
         "Harpreet", "Gurleen", "Manpreet", "Arnav", "Om", "Parth", "Zara", "Sana", "Imran", "Farhan"]
LAST = ["Sharma", "Verma", "Patel", "Shah", "Mehta", "Singh", "Kapoor", "Gupta", "Reddy", "Nair",
        "Iyer", "Joshi", "Desai", "Kulkarni", "Banerjee", "Chatterjee", "Malhotra", "Bhatia", "Chopra", "Jain",
        "Agarwal", "Saxena", "Pillai", "Menon", "Rao", "Khan", "Sheikh", "Fernandes", "D'Souza", "Bherwani",
        "Bohra", "Banga", "Thakur", "Pandey", "Mishra", "Sinha", "Ghosh", "Bose", "Arora", "Sethi"]
CITIES = {
    "Mumbai": ["Andheri West", "Bandra West", "Powai", "Malad East", "Borivali West", "Dadar", "Chembur"],
    "Thane": ["Ghodbunder Road", "Majiwada", "Naupada", "Kolshet Road"],
    "Pune": ["Kothrud", "Baner", "Viman Nagar", "Hinjewadi"],
    "Delhi": ["Saket", "Dwarka", "Rajouri Garden", "Lajpat Nagar"],
    "Bengaluru": ["Indiranagar", "Koramangala", "Whitefield", "HSR Layout"],
    "Hyderabad": ["Gachibowli", "Banjara Hills", "Kondapur"],
    "Ahmedabad": ["Satellite", "Navrangpura", "Bopal"],
}
TICKET_ISSUES = {
    0: ["Late delivery", "Wrong item received", "Refund not processed", "Damaged product", "Payment failed", "App crash"],
    1: ["Payment failed", "Streaming / app issue", "Billing dispute", "Cancellation request", "Account access issue"],
}


def _iso(d):
    return d.isoformat()


def generate(n_persons=2500, seed=42, today=None):
    rng = np.random.default_rng(seed)
    random.seed(seed)
    today = today or date.today()
    start = today - timedelta(days=540)

    init_db()
    with get_conn() as conn:
        for t in ["agent_runs", "pipeline_runs", "model_versions", "retention_actions", "personas", "score_history",
                  "scores", "tickets", "sessions", "orders", "relationships", "persons", "businesses", "network_lookups",
                  "insights"]:
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM sqlite_sequence")

        # ---- businesses
        biz_ids = []
        for b in BUSINESSES:
            cur = conn.execute(
                "INSERT INTO businesses(name, category, price_level, is_subscription, email, password_hash, api_key, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (b["name"], b["category"], b["price_level"], b["sub"], b["email"],
                 generate_password_hash("demo123"), "riq_live_" + secrets.token_hex(12), _iso(start)))
            biz_ids.append(cur.lastrowid)

        # ---- persons with hidden traits
        persons = []
        used_emails, used_phones = set(), set()
        for i in range(n_persons):
            fn, ln = random.choice(FIRST), random.choice(LAST)
            base = f"{fn}.{ln}".lower().replace("'", "")
            email = f"{base}{random.randint(1, 999)}@{random.choice(['gmail.com', 'yahoo.in', 'outlook.com'])}"
            while email in used_emails:
                email = f"{base}{random.randint(1, 9999)}@gmail.com"
            used_emails.add(email)
            phone = "9" + "".join(str(random.randint(0, 9)) for _ in range(9))
            while phone in used_phones:
                phone = "8" + "".join(str(random.randint(0, 9)) for _ in range(9))
            used_phones.add(phone)
            city = random.choices(list(CITIES), weights=[30, 12, 12, 14, 14, 9, 9])[0]
            area = random.choice(CITIES[city])
            address = f"Flat {random.randint(101, 1804)}, {random.choice(['Sai', 'Shanti', 'Lotus', 'Green', 'Royal', 'Sunrise', 'Orchid'])} " \
                      f"{random.choice(['Residency', 'Heights', 'Apartments', 'Towers', 'Enclave'])}, {area}, {city}"
            age = int(np.clip(rng.normal(30, 8), 18, 65))
            traits = dict(
                price_sens=float(rng.beta(2, 2)),
                loyalty=float(rng.beta(2.5, 2)),
                fickle=float(rng.beta(1.5, 4)),          # tendency to switch brands across the network
                patience=float(rng.beta(3, 2)),
                affluence=float(rng.lognormal(0, 0.35)),
                engagement=float(rng.lognormal(0, 0.3)),
            )
            created = start + timedelta(days=int(rng.integers(0, 120)))
            cur = conn.execute(
                "INSERT INTO persons(name, email, phone, city, address, age, created_at) VALUES (?,?,?,?,?,?,?)",
                (f"{fn} {ln}", email, phone, city, address, age, _iso(created)))
            persons.append((cur.lastrowid, traits, age))

        # ---- relationships + activity
        orders, sessions, tickets, rels = [], [], [], []
        biz_weights = np.array([0.55, 0.35, 0.5, 0.5, 0.6, 0.25])
        for pid, tr, age in persons:
            k = int(rng.choice([1, 2, 3, 4], p=[0.3, 0.35, 0.23, 0.12]))
            w = biz_weights.copy()
            if age > 40:
                w[[3, 5]] *= 0.6
            if tr["affluence"] > 1.2:
                w[[1, 5]] *= 1.6
            chosen = rng.choice(len(BUSINESSES), size=k, replace=False, p=w / w.sum())
            for bi in chosen:
                b = BUSINESSES[bi]
                bid = biz_ids[bi]
                joined = start + timedelta(days=int(rng.integers(0, 540 - 25)))
                if b["sub"]:
                    plan = "Annual" if rng.random() < 0.2 + 0.35 * tr["loyalty"] else "Monthly"
                else:
                    plan = "Member" if rng.random() < 0.15 + 0.3 * tr["loyalty"] * tr["affluence"] else "Regular"
                rels.append((bid, pid, _iso(joined), plan))
                interest = rng.dirichlet(np.ones(len(b["cats"])) * 0.8)
                eng = tr["engagement"] * rng.lognormal(0, 0.25)

                # pass 1: monthly loop -> tickets & churn time
                dissat = 0.0
                churn_day = None
                m_start = joined
                rel_tickets = []
                sudden = rng.random() < 0.3
                month_noise = rng.lognormal(0, 0.5, size=40)  # some churners leave without warning
                while m_start < today + timedelta(days=200):
                    t_rate = 0.06 + 0.10 * (1 - tr["patience"])
                    if rng.random() < t_rate:
                        tdate = m_start + timedelta(days=int(rng.integers(0, 30)))
                        if tdate <= today:
                            resolved = rng.random() < 0.72
                            csat = int(np.clip(rng.normal(4.2 if resolved else 2.0, 0.8), 1, 5))
                            rel_tickets.append((tdate, random.choice(TICKET_ISSUES[b["sub"]]), int(resolved), csat))
                            dissat += (0.35 if resolved else 1.1) * (1.2 - tr["patience"])
                    dissat *= 0.8
                    price_pain = tr["price_sens"] * (b["price_level"] - 1) / 2
                    plan_eff = 0.55 if plan == "Monthly" else (-0.9 if plan == "Annual" else (-0.45 if plan == "Member" else 0))
                    logit = (np.log(b["hazard"] / (1 - b["hazard"])) + 1.3 * price_pain + 0.9 * dissat
                             - 1.4 * (tr["loyalty"] - 0.5) + 2.2 * (tr["fickle"] - 0.25) + plan_eff)
                    tenure_m = (m_start - joined).days / 30
                    if tenure_m < 3:
                        logit += 0.5  # new customers are fragile
                    h = 1 / (1 + np.exp(-logit))
                    if rng.random() < h:
                        churn_day = m_start + timedelta(days=int(rng.integers(0, 30)))
                        break
                    m_start += timedelta(days=30)
                if churn_day is None:
                    churn_day = today + timedelta(days=400)
                for (tdate, issue, res, csat) in rel_tickets:
                    if tdate < churn_day:
                        tickets.append((bid, pid, _iso(tdate), issue, res, csat))

                def mult(d):
                    if d >= churn_day:
                        return 0.0
                    noise = month_noise[min(max((d - joined).days // 30, 0), 39)]
                    left = (churn_day - d).days
                    if not sudden and left < 100:
                        return noise * (0.3 + 0.7 * (left / 100) ** 1.2)
                    return noise

                # pass 2: orders
                end = min(today, churn_day)
                if b["sub"]:
                    d = joined
                    step = 365 if plan == "Annual" else 30
                    if bid == biz_ids[3]:
                        price = 1499 if plan == "Annual" else 199
                    else:
                        price = 9999 if plan == "Annual" else 1200
                    while d <= end:
                        orders.append((bid, pid, _iso(d), float(price), b["cats"][0], 0, 0))
                        d += timedelta(days=step)
                    # add-ons
                    n_add = rng.poisson(0.25 * eng * max((end - joined).days, 0) / 30)
                    for _ in range(n_add):
                        od = joined + timedelta(days=int(rng.integers(0, max((end - joined).days, 1))))
                        if rng.random() > mult(od):
                            continue
                        ci = int(rng.choice(range(1, len(b["cats"])), p=interest[1:] / interest[1:].sum()))
                        amt = float(b["aov"] * (0.6 if bid == biz_ids[3] else 1.8) * tr["affluence"] * rng.lognormal(0, 0.3))
                        orders.append((bid, pid, _iso(od), round(amt, 0), b["cats"][ci],
                                       int(rng.random() < tr["price_sens"] * 0.6), 0))
                else:
                    d = joined
                    while d <= end:
                        lam = b["rate"] * eng * mult(d) * (1.3 if plan == "Member" else 1.0) * (0.7 + 0.6 * tr["loyalty"])
                        n = rng.poisson(lam)
                        for _ in range(n):
                            od = d + timedelta(days=int(rng.integers(0, 30)))
                            if od > end:
                                continue
                            ci = int(rng.choice(len(b["cats"]), p=interest))
                            disc = int(rng.random() < 0.1 + 0.7 * tr["price_sens"])
                            amt = b["aov"] * tr["affluence"] * rng.lognormal(0, 0.45) * (0.85 if disc else 1)
                            ret_p = {0: 0.14, 1: 0.05}.get(bi, 0.02) * (1.5 if tr["patience"] < 0.4 else 1)
                            orders.append((bid, pid, _iso(od), round(float(amt), 0), b["cats"][ci], disc,
                                           int(rng.random() < ret_p)))
                        d += timedelta(days=30)
                    # make sure every customer has a first order
                    orders.append((bid, pid, _iso(joined), round(float(b["aov"] * tr["affluence"]), 0),
                                   b["cats"][int(np.argmax(interest))], 0, 0))

                # pass 3: weekly sessions
                wk = joined - timedelta(days=joined.weekday())
                while wk <= end:
                    lam = b["sess"] * eng * mult(wk)
                    c = rng.poisson(lam)
                    if c > 0:
                        sessions.append((bid, pid, _iso(wk), int(c)))
                    wk += timedelta(days=7)

        conn.executemany("INSERT INTO relationships(business_id, person_id, joined_at, plan) VALUES (?,?,?,?)", rels)
        conn.executemany("INSERT INTO orders(business_id, person_id, order_date, amount, category, discount_used, returned)"
                         " VALUES (?,?,?,?,?,?,?)", orders)
        conn.executemany("INSERT INTO sessions(business_id, person_id, week_start, count) VALUES (?,?,?,?)", sessions)
        conn.executemany("INSERT INTO tickets(business_id, person_id, created_at, issue, resolved, csat) VALUES (?,?,?,?,?,?)",
                         tickets)
    set_meta("as_of_date", _iso(today))
    set_meta("seeded_at", datetime.now().isoformat(timespec="seconds"))
    return dict(persons=len(persons), relationships=len(rels), orders=len(orders), sessions=len(sessions),
                tickets=len(tickets))


if __name__ == "__main__":
    print(generate())
