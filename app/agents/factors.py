"""Factor Identification Agent.

Finds the key reasons for churn across the customer base: global feature importance
(mean absolute contribution) and data-backed churn-rate lifts for common patterns,
computed per business and network-wide.
"""
import numpy as np
import pandas as pd

from .base import Agent
from .explainability import HIDDEN
from .preprocessing import FEATURES, FEATURE_LABELS, build_features

PATTERNS = [
    ("Has unresolved complaints", lambda d: d["unresolved_tickets_90"] > 0),
    ("On a monthly plan", lambda d: d["is_monthly_plan"] == 1),
    ("App usage falling 40%+", lambda d: d["session_trend"] <= -0.4),
    ("No purchase in 45+ days", lambda d: d["recency_days"] >= 45),
    ("Uses discounts on most orders", lambda d: d["discount_rate"] >= 0.5),
    ("Left another brand in the network", lambda d: d["network_churned"] >= 1),
    ("New customer (< 90 days)", lambda d: d["tenure_days"] < 90),
    ("Low support satisfaction (< 3/5)", lambda d: d["avg_csat"] < 3),
]


def pattern_lifts(labelled):
    rows = []
    base = labelled["churned"].mean()
    for name, fn in PATTERNS:
        m = fn(labelled)
        n = int(m.sum())
        if n < 15 or (~m).sum() < 15:
            continue
        r_in, r_out = labelled.loc[m, "churned"].mean(), labelled.loc[~m, "churned"].mean()
        rows.append(dict(pattern=name, customers=n, churn_rate=round(float(r_in), 4),
                         others_rate=round(float(r_out), 4), lift=round(float(r_in / max(r_out, 1e-3)), 2)))
    rows.sort(key=lambda d: -d["lift"])
    return dict(base_rate=round(float(base), 4), patterns=rows)


class FactorIdentificationAgent(Agent):
    name = "Factor Identification Agent"
    role = "Finds the key reasons for churn, analyses feature importance and identifies common patterns"

    def run(self, features, contrib, raw, today):
        imp_cols = [i for i, f in enumerate(FEATURES) if f not in HIDDEN]
        result = {}
        labelled = build_features(raw, pd.Timestamp(today) - pd.Timedelta(days=90), label_horizon=90)

        def importance(mask):
            C = np.abs(contrib[mask][:, imp_cols])
            if len(C) == 0:
                return []
            m = C.mean(axis=0)
            m = m / m.sum() if m.sum() > 0 else m
            items = [dict(feature=FEATURES[j], label=FEATURE_LABELS[FEATURES[j]], importance=round(float(v), 4))
                     for j, v in zip(imp_cols, m)]
            return sorted(items, key=lambda d: -d["importance"])[:10]

        bids = features.index.get_level_values("business_id").values
        result["all"] = dict(importance=importance(np.ones(len(bids), bool)), **pattern_lifts(labelled))
        for bid in np.unique(bids):
            lab_b = labelled[labelled.index.get_level_values("business_id") == bid]
            result[int(bid)] = dict(importance=importance(bids == bid), **pattern_lifts(lab_b))
        top = result["all"]["importance"][:3]
        pats = result["all"]["patterns"][:2]
        summary = ("Top churn drivers network-wide: " + ", ".join(t["label"] for t in top) + ". "
                   + " ".join(f"'{p['pattern']}' customers churn {p['lift']}x more." for p in pats))
        return result, summary
