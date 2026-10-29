# RetainIQ — Multi-Agent Customer Churn Prediction & Persona Network

**Team:** Sehaj Singh Banga (S005), Pratik Bherwani (S008), Yash Bohra (S009)

RetainIQ is a web platform for businesses. It predicts which customers are about to leave, explains why,
recommends how to keep them, and builds one shared **persona** for every shopper across all brands that use
RetainIQ. Any brand on the network can recognise a returning shopper at checkout (one-tap login) and
personalise for them.

---

## 1. Run it (2 minutes)

You need **Python 3.10+**.

**Windows:** double-click `run_windows.bat`
**Mac / Linux:** `bash run_mac_linux.sh`

Or manually:

```bash
pip install -r requirements.txt
python run.py
```

The first run generates the demo network, trains the models and runs all agents (about 1 minute).
Then the browser opens at **http://localhost:5000**.

| Demo login | Password |
|---|---|
| admin@stylekart.in (Fashion) | demo123 |
| admin@gadgethub.in (Electronics) | demo123 |
| admin@freshbasket.in (Grocery) | demo123 |
| admin@streambox.in (OTT) | demo123 |
| admin@quickbite.in (Food delivery) | demo123 |
| admin@fitzone.in (Fitness) | demo123 |

Start fresh any time: `python run.py --reset`

---

### Put it online (GitHub + Render, free)

1. Create a new repository on GitHub and upload everything in this folder (keep `data/` and `models/` empty).
2. On render.com, sign in with GitHub, click **New → Web Service** and pick the repository.
3. Settings: Runtime **Python 3**, Instance type **Free**,
   Build command: `pip install -r requirements.txt && python -c "import run; run.setup()"`
   Start command: `gunicorn wsgi:app --workers 1 --threads 8 --timeout 180 --bind 0.0.0.0:$PORT`
   Environment variable: `PYTHON_VERSION` = `3.12.7`
   (Or choose **New → Blueprint**: `render.yaml` holds the same settings.)
4. Click **Create**. The build trains the model (3–5 minutes); then the app is live at `https://<name>.onrender.com`.

On the free plan the app sleeps after 15 minutes without visitors (the next visit wakes it in about a minute)
and resets to the demo data when it restarts.

## 2. What's inside

| Page | What it shows |
|---|---|
| **Overview** | Active customers, high-risk count, revenue at risk, risk distribution, top churn drivers, risky patterns, segments, priority list |
| **Customers at risk** | Every customer with churn probability, risk tier, main reason, behaviour and next best action; search, filter, sort, export CSV |
| **Customer page** | Churn score, *why this score* (factor-by-factor breakdown), recommended actions with Send buttons, 12-month behaviour chart, orders, tickets, network persona, action outcomes |
| **Persona network** | Search any person in the network → archetype, interests, spend band, loyalty, churn tendency, likely next purchases, brands they're likely to leave |
| **Retention actions** | All actions sent, outcome tracking (Retained / Churned), success rate per action type |
| **/checkout-demo** | A demo store using the one-tap RetainIQ login (like GoKwik): enter a phone number → details auto-filled + persona sent to the store |

---

## 3. Multi-agent architecture

```
                        Coordinator Agent  (orchestrates, runs stages, integrates)
                                 |
Stage 1   Data Preprocessing Agent        clean data, handle missing values, encode, engineer 27 features
Stage 2   Behavior Analysis Agent  ||  Churn Prediction Agent          (parallel)
Stage 3   Explainability Agent     ||  Risk Assessment Agent           (parallel)
Stage 4   Factor Identification    ||  Persona Agent                   (parallel)
Stage 5   Retention Strategy Agent
Stage 6   Coordinator → Final Response Plan = score + key factors + actions + persona (saved to database)
Feedback  Action outcomes are recorded; retraining on the latest data creates a new model version

The agents run automatically: at start-up, every 30 minutes, and ~1 minute after new data arrives
(orders, one-tap logins, uploads via the API). Businesses never have to trigger them.
```

