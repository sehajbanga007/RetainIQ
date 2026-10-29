"""Retention Strategy Agent.

Recommends personalised next-best actions for each customer from (a) the factors that
are pushing their churn risk up, (b) their value and price sensitivity, and (c) their
cross-business persona (interests), producing a ranked action plan.
"""
from .base import Agent

CHANNEL_BY_ENGAGEMENT = {"High": "In-app message", "Medium": "Push notification", "Low": "Email + SMS",
                         "Dormant": "WhatsApp + Email"}


def _discount(clv_rank, price_sens):
    base = 10 if clv_rank < 0.5 else 15
    if price_sens >= 0.5:
        base += 5
    return min(base, 25)


def plan_actions(expl, feats, risk_row, behav, persona, biz):
    """Return a ranked list of actions for one customer."""
    actions = []
    seen = set()
    top_interest = None
    if persona:
        for it in persona.get("interests", []):
            if it.get("business") == biz["name"]:
                top_interest = it["category"]
                break
    disc = _discount(risk_row.get("clv_rank", 0.5), feats.get("discount_rate", 0))
    channel = CHANNEL_BY_ENGAGEMENT.get(behav.get("engagement"), "Email")
    tier = risk_row["risk_tier"]

    def add(key, action, why, offer, impact, ch=None):
        if key in seen:
            return
        seen.add(key)
        actions.append(dict(action=action, why=why, offer=offer, channel=ch or channel, expected_impact=impact))

    for e in [x for x in expl if x["impact"] > 0]:
        f, v = e["feature"], e["value"]
        if f in ("unresolved_tickets_90", "tickets_90") and v <= 0:
            continue
        if f == "avg_csat" and v >= 3.5:
            continue
        if (f == "return_rate" and v < 0.1) or (f == "discount_rate" and v < 0.4) or \
                (f == "tenure_days" and v >= 150) or (f == "network_churned" and v < 1):
            continue
        if f in ("unresolved_tickets_90", "tickets_90", "avg_csat"):
            add("support", "Priority support callback",
                e["reason"], "Resolve open issue within 24h + ₹" + ("200" if biz["price_level"] == 1 else "500") + " goodwill credit",
                "High", "Phone call")
        elif f in ("is_monthly_plan",):
            add("plan", "Offer annual plan upgrade", e["reason"],
                f"Switch to annual at {disc + 10}% off — locks in 12 months", "High")
        elif f in ("recency_days", "orders_90", "orders_30", "order_trend", "spend_trend", "spend_90"):
            what = f"on {top_interest}" if top_interest else "on their next order"
            add("winback", "Personalised win-back offer", e["reason"], f"{disc}% off {what}, valid 7 days", "Medium")
        elif f in ("session_recency_days", "sessions_30", "session_trend"):
            content = f"new arrivals in {top_interest}" if top_interest else "personalised picks"
            add("reengage", "Re-engagement campaign", e["reason"], f"Send curated {content} + reminder of saved items",
                "Medium")
        elif f == "discount_rate":
            add("price", "Loyalty price-lock", e["reason"], "Member price on favourite items for 3 months", "Medium")
        elif f == "return_rate":
            add("returns", "Fit & quality assistance", e["reason"], "Free size-exchange + product advisor chat", "Low")
        elif f == "tenure_days":
            add("onboard", "Onboarding journey", e["reason"], "Welcome series: how-to tips + first-month benefit", "Medium")
        elif f == "network_churned":
            add("loyalty", "Loyalty points boost", e["reason"], "2x loyalty points for the next 60 days", "Medium")
        elif f in ("is_annual_plan", "is_member"):
            add("member", "Invite to loyalty membership", e["reason"], "Free 3-month membership trial", "Medium")

    if tier == "High" and risk_row.get("clv_rank", 0) >= 0.7:
        add("vip", "VIP save desk", "High-value customer at high risk", "Personal account manager reach-out + exclusive perk",
            "High", "Phone call")
    if persona and persona.get("next_interests") and tier != "Low":
        nxt = persona["next_interests"][0]["category"]
        add("cross", "Cross-sell based on network persona", "Persona shows rising interest in " + nxt,
            f"Recommend products related to '{nxt}'", "Low")
    if not actions:
        if tier == "Low":
            add("nurture", "Keep nurturing", "Customer is healthy", "Regular newsletter, early access to sales", "Low",
                "Email")
        else:
            add("checkin", "Personal check-in", "Elevated risk without one dominant factor",
                f"Feedback survey + {disc}% thank-you coupon", "Medium")
    order = {"High": 0, "Medium": 1, "Low": 2}
    actions.sort(key=lambda a: order[a["expected_impact"]])
    return actions[:4]


class RetentionStrategyAgent(Agent):
    name = "Retention Strategy Agent"
    role = "Recommends personalised retention actions, incentives and engagement campaigns"

    def run(self, features, explanations, risk, behavior, personas, businesses):
        plans = []
        risk = risk.copy()
        risk["clv_rank"] = risk.groupby(level="business_id")["clv"].rank(pct=True)
        counts = {}
        frec = features[["discount_rate"]].to_dict("records")
        rrec = risk[["risk_tier", "clv_rank"]].to_dict("records")
        brec = behavior[["engagement"]].to_dict("records")
        for i, idx in enumerate(features.index):
            bid, pid = idx
            feats = frec[i]
            r = rrec[i]
            acts = plan_actions(explanations[i], feats, r, brec[i], personas.get(int(pid)),
                                businesses[int(bid)])
            plans.append(acts)
            if r["risk_tier"] != "Low":
                for a in acts:
                    counts[a["action"]] = counts.get(a["action"], 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        summary = (f"Generated action plans for {len(plans)} customers. Most recommended for at-risk customers: "
                   + ", ".join(f"{k} ({v})" for k, v in top) + ".")
        return plans, summary
