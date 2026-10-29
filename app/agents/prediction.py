"""Churn Prediction Agent.

Trains churn models on historical snapshots of the whole network (every business's
customers contribute — the 'network effect'), compares candidate algorithms, keeps
the best one as the active model version, and scores customers with a churn
probability for the next 90 days.
"""
import json
import os
import pickle
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (roc_auc_score, precision_score, recall_score, f1_score, accuracy_score,
                             confusion_matrix, average_precision_score)
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .base import Agent
from .preprocessing import FEATURES, build_features, load_raw
from ..db import get_conn, MODEL_DIR, jload

HORIZON = 90
SNAPSHOT_OFFSETS = [90, 120, 150, 180]  # days before 'today' used as training snapshots


def build_training_set(raw, today):
    frames = []
    for off in SNAPSHOT_OFFSETS:
        as_of = pd.Timestamp(today) - pd.Timedelta(days=off)
        df = build_features(raw, as_of, label_horizon=HORIZON)
        df["snapshot"] = as_of.date().isoformat()
        frames.append(df)
    return pd.concat(frames)


def _candidates():
    return {
        "Logistic Regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5)),
        "Random Forest": RandomForestClassifier(n_estimators=250, max_depth=10, min_samples_leaf=5,
                                                n_jobs=-1, random_state=7),
        "Gradient Boosting": GradientBoostingClassifier(n_estimators=250, max_depth=3, learning_rate=0.06,
                                                        subsample=0.85, random_state=7),
    }


def _metrics(y, p, thr):
    yhat = (p >= thr).astype(int)
    cm = confusion_matrix(y, yhat, labels=[0, 1])
    return dict(auc=round(float(roc_auc_score(y, p)), 4), pr_auc=round(float(average_precision_score(y, p)), 4),
                accuracy=round(float(accuracy_score(y, yhat)), 4), precision=round(float(precision_score(y, yhat, zero_division=0)), 4),
                recall=round(float(recall_score(y, yhat, zero_division=0)), 4), f1=round(float(f1_score(y, yhat, zero_division=0)), 4),
                threshold=round(float(thr), 3), confusion=cm.tolist())


def _best_threshold(y, p):
    best, best_t = -1, 0.5
    for t in np.arange(0.2, 0.8, 0.02):
        f = f1_score(y, (p >= t).astype(int), zero_division=0)
        if f > best:
            best, best_t = f, t
    return best_t


class ChurnPredictionAgent(Agent):
    name = "Churn Prediction Agent"
    role = "Uses ML models to predict each customer's probability of churning in the next 90 days"

    # ---------- model persistence
    @staticmethod
    def active_version():
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM model_versions WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def load_model(self):
        v = self.active_version()
        if not v or not os.path.exists(v["path"]):
            return None, None
        with open(v["path"], "rb") as f:
            return pickle.load(f), v

    # ---------- training
    def train(self, today, raw=None):
        raw = raw if raw is not None else load_raw()
        data = build_training_set(raw, today)
        X, y = data[FEATURES].values, data["churned"].values
        groups = data.index.get_level_values("person_id")
        tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=11).split(X, y, groups))

        results, fitted = {}, {}
        for name, model in _candidates().items():
            model.fit(X[tr], y[tr])
            p = model.predict_proba(X[te])[:, 1]
            thr = _best_threshold(y[te], p)
            results[name] = _metrics(y[te], p, thr)
            fitted[name] = model
        best = max(results, key=lambda k: results[k]["auc"])
        model = fitted[best]
        # refit the winner on all data
        model.fit(X, y)

        if best == "Logistic Regression":
            imp = np.abs(model[-1].coef_[0])
        else:
            imp = model.feature_importances_
        imp = imp / imp.sum()
        importance = sorted(({"feature": f, "importance": round(float(v), 4)} for f, v in zip(FEATURES, imp)),
                            key=lambda d: -d["importance"])

        os.makedirs(MODEL_DIR, exist_ok=True)
        with get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM model_versions").fetchone()["c"] + 1
            path = os.path.join(MODEL_DIR, f"model_v{n}.pkl")
            bundle = dict(model=model, algorithm=best, threshold=results[best]["threshold"], features=FEATURES,
                          base_rate=float(y.mean()), background=data[FEATURES].median().to_dict())
            with open(path, "wb") as f:
                pickle.dump(bundle, f)
            conn.execute("UPDATE model_versions SET is_active=0")
            conn.execute(
                "INSERT INTO model_versions(created_at, algorithm, metrics, candidates, feature_importance, n_train, path, is_active)"
                " VALUES (?,?,?,?,?,?,?,1)",
                (datetime.now().isoformat(timespec="seconds"), best, json.dumps(results[best]), json.dumps(results),
                 json.dumps(importance), int(len(data)), path))
        summary = (f"Trained 3 candidate models on {len(data)} labelled snapshots "
                   f"(churn rate {y.mean():.1%}). Best: {best} — AUC {results[best]['auc']}, "
                   f"recall {results[best]['recall']}, F1 {results[best]['f1']}. Saved as v{n}.")
        return bundle, summary

    # ---------- scoring
    def run(self, features, today):
        bundle, version = self.load_model()
        trained_summary = ""
        if bundle is None:
            bundle, trained_summary = self.train(today)
            version = self.active_version()
        p = bundle["model"].predict_proba(features[FEATURES].values)[:, 1]
        out = features[[]].copy()
        out["churn_prob"] = p
        summary = (f"{trained_summary + ' ' if trained_summary else ''}Scored {len(p)} customers with model "
                   f"v{version['id']} ({bundle['algorithm']}). Mean churn probability {p.mean():.1%}; "
                   f"{int((p >= 0.5).sum())} customers above 50%.")
        return dict(probs=out, bundle=bundle, version=version), summary
