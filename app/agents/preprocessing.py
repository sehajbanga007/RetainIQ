"""Data Preprocessing Agent.

Loads raw customer data (relationships, orders, app sessions, support tickets) from the
database, cleans it (types, missing values, invalid rows), and engineers model features
for every (business, customer) relationship as of a given date. It can also attach a
churn label for a future window, which is how training data is created.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .base import Agent
from ..db import connect

FEATURES = [
    "tenure_days", "recency_days", "session_recency_days",
    "orders_30", "orders_90", "order_trend", "spend_90", "spend_trend", "avg_order_value",
    "sessions_30", "session_trend",
    "tickets_90", "unresolved_tickets_90", "avg_csat",
    "discount_rate", "return_rate",
    "is_monthly_plan", "is_annual_plan", "is_member",
    "network_relationships", "network_churned",
    "cat_fashion", "cat_electronics", "cat_grocery", "cat_ott", "cat_food", "cat_fitness",
]

FEATURE_LABELS = {
    "tenure_days": "Customer tenure (days)",
    "recency_days": "Days since last purchase",
    "session_recency_days": "Days since last app visit",
    "orders_30": "Orders in last 30 days",
    "orders_90": "Orders in last 90 days",
    "order_trend": "Order trend (last 90d vs previous 90d)",
    "spend_90": "Spend in last 90 days (₹)",
    "spend_trend": "Spend trend (last 90d vs previous 90d)",
    "avg_order_value": "Average order value (₹)",
    "sessions_30": "App visits in last 30 days",
    "session_trend": "App engagement trend",
    "tickets_90": "Support tickets (90d)",
    "unresolved_tickets_90": "Unresolved complaints (90d)",
    "avg_csat": "Support satisfaction (1-5)",
    "discount_rate": "Share of orders using discounts",
    "return_rate": "Return rate",
    "is_monthly_plan": "On monthly plan",
    "is_annual_plan": "On annual plan",
    "is_member": "Loyalty member",
    "network_relationships": "Other brands in network",
    "network_churned": "Brands left elsewhere in network",
    "cat_fashion": "Business: Fashion", "cat_electronics": "Business: Electronics",
    "cat_grocery": "Business: Grocery", "cat_ott": "Business: OTT",
    "cat_food": "Business: Food delivery", "cat_fitness": "Business: Fitness",
}

CAT_MAP = {
    "Fashion E-commerce": "cat_fashion", "Electronics": "cat_electronics", "Grocery Delivery": "cat_grocery",
    "OTT Streaming": "cat_ott", "Food Delivery": "cat_food", "Fitness": "cat_fitness",
}


def load_raw():
    """Load and clean all raw tables into pandas DataFrames."""
    conn = connect()
    try:
        rel = pd.read_sql("SELECT r.*, b.category AS biz_category, b.name AS biz_name FROM relationships r "
                          "JOIN businesses b ON b.id=r.business_id", conn)
        orders = pd.read_sql("SELECT business_id, person_id, order_date, amount, category, discount_used, returned FROM orders", conn)
        sess = pd.read_sql("SELECT business_id, person_id, week_start, count FROM sessions", conn)
        tick = pd.read_sql("SELECT business_id, person_id, created_at, resolved, csat FROM tickets", conn)
        persons = pd.read_sql("SELECT id AS person_id, name, email, phone, city, address, age FROM persons", conn)
    finally:
        conn.close()

    # --- cleaning
    rel["joined_at"] = pd.to_datetime(rel["joined_at"], errors="coerce")
    rel = rel.dropna(subset=["joined_at"])
    rel["plan"] = rel["plan"].fillna("Regular")

    orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")
    orders["amount"] = pd.to_numeric(orders["amount"], errors="coerce")
    orders = orders.dropna(subset=["order_date", "amount"])
    orders = orders[orders["amount"] > 0]
    orders["discount_used"] = orders["discount_used"].fillna(0).astype(int)
    orders["returned"] = orders["returned"].fillna(0).astype(int)
    orders["category"] = orders["category"].fillna("Other")

    sess["week_start"] = pd.to_datetime(sess["week_start"], errors="coerce")
    sess = sess.dropna(subset=["week_start"])

    tick["created_at"] = pd.to_datetime(tick["created_at"], errors="coerce")
    tick = tick.dropna(subset=["created_at"])
    tick["csat"] = pd.to_numeric(tick["csat"], errors="coerce")
    return dict(rel=rel, orders=orders, sessions=sess, tickets=tick, persons=persons)


def _agg(df, mask, key, col, fn, name):
    return df[mask].groupby(key)[col].agg(fn).rename(name)


def build_features(raw, as_of, label_horizon=None):
    """Feature matrix for every relationship active at `as_of`.

    Active = joined before as_of and had an order/app visit in the 30 days before it
    (or joined in the last 30 days). If label_horizon is given, adds `churned` = no
    activity at all in (as_of, as_of + horizon].
    """
    as_of = pd.Timestamp(as_of)
    key = ["business_id", "person_id"]
    rel = raw["rel"][raw["rel"]["joined_at"] <= as_of].copy()
    o = raw["orders"][raw["orders"]["order_date"] <= as_of]
    s = raw["sessions"][raw["sessions"]["week_start"] <= as_of]
    t = raw["tickets"][raw["tickets"]["created_at"] <= as_of]

    d30, d60, d90, d180 = (as_of - pd.Timedelta(days=x) for x in (30, 60, 90, 180))

    feats = [
        o.groupby(key)["order_date"].max().rename("last_order"),
        o.groupby(key)["amount"].mean().rename("avg_order_value"),
        o.groupby(key)["discount_used"].mean().rename("discount_rate"),
        o.groupby(key)["returned"].mean().rename("return_rate"),
        o.groupby(key)["amount"].sum().rename("lifetime_spend"),
        o.groupby(key).size().rename("lifetime_orders"),
        _agg(o, o["order_date"] > d30, key, "amount", "size", "orders_30"),
        _agg(o, o["order_date"] > d90, key, "amount", "size", "orders_90"),
        _agg(o, (o["order_date"] > d180) & (o["order_date"] <= d90), key, "amount", "size", "orders_prev90"),
        _agg(o, o["order_date"] > d90, key, "amount", "sum", "spend_90"),
        _agg(o, (o["order_date"] > d180) & (o["order_date"] <= d90), key, "amount", "sum", "spend_prev90"),
        s.groupby(key)["week_start"].max().rename("last_session"),
        _agg(s, s["week_start"] > d30, key, "count", "sum", "sessions_30"),
        _agg(s, (s["week_start"] > d90) & (s["week_start"] <= d30), key, "count", "sum", "sessions_prev60"),
        _agg(t, t["created_at"] > d90, key, "resolved", "size", "tickets_90"),
        _agg(t, (t["created_at"] > d90) & (t["resolved"] == 0), key, "resolved", "size", "unresolved_tickets_90"),
        _agg(t, t["created_at"] > d180, key, "csat", "mean", "avg_csat"),
    ]
    df = rel.set_index(key).join(feats, how="left")

    count_cols = ["orders_30", "orders_90", "orders_prev90", "spend_90", "spend_prev90", "sessions_30",
                  "sessions_prev60", "tickets_90", "unresolved_tickets_90", "lifetime_orders", "lifetime_spend"]
    df[count_cols] = df[count_cols].fillna(0)
    df["avg_csat"] = df["avg_csat"].fillna(4.0)  # no tickets -> assume satisfied
    for c in ["avg_order_value", "discount_rate", "return_rate"]:
        df[c] = df[c].fillna(df[c].median() if df[c].notna().any() else 0)

    last_activity = pd.concat([df["last_order"], df["last_session"] + pd.Timedelta(days=6)], axis=1).max(axis=1)
    last_activity = last_activity.fillna(df["joined_at"])
    df["last_activity"] = last_activity
    df["tenure_days"] = (as_of - df["joined_at"]).dt.days
    df["recency_days"] = (as_of - df["last_order"].fillna(df["joined_at"])).dt.days.clip(lower=0)
    df["session_recency_days"] = (as_of - df["last_session"].fillna(df["joined_at"])).dt.days.clip(lower=0)
    df["order_trend"] = ((df["orders_90"] - df["orders_prev90"]) / (df["orders_prev90"] + 1)).clip(-1, 3)
    df["spend_trend"] = ((df["spend_90"] - df["spend_prev90"]) / (df["spend_prev90"] + 500)).clip(-1, 3)
    df["session_trend"] = ((df["sessions_30"] - df["sessions_prev60"] / 2) / (df["sessions_prev60"] / 2 + 1)).clip(-1, 3)
    df["is_monthly_plan"] = (df["plan"] == "Monthly").astype(int)
    df["is_annual_plan"] = (df["plan"] == "Annual").astype(int)
    df["is_member"] = (df["plan"] == "Member").astype(int)
    for cat, col in CAT_MAP.items():
        df[col] = (df["biz_category"] == cat).astype(int)

    # ---- network (cross-business) features
    gone = (as_of - df["last_activity"]).dt.days > 30
    df["_gone"] = gone.astype(int)
    per_person = df.groupby(level="person_id").agg(n=("_gone", "size"), g=("_gone", "sum"))
    pp = per_person.reindex(df.index.get_level_values("person_id"))
    df["network_relationships"] = (pp["n"].values - 1)
    df["network_churned"] = (pp["g"].values - df["_gone"].values)

    # ---- keep only relationships active at as_of
    active = (~gone) | (df["tenure_days"] <= 30)
    df = df[active].copy()
    df = df.drop(columns=["_gone"])

    if label_horizon:
        end = as_of + pd.Timedelta(days=label_horizon)
        fo = raw["orders"][(raw["orders"]["order_date"] > as_of) & (raw["orders"]["order_date"] <= end)]
        fs = raw["sessions"][(raw["sessions"]["week_start"] > as_of) & (raw["sessions"]["week_start"] <= end)]
        alive = set(map(tuple, fo[key].drop_duplicates().values)) | set(map(tuple, fs[key].drop_duplicates().values))
        df["churned"] = [0 if idx in alive else 1 for idx in df.index]

    df[FEATURES] = df[FEATURES].astype(float).replace([np.inf, -np.inf], 0).fillna(0)
    return df


class DataPreprocessingAgent(Agent):
    name = "Data Preprocessing Agent"
    role = "Cleans raw data, handles missing values, encodes categories and engineers features"

    def run(self, as_of):
        raw = load_raw()
        df = build_features(raw, as_of)
        summary = (f"Loaded {len(raw['rel'])} customer relationships, {len(raw['orders'])} orders, "
                   f"{int(raw['sessions']['count'].sum())} app visits, {len(raw['tickets'])} tickets. "
                   f"Engineered {len(FEATURES)} features for {len(df)} active customers.")
        return dict(raw=raw, features=df), summary
