"""Behavior Analysis Agent.

Analyses usage patterns: engagement level, behavioural trend and an RFM-style
customer segment for every customer.
"""
import numpy as np
import pandas as pd

from .base import Agent


def _engagement(row):
    s30, rec = row["sessions_30"], row["session_recency_days"]
    if rec > 21 and row["recency_days"] > 30:
        return "Dormant"
    if s30 >= 12:
        return "High"
    if s30 >= 5:
        return "Medium"
    return "Low"


def _trend(row):
    if row["session_recency_days"] > 21 and row["recency_days"] > 21:
        return "Declining sharply"
    t = 0.6 * row["session_trend"] + 0.25 * row["order_trend"] + 0.15 * row["spend_trend"]
    if t <= -0.45:
        return "Declining sharply"
    if t <= -0.15:
        return "Declining"
    if t >= 0.25:
        return "Growing"
    return "Stable"


class BehaviorAnalysisAgent(Agent):
    name = "Behavior Analysis Agent"
    role = "Analyses usage patterns, tracks engagement levels and finds behavioural trends"

    def run(self, features):
        df = features
        out = pd.DataFrame(index=df.index)
        out["engagement"] = df.apply(_engagement, axis=1)
        out["trend"] = df.apply(_trend, axis=1)

        # RFM segmentation within each business (quantiles are business-specific)
        seg = []
        for bid, g in df.groupby(level="business_id"):
            r = g["recency_days"].rank(pct=True, ascending=False)   # recent = high
            f = g["orders_90"].rank(pct=True)
            m = g["spend_90"].rank(pct=True)
            s = pd.Series(index=g.index, dtype=object)
            for idx in g.index:
                ri, fi, mi, ten = r[idx], f[idx], m[idx], g.at[idx, "tenure_days"]
                if ten <= 60:
                    s[idx] = "New Customer"
                elif ri >= 0.6 and fi >= 0.6 and mi >= 0.6:
                    s[idx] = "Champion"
                elif fi >= 0.5 and ri >= 0.4:
                    s[idx] = "Loyal Regular"
                elif mi >= 0.7 and ri < 0.4:
                    s[idx] = "Big Spender Slipping"
                elif ri < 0.3:
                    s[idx] = "Hibernating"
                else:
                    s[idx] = "Needs Attention"
            seg.append(s)
        out["segment"] = pd.concat(seg) if seg else ""
        declining = int(out["trend"].str.startswith("Declining").sum())
        summary = (f"Profiled {len(out)} customers: "
                   + ", ".join(f"{k} {v}" for k, v in out["engagement"].value_counts().items())
                   + f". {declining} show a declining trend.")
        return out, summary


def monthly_series(raw, business_id, person_id, as_of, months=12):
    """Monthly orders / spend / app visits for one customer (for charts)."""
    as_of = pd.Timestamp(as_of)
    o = raw["orders"]
    o = o[(o["business_id"] == business_id) & (o["person_id"] == person_id)]
    s = raw["sessions"]
    s = s[(s["business_id"] == business_id) & (s["person_id"] == person_id)]
    res = []
    for k in range(months - 1, -1, -1):
        end = as_of - pd.DateOffset(months=k)
        start = end - pd.DateOffset(months=1)
        om = o[(o["order_date"] > start) & (o["order_date"] <= end)]
        sm = s[(s["week_start"] > start) & (s["week_start"] <= end)]
        res.append(dict(month=end.strftime("%b %y"), orders=int(len(om)), spend=float(om["amount"].sum()),
                        visits=int(sm["count"].sum())))
    return res
