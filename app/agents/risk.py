"""Risk Assessment Agent.

Turns churn probability into business risk: risk tier, estimated customer value
(next 12 months), revenue at risk, and an action priority that combines both.
"""
import numpy as np
import pandas as pd

from .base import Agent


class RiskAssessmentAgent(Agent):
    name = "Risk Assessment Agent"
    role = "Evaluates churn likelihood against customer value to prioritise who to save first"

    def run(self, features, probs):
        df = pd.DataFrame(index=features.index)
        p = probs["churn_prob"].values
        df["churn_prob"] = p
        df["risk_tier"] = np.select([p >= 0.5, p >= 0.2], ["High", "Medium"], "Low")
        # 12-month value estimate: recent quarterly spend annualised, blended with lifetime run-rate
        tenure_m = np.maximum(features["tenure_days"].values / 30, 1)
        run_rate = features["lifetime_spend"].values / tenure_m * 12
        clv = 0.6 * features["spend_90"].values * 4 + 0.4 * run_rate
        df["clv"] = np.round(clv, 0)
        df["revenue_at_risk"] = np.round(clv * p, 0)
        pr = []
        for bid, g in df.groupby(level="business_id"):
            hi_val = g["clv"] >= g["clv"].quantile(0.7)
            pri = np.select(
                [(g["risk_tier"] == "High") & hi_val, g["risk_tier"] == "High",
                 (g["risk_tier"] == "Medium") & hi_val, g["risk_tier"] == "Medium"],
                ["P1 - Critical", "P2 - High", "P2 - High", "P3 - Monitor"], "P4 - Healthy")
            pr.append(pd.Series(pri, index=g.index))
        df["priority"] = pd.concat(pr)
        summary = (f"High risk: {int((df.risk_tier == 'High').sum())}, Medium: {int((df.risk_tier == 'Medium').sum())}, "
                   f"Low: {int((df.risk_tier == 'Low').sum())}. {int((df.priority == 'P1 - Critical').sum())} critical "
                   f"(high-value & high-risk). Total revenue at risk ₹{df['revenue_at_risk'].sum():,.0f}.")
        return df, summary
