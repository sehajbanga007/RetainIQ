"""Coordinator Agent.

Central hub of the multi-agent system: assigns tasks to the specialised agents,
runs independent agents in parallel, passes results between stages, integrates
everything into one final response plan per customer and stores it.

Stage 1  Data Preprocessing Agent
Stage 2  Behavior Analysis Agent  ||  Churn Prediction Agent          (parallel)
Stage 3  Explainability Agent  ||  Risk Assessment Agent              (parallel)
Stage 4  Factor Identification Agent  ||  Persona Agent               (parallel)
Stage 5  Retention Strategy Agent
Stage 6  Coordinator integrates + saves the Final Response Plan
"""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date

import pandas as pd

from .base import Agent
from .behavior import BehaviorAnalysisAgent
from .explainability import ExplainabilityAgent
from .factors import FactorIdentificationAgent
from .persona import PersonaAgent
from .prediction import ChurnPredictionAgent
from .preprocessing import DataPreprocessingAgent, FEATURES
from .retention import RetentionStrategyAgent
from .risk import RiskAssessmentAgent
from ..db import get_conn, get_meta

AGENTS = [
    ("Coordinator Agent", "Orchestrates the workflow, assigns tasks to specialised agents and combines results"),
    (DataPreprocessingAgent.name, DataPreprocessingAgent.role),
    (BehaviorAnalysisAgent.name, BehaviorAnalysisAgent.role),
    (ChurnPredictionAgent.name, ChurnPredictionAgent.role),
    (ExplainabilityAgent.name, ExplainabilityAgent.role),
    (RiskAssessmentAgent.name, RiskAssessmentAgent.role),
    (FactorIdentificationAgent.name, FactorIdentificationAgent.role),
    (PersonaAgent.name, PersonaAgent.role),
    (RetentionStrategyAgent.name, RetentionStrategyAgent.role),
]

_lock = threading.Lock()


def current_as_of():
    d = get_meta("as_of_date")
    return pd.Timestamp(d) if d else pd.Timestamp(date.today())


