"""SQLite database layer for RetainIQ."""
import os
import sqlite3
import json
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "models")
DB_PATH = os.environ.get("RETAINIQ_DB", os.path.join(DATA_DIR, "retainiq.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_level INTEGER DEFAULT 2,
    is_subscription INTEGER DEFAULT 0,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    api_key TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

-- A person is one real customer in the RetainIQ network (shared across businesses)
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    phone TEXT UNIQUE,
    city TEXT,
    address TEXT,
    age INTEGER,
    created_at TEXT NOT NULL
);

-- A relationship = a person being a customer of a business
CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    person_id INTEGER NOT NULL REFERENCES persons(id),
    joined_at TEXT NOT NULL,
    plan TEXT DEFAULT 'Regular',
    consent INTEGER DEFAULT 1,
    source TEXT DEFAULT 'seed',
    UNIQUE(business_id, person_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT,
    discount_used INTEGER DEFAULT 0,
    returned INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_orders_bp ON orders(business_id, person_id);

CREATE TABLE IF NOT EXISTS sessions (
    business_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    week_start TEXT NOT NULL,
    count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_bp ON sessions(business_id, person_id);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    issue TEXT,
    resolved INTEGER DEFAULT 1,
    csat INTEGER
);
CREATE INDEX IF NOT EXISTS ix_tickets_bp ON tickets(business_id, person_id);

CREATE TABLE IF NOT EXISTS scores (
    business_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    churn_prob REAL,
    risk_tier TEXT,
    priority TEXT,
    clv REAL,
    revenue_at_risk REAL,
    engagement TEXT,
    trend TEXT,
    segment TEXT,
    top_reason TEXT,
    explanation TEXT,
    actions TEXT,
    features TEXT,
    scored_at TEXT,
    PRIMARY KEY (business_id, person_id)
);

CREATE TABLE IF NOT EXISTS score_history (
    business_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    churn_prob REAL,
    scored_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_sh_bp ON score_history(business_id, person_id);

CREATE TABLE IF NOT EXISTS personas (
    person_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS retention_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    channel TEXT,
    status TEXT DEFAULT 'Planned',
    churn_prob_at_action REAL,
    note TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS model_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    algorithm TEXT,
    metrics TEXT,
    candidates TEXT,
    feature_importance TEXT,
    n_train INTEGER,
    path TEXT,
    is_active INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    business_id INTEGER,
    trigger TEXT,
    started_at TEXT,
    finished_at TEXT,
    n_customers INTEGER,
    status TEXT
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    agent TEXT,
    stage INTEGER,
    status TEXT,
    started_at TEXT,
    duration_ms INTEGER,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS network_lookups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER,
    person_id INTEGER,
    at TEXT,
    consent INTEGER,
    channel TEXT
);

CREATE TABLE IF NOT EXISTS insights (
    key TEXT PRIMARY KEY,
    data TEXT,
    updated_at TEXT
);
"""


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def get_meta(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_meta(key, value):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def jload(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default
