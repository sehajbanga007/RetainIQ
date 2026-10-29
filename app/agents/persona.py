"""Persona Agent (RetainIQ Network).

Builds one unified persona for every person across all businesses in the network:
identity, brands they use, interests, spending band, price sensitivity, loyalty,
churn tendency, which relationships they are likely to leave and what they are
likely to buy next (from network-wide category associations).
"""
from datetime import datetime

import numpy as np
import pandas as pd

from .base import Agent


def category_associations(orders):
    """Lift between product categories across all people in the network."""
    m = pd.crosstab(orders["person_id"], orders["category"]).clip(upper=1)
    n = len(m)
    support = m.mean()
    co = (m.T @ m) / n
    lift = co.div(support, axis=0).div(support, axis=1)
    arr = lift.to_numpy(copy=True)
    np.fill_diagonal(arr, 0)
    lift = pd.DataFrame(arr, index=lift.index, columns=lift.columns)
    return m, lift, support


def _s(v):
    """Missing values (NaN) -> None so the persona is valid JSON."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def archetype(p):
    traits = []
    if p["price_sensitivity"]["score"] >= 0.5:
        traits.append("Deal-seeker")
    if p["spend_band"] in ("High", "Premium"):
        traits.append("High spender")
    if p["brands_left"] >= 1:
        traits.append("Brand switcher")
    if p["n_brands"] >= 3:
        traits.append("Multi-platform")
    if p["return_rate"] >= 0.15:
        traits.append("Frequent returner")
    if p["loyalty_score"] >= 75:
        traits.append("Loyal")
    if p["avg_tenure_days"] < 120:
        traits.append("New to network")
    if p["spend_band"] == "Premium" and p["loyalty_score"] >= 70:
        name = "Premium Loyalist"
    elif p["brands_left"] >= 2 or (p["brands_left"] >= 1 and p["churn_tendency"] >= 40):
        name = "Brand Switcher"
    elif p["price_sensitivity"]["score"] >= 0.55:
        name = "Deal Hunter"
    elif p["n_brands"] >= 3:
        name = "Multi-Platform Explorer"
    elif p["avg_tenure_days"] < 120:
        name = "New Explorer"
    elif p["loyalty_score"] >= 70:
        name = "Steady Regular"
    else:
        name = "Casual Shopper"
    return name, traits


class PersonaAgent(Agent):
    name = "Persona Agent"
    role = "Builds a unified cross-business persona of every person in the RetainIQ network"

    def run(self, raw, features, probs, today):
        today = pd.Timestamp(today)
        orders, rel, persons = raw["orders"], raw["rel"], raw["persons"].set_index("person_id")
        orders = orders.merge(rel[["business_id", "biz_name"]].drop_duplicates(), on="business_id", how="left")

        # interests: recency-weighted spend share per category
        age_days = (today - orders["order_date"]).dt.days.clip(lower=0)
        orders = orders.assign(w=orders["amount"] * np.exp(-age_days / 180))
        aff = orders.groupby(["person_id", "category", "biz_name"])["w"].sum().reset_index()
        aff["share"] = aff["w"] / aff.groupby("person_id")["w"].transform("sum")

        # 'rising' categories: last 90d share vs before
        recent = orders[orders["order_date"] > today - pd.Timedelta(days=90)]
        rec_share = (recent.groupby(["person_id", "category"])["amount"].sum()
                     / recent.groupby("person_id")["amount"].sum()).rename("recent_share")

        owned, lift, support = category_associations(orders)

        spend180 = orders[orders["order_date"] > today - pd.Timedelta(days=180)].groupby("person_id")["amount"].sum()
        q = spend180.quantile([0.4, 0.75, 0.93]).values
        disc = orders.groupby("person_id")["discount_used"].mean()
        ret = orders.groupby("person_id")["returned"].mean()
        lifetime = orders.groupby("person_id")["amount"].sum()
        last_act = orders.groupby(["business_id", "person_id"])["order_date"].max()
        s = raw["sessions"]
        last_sess = s.groupby(["business_id", "person_id"])["week_start"].max()
        sess90 = s[s["week_start"] > today - pd.Timedelta(days=90)].groupby(["person_id", "business_id"])["count"].sum()

        prob_map = probs["churn_prob"].to_dict()
        personas = {}
        rel_by_person = rel.groupby("person_id")
        aff_by_person = {pid: g.sort_values("share", ascending=False) for pid, g in aff.groupby("person_id")}
        for pid, g in rel_by_person:
            if pid not in persons.index:
                continue
            info = persons.loc[pid]
            brands = []
            left = 0
            tenures = []
            for _, r in g.iterrows():
                key = (r["business_id"], pid)
                la = max([x for x in [last_act.get(key), last_sess.get(key)] if x is not None and not pd.isna(x)],
                         default=r["joined_at"])
                p = prob_map.get(key)
                status = "Active" if p is not None else "Left"
                if status == "Left":
                    left += 1
                tenures.append(((la if status == "Left" else today) - r["joined_at"]).days)
                brands.append(dict(business_id=int(r["business_id"]), business=r["biz_name"], category=r["biz_category"],
                                   since=r["joined_at"].date().isoformat(), status=status, plan=r["plan"],
                                   churn_prob=round(float(p), 3) if p is not None else None,
                                   last_active=pd.Timestamp(la).date().isoformat()))
            a = aff_by_person.get(pid)
            interests = []
            if a is not None:
                for _, r in a.head(6).iterrows():
                    rs = rec_share.get((pid, r["category"]), 0.0)
                    interests.append(dict(category=r["category"], business=r["biz_name"],
                                          score=round(float(r["share"]) * 100, 1),
                                          trend="Rising" if rs > r["share"] + 0.1 else (
                                              "Fading" if rs + 0.1 < r["share"] else "Steady")))
            # next interests via association lift
            nxt = []
            if pid in owned.index:
                have = owned.columns[owned.loc[pid].values > 0]
                if len(have):
                    sc = lift.loc[have].clip(lower=1).sub(1)
                    total = sc.sum().drop(labels=list(have), errors="ignore")
                    total = total[total > 0.05].sort_values(ascending=False).head(3)
                    for cat, val in total.items():
                        src = lift.loc[have, cat].idxmax()
                        nxt.append(dict(category=cat, score=round(float(val), 2),
                                        reason=f"People who buy {src} are {lift.loc[src, cat]:.1f}x more likely to buy {cat}"))
            for it in interests:
                if it["trend"] == "Rising" and len(nxt) < 4:
                    nxt.insert(0, dict(category=it["category"], score=1.0, reason=f"Recent spend on {it['category']} is rising"))
                    break

            sp = float(spend180.get(pid, 0))
            band = "Low" if sp <= q[0] else "Mid" if sp <= q[1] else "High" if sp <= q[2] else "Premium"
            ds = float(disc.get(pid, 0))
            active_probs = [b["churn_prob"] for b in brands if b["churn_prob"] is not None]
            churn_tendency = round(float(np.mean(active_probs)) * 100, 1) if active_probs else 100.0
            loyalty = round(100 * (1 - left / len(brands)) * 0.7 + 30 * min(np.mean(tenures) / 365, 1), 1)
            most_active = None
            if pid in sess90.index.get_level_values(0):
                b_id = int(sess90.loc[pid].idxmax())
                most_active = next((b["business"] for b in brands if b["business_id"] == b_id), None)
            p = dict(
                person_id=int(pid), name=_s(info["name"]) or "Unknown", email=_s(info["email"]), phone=_s(info["phone"]),
                city=_s(info["city"]), address=_s(info["address"]), age=int(info["age"]) if not pd.isna(info["age"]) else None,
                n_brands=len(brands), brands_left=left, brands=brands, interests=interests, next_interests=nxt[:3],
                spend_180d=round(sp, 0), lifetime_spend=round(float(lifetime.get(pid, 0)), 0), spend_band=band,
                price_sensitivity=dict(score=round(ds, 2), level="High" if ds >= 0.5 else "Medium" if ds >= 0.25 else "Low"),
                return_rate=round(float(ret.get(pid, 0)), 3), loyalty_score=loyalty, churn_tendency=churn_tendency,
                avg_tenure_days=int(np.mean(tenures)), most_active_on=most_active,
                likely_to_leave=[dict(business=b["business"], category=b["category"], churn_prob=b["churn_prob"])
                                 for b in brands if b["churn_prob"] is not None and b["churn_prob"] >= 0.5],
                updated_at=datetime.now().isoformat(timespec="seconds"),
            )
            p["archetype"], p["traits"] = archetype(p)
            personas[int(pid)] = p
        counts = pd.Series([p["archetype"] for p in personas.values()]).value_counts()
        summary = (f"Built/updated {len(personas)} network personas. Archetypes: "
                   + ", ".join(f"{k} {v}" for k, v in counts.head(4).items()) + ".")
        return personas, summary