| Agent | File | Job |
|---|---|---|
| Coordinator | `app/agents/coordinator.py` | Assigns tasks, runs agents in parallel (thread pool), merges outputs, logs every step |
| Data Preprocessing | `app/agents/preprocessing.py` | Cleans data; builds features like recency, order/spend trends, app-usage trend, tickets, CSAT, discount & return rate, plan type, network features |
| Behavior Analysis | `app/agents/behavior.py` | Engagement level, trend (growing/declining), RFM-style segment |
| Churn Prediction | `app/agents/prediction.py` | Trains Logistic Regression, Random Forest and Gradient Boosting; picks the best by ROC-AUC; predicts 90-day churn probability |
| Explainability | `app/agents/explainability.py` | Per-customer contribution of each feature (tree path attribution / linear attribution) |
| Risk Assessment | `app/agents/risk.py` | Risk tier, 12-month value, revenue at risk, priority (P1–P4) |
| Factor Identification | `app/agents/factors.py` | Global churn drivers + data-backed patterns ("monthly-plan customers churn 2.5x more") |
| Persona | `app/agents/persona.py` | Cross-business persona: interests, spend band, price sensitivity, loyalty, churn tendency, archetype, likely next categories (association lift) |
| Retention Strategy | `app/agents/retention.py` | Maps each customer's risk factors + value + persona to ranked actions, offers and channels |

### How churn is defined
A customer has **churned** if they make **no orders and no app visits for 90 days**. Training uses 4 historical
snapshots (90/120/150/180 days ago): features are computed as of the snapshot, the label comes from the next 90
days. The test split is by person, so the model is always evaluated on people it never saw.

### Why the network matters (USP)
- One model learns from **all brands' customers**, so even a new brand gets good predictions on day one.
- **Network features**: if a person has left other brands before, that raises their risk everywhere.
- **Personas** follow the shopper: a new store can personalise from the first visit.

---

## 4. Live activity simulator

While the app runs, `app/simulator.py` moves a simulated clock forward **one day every 2 minutes**. Each
simulated day, active shoppers order, open the app and sometimes raise tickets; some start drifting away and
eventually stop, some come back (customers who were sent a retention action are 4x more likely to return), and
new shoppers join every brand. After each day the agents rescore everyone, so the dashboard updates by itself
(the sidebar shows **Live** and the simulated date). Retention actions get their outcome (Retained / Churned)
recorded automatically a few simulated days after they are sent.

Settings: `RETAINIQ_TICK_SEC=60 python run.py` (faster), `RETAINIQ_SIMULATOR=0 python run.py` (off).

## 5. Data

`app/data_gen.py` simulates 18 months for 6 brands that share ~2,500 shoppers (≈5,400 customer relationships,
75k orders, 130k weekly app-visit records, 3.4k support tickets). Each shopper has hidden traits (price
sensitivity, loyalty, tendency to switch brands, patience) that drive behaviour consistently across brands, and
churners show realistic warning signs (declining usage, complaints) — though ~30% leave suddenly, like in real life.

We used synthetic data because public churn datasets (e.g. Kaggle Telco) cover a single company and cannot link
the same person across businesses, which the persona network needs. Real data can be loaded through
**Data & API → Upload** (sample CSVs are downloadable on that page) or the API.

---

## 6. API (for a business's own website/app)

All calls need the header `X-API-Key` (shown on the Data & API page).

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/v1/network-login` | One-tap login: `{"phone": "...", "consent": true}` → name, email, phone, address + persona; links the shopper to the business |
| GET | `/api/v1/persona?email=` or `?phone=` | Persona of any person in the network |
| POST | `/api/v1/events/order` | Send an order in real time |
| GET | `/api/v1/scores?limit=50` | Churn scores + reasons + actions for your customers |

---

## 7. Suggested demo flow (5 minutes)

1. Sign in as **StyleKart** → Overview: active customers, high-risk count, revenue at risk, top drivers.
2. Click the top customer → explain the *Why this score* chart and the recommended actions → click **Send**.
3. Scroll to **Network persona** → same person's activity at other brands, likely next purchases, brands they may leave.
4. **Persona network** → search a name → full cross-brand profile.
5. **Open one-tap checkout demo** → click the demo phone number → tick consent → details auto-fill and the store receives the persona.
6. **Retention actions** → mark an action Retained → success rate updates.

The agents, model evaluation and API are explained in the presentation (they work in the background).

## 8. Tech stack

Python · Flask (REST API) · SQLite · pandas / NumPy · scikit-learn · vanilla JavaScript + SVG charts (no build step)

```
retainiq/
├── run.py                 start the app (first run = setup)
├── requirements.txt
├── app/
│   ├── main.py            web server + REST API
│   ├── db.py              database schema
│   ├── data_gen.py        synthetic network generator
│   ├── simulator.py       live activity simulator
│   ├── ingest.py          CSV + API ingestion, identity resolution (email/phone)
│   ├── agents/            the 9 agents
│   └── static/            dashboard (index.html, app.js, charts.js, styles.css, checkout.html)
├── data/                  retainiq.db (created on first run)
└── models/                trained model versions (created on first run)
```
