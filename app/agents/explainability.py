"""Explainability Agent.

Explains each individual prediction: how much every feature pushed that customer's
churn probability up or down. For tree models it uses exact path attribution
(Saabas / TreeInterpreter method: each split's change in the tree's output is credited
to the split feature); for logistic regression it uses coefficient x (value - mean).
Contributions are reported in percentage points of churn probability and they add up
to (customer's probability - average probability).
"""
import numpy as np
from scipy import sparse

from .base import Agent
from .preprocessing import FEATURES, FEATURE_LABELS


def _fmt_money(v):
    return f"₹{v:,.0f}"


def reason_text(f, v, up):
    """Human-readable explanation of one feature's effect."""
    if f == "recency_days":
        return f"No purchase in the last {v:.0f} days" if up else f"Purchased recently ({v:.0f} days ago)"
    if f == "session_recency_days":
        return f"Hasn't opened the app in {v:.0f} days" if up else f"Visited the app {v:.0f} days ago"
    if f == "sessions_30":
        return f"Only {v:.0f} app visits in the last 30 days" if up else f"Active user — {v:.0f} app visits this month"
    if f == "session_trend":
        return f"App usage down {abs(v)*100:.0f}% vs earlier" if (up and v < 0) else (
            "App usage is falling" if up else f"App usage {'up' if v >= 0 else 'steady'}")
    if f == "orders_30":
        return f"{v:.0f} orders in last 30 days" + (" — below usual" if up else "")
    if f == "orders_90":
        return f"Only {v:.0f} orders in last 90 days" if up else f"{v:.0f} orders in last 90 days"
    if f == "order_trend":
        return f"Orders down {abs(v)*100:.0f}% vs previous quarter" if (up and v < 0) else (
            "Order frequency changing" if up else "Order frequency stable or growing")
    if f == "spend_trend":
        return f"Spending down {abs(v)*100:.0f}% vs previous quarter" if (up and v < 0) else (
            "Spending pattern shifting" if up else "Spending stable or growing")
    if f == "spend_90":
        return f"Spent {_fmt_money(v)} in last 90 days"
    if f == "avg_order_value":
        return f"Average order {_fmt_money(v)}"
    if f == "tickets_90":
        return f"Raised {v:.0f} support ticket(s) recently" if v > 0 else "No recent support issues"
    if f == "unresolved_tickets_90":
        return f"{v:.0f} unresolved complaint(s)" if v > 0 else "No unresolved complaints"
    if f == "avg_csat":
        return f"Low support satisfaction ({v:.1f}/5)" if (up and v < 3.5) else f"Good support satisfaction ({v:.1f}/5)"
    if f == "discount_rate":
        return f"Price-sensitive — {v*100:.0f}% of orders used discounts" if (up and v >= 0.4) else f"{v*100:.0f}% of orders used discounts"
    if f == "return_rate":
        return f"High return rate ({v*100:.0f}%)" if (up and v >= 0.1) else f"Return rate {v*100:.0f}%"
    if f == "is_monthly_plan":
        return "On a month-to-month plan (easy to cancel)" if v else "Not on a monthly plan"
    if f == "is_annual_plan":
        return "Locked in on an annual plan" if v else "Not on an annual plan"
    if f == "is_member":
        return "Loyalty member" if v else "Not a loyalty member"
    if f == "tenure_days":
        return f"New customer ({v:.0f} days)" if up and v < 120 else f"Customer for {v:.0f} days"
    if f == "network_churned":
        return f"Has left {v:.0f} other brand(s) in the network" if v > 0 else "No history of switching brands"
    if f == "network_relationships":
        return f"Active with {v:.0f} other brand(s) in the network"
    return FEATURE_LABELS.get(f, f)