class CoordinatorAgent(Agent):
    name = "Coordinator Agent"
    role = AGENTS[0][1]

    def __init__(self):
        self.pre = DataPreprocessingAgent()
        self.behavior = BehaviorAnalysisAgent()
        self.pred = ChurnPredictionAgent()
        self.expl = ExplainabilityAgent()
        self.risk = RiskAssessmentAgent()
        self.factors = FactorIdentificationAgent()
        self.persona = PersonaAgent()
        self.retention = RetentionStrategyAgent()

    def _log(self, run_id, agent, stage, ms, summary, status="Completed"):
        with get_conn() as conn:
            conn.execute("INSERT INTO agent_runs(run_id, agent, stage, status, started_at, duration_ms, summary)"
                         " VALUES (?,?,?,?,?,?,?)",
                         (run_id, agent, stage, status, datetime.now().isoformat(timespec="seconds"), ms, summary))

    def _call(self, run_id, stage, agent, *args):
        t0 = time.perf_counter()
        try:
            out, summary = agent.run(*args)
        except Exception as e:
            self._log(run_id, agent.name, stage, int((time.perf_counter() - t0) * 1000), f"Failed: {e}", "Failed")
            raise
        self._log(run_id, agent.name, stage, int((time.perf_counter() - t0) * 1000), summary)
        return out

    def run(self, business_id=None, trigger="manual", retrain=False):
        with _lock:
            return self._run(business_id, trigger, retrain)

    def _run(self, business_id, trigger, retrain):
        run_id = uuid.uuid4().hex[:10]
        t_start = time.perf_counter()
        as_of = current_as_of()
        with get_conn() as conn:
            conn.execute("INSERT INTO pipeline_runs(run_id, business_id, trigger, started_at, status) VALUES (?,?,?,?,?)",
                         (run_id, business_id, trigger, datetime.now().isoformat(timespec="seconds"), "Running"))
            businesses = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM businesses")}
        self._log(run_id, self.name, 0, 0,
                  f"Run started ({trigger}). Assigning tasks to {len(AGENTS) - 1} specialised agents for "
                  f"{len(businesses)} businesses in the network, as of {as_of.date()}.")
        try:
            # Stage 1
            pre = self._call(run_id, 1, self.pre, as_of)
            raw, feats = pre["raw"], pre["features"]
            if retrain:
                t0 = time.perf_counter()
                _, s = self.pred.train(as_of, raw)
                self._log(run_id, "Churn Prediction Agent (training)", 1, int((time.perf_counter() - t0) * 1000), s)

            # Stage 2 (parallel)
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_beh = ex.submit(self._call, run_id, 2, self.behavior, feats)
                f_pred = ex.submit(self._call, run_id, 2, self.pred, feats, as_of)
                behavior, pred = f_beh.result(), f_pred.result()
            probs, bundle = pred["probs"], pred["bundle"]

            # Stage 3 (parallel)
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_exp = ex.submit(self._call, run_id, 3, self.expl, feats, bundle)
                f_risk = ex.submit(self._call, run_id, 3, self.risk, feats, probs)
                expl, risk = f_exp.result(), f_risk.result()

            # Stage 4 (parallel)
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_fac = ex.submit(self._call, run_id, 4, self.factors, feats, expl["contrib"], raw, as_of)
                f_per = ex.submit(self._call, run_id, 4, self.persona, raw, feats, probs, as_of)
                factors, personas = f_fac.result(), f_per.result()

            # Stage 5
            plans = self._call(run_id, 5, self.retention, feats, expl["explanations"], risk, behavior, personas,
                               businesses)

            # Stage 6: integrate + persist final response plans
            t0 = time.perf_counter()
            now = datetime.now().isoformat(timespec="seconds")
            rows, hist = [], []
            frec = feats[FEATURES].round(3).to_dict("records")
            rrec = risk.to_dict("records")
            brec = behavior.to_dict("records")
            extra = feats[["lifetime_spend", "lifetime_orders", "plan", "joined_at"]].to_dict("records")
            for i, idx in enumerate(feats.index):
                bid, pid = int(idx[0]), int(idx[1])
                r, b = rrec[i], brec[i]
                ex_items = expl["explanations"][i]
                ups = [e for e in ex_items if e["impact"] > 0]
                top_reason = ups[0]["reason"] if ups else "No major risk factors"
                fvals = dict(frec[i])
                ex_ = extra[i]
                fvals.update(lifetime_spend=float(ex_["lifetime_spend"]), lifetime_orders=int(ex_["lifetime_orders"]),
                             plan=ex_["plan"], joined_at=ex_["joined_at"].date().isoformat())
                rows.append((bid, pid, float(r["churn_prob"]), r["risk_tier"], r["priority"], float(r["clv"]),
                             float(r["revenue_at_risk"]), b["engagement"], b["trend"], b["segment"], top_reason,
                             json.dumps(ex_items), json.dumps(plans[i]), json.dumps(fvals), now))
                hist.append((bid, pid, float(r["churn_prob"]), as_of.date().isoformat()))
            with get_conn() as conn:
                conn.execute("DELETE FROM scores")
                conn.executemany("INSERT INTO scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                conn.execute("DELETE FROM score_history WHERE scored_at=?", (as_of.date().isoformat(),))
                conn.executemany("INSERT INTO score_history VALUES (?,?,?,?)", hist)
                conn.executemany("INSERT OR REPLACE INTO personas(person_id, data, updated_at) VALUES (?,?,?)",
                                 [(pid, json.dumps(p), now) for pid, p in personas.items()])
                for k, v in factors.items():
                    conn.execute("INSERT OR REPLACE INTO insights(key, data, updated_at) VALUES (?,?,?)",
                                 (f"factors:{k}", json.dumps(v), now))
                conn.execute("UPDATE pipeline_runs SET finished_at=?, n_customers=?, status='Completed' WHERE run_id=?",
                             (now, len(rows), run_id))
            self._log(run_id, self.name, 6, int((time.perf_counter() - t0) * 1000),
                      f"Integrated outputs into final response plans (score + key factors + actions + persona) for "
                      f"{len(rows)} customers and {len(personas)} personas. Total run time "
                      f"{time.perf_counter() - t_start:.1f}s.")
            return dict(run_id=run_id, customers=len(rows), personas=len(personas))
        except Exception as e:
            with get_conn() as conn:
                conn.execute("UPDATE pipeline_runs SET finished_at=?, status=? WHERE run_id=?",
                             (datetime.now().isoformat(timespec="seconds"), f"Failed: {e}", run_id))
            raise