def _tree_contrib(tree, X, positive_class=None):
    t = tree.tree_
    n_nodes = t.node_count
    if positive_class is None:
        vals = t.value[:, 0, 0]
    else:
        v = t.value[:, 0, :]
        vals = v[:, positive_class] / np.maximum(v.sum(axis=1), 1e-12)
    parent = np.full(n_nodes, -1)
    for i in range(n_nodes):
        for c in (t.children_left[i], t.children_right[i]):
            if c != -1:
                parent[c] = i
    rows, cols, data = [], [], []
    for node in range(1, n_nodes):
        p = parent[node]
        if p >= 0:
            rows.append(node)
            cols.append(t.feature[p])
            data.append(vals[node] - vals[p])
    M = sparse.csr_matrix((data, (rows, cols)), shape=(n_nodes, X.shape[1]))
    path = tree.decision_path(X)
    return np.asarray((path @ M).todense()), vals[0]


def contributions(bundle, X):
    """Return (contribution matrix in probability points, base probability)."""
    model, algo = bundle["model"], bundle["algorithm"]
    p = model.predict_proba(X)[:, 1]
    if algo == "Gradient Boosting":
        lr = model.learning_rate
        C = np.zeros_like(X, dtype=float)
        for est in model.estimators_[:, 0]:
            c, _ = _tree_contrib(est, X)
            C += lr * c
        raw = model.decision_function(X)
        bias = raw - C.sum(axis=1)
        base_logit = float(np.median(bias))
        base_p = 1 / (1 + np.exp(-base_logit))
        # rescale logit contributions so they sum to (p - base_p)
        tot = C.sum(axis=1)
        C = C * np.where(np.abs(tot) > 1e-9, (p - base_p) / np.where(np.abs(tot) > 1e-9, tot, 1), 0)[:, None]
        return C, base_p
    if algo == "Random Forest":
        C = np.zeros_like(X, dtype=float)
        base = 0.0
        for est in model.estimators_:
            c, b0 = _tree_contrib(est, X, positive_class=1)
            C += c
            base += b0
        n = len(model.estimators_)
        C /= n
        base_p = base / n
        tot = C.sum(axis=1)
        C = C * np.where(np.abs(tot) > 1e-9, (p - base_p) / np.where(np.abs(tot) > 1e-9, tot, 1), 0)[:, None]
        return C, base_p
    # logistic regression
    scaler, lr = model[0], model[-1]
    Z = scaler.transform(X)
    C = Z * lr.coef_[0]
    base_logit = float(lr.intercept_[0])
    base_p = 1 / (1 + np.exp(-base_logit))
    tot = C.sum(axis=1)
    C = C * np.where(np.abs(tot) > 1e-9, (p - base_p) / np.where(np.abs(tot) > 1e-9, tot, 1), 0)[:, None]
    return C, base_p


HIDDEN = {"cat_fashion", "cat_electronics", "cat_grocery", "cat_ott", "cat_food", "cat_fitness",
          "network_relationships"}


class ExplainabilityAgent(Agent):
    name = "Explainability Agent"
    role = "Explains each individual prediction and highlights the top risk factors"

    def run(self, features, bundle):
        X = features[FEATURES].values
        C, base_p = contributions(bundle, X)
        explanations = []
        for i in range(len(X)):
            items = []
            for j, f in enumerate(FEATURES):
                if f in HIDDEN:
                    continue
                c = float(C[i, j])
                if abs(c) < 0.002:
                    continue
                v = float(X[i, j])
                items.append(dict(feature=f, label=FEATURE_LABELS[f], value=round(v, 3), impact=round(c * 100, 2),
                                  reason=reason_text(f, v, c > 0)))
            items.sort(key=lambda d: -abs(d["impact"]))
            explanations.append(items[:8])
        n_with = sum(1 for e in explanations if any(x["impact"] > 0 for x in e))
        summary = (f"Explained {len(X)} predictions (method: {'tree path attribution' if bundle['algorithm'] != 'Logistic Regression' else 'linear attribution'}). "
                   f"Baseline churn probability {base_p:.1%}; {n_with} customers have at least one risk-raising factor.")
        return dict(explanations=explanations, contrib=C, base_p=base_p), summary
