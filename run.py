"""Start RetainIQ.

    python run.py            -> start the web app (first run builds the demo data + trains the model)
    python run.py --reset    -> wipe and regenerate the demo network, retrain, then start
"""
import os
import sys
import webbrowser
from threading import Timer

from app.db import init_db, get_conn, MODEL_DIR
from app.main import create_app


def setup(reset=False):
    init_db()
    with get_conn() as conn:
        empty = conn.execute("SELECT COUNT(*) c FROM businesses").fetchone()["c"] == 0
    if reset or empty:
        from app.data_gen import generate
        from app.agents.coordinator import CoordinatorAgent
        for f in os.listdir(MODEL_DIR) if os.path.isdir(MODEL_DIR) else []:
            if f.endswith(".pkl"):
                os.remove(os.path.join(MODEL_DIR, f))
        print("[setup] Generating synthetic multi-business network data ...")
        print("[setup]", generate())
        print("[setup] Training models and running the multi-agent pipeline (takes ~1 min) ...")
        print("[setup]", CoordinatorAgent().run(trigger="setup", retrain=True))


if __name__ == "__main__":
    setup(reset="--reset" in sys.argv)
    port = int(os.environ.get("PORT", 5000))
    app = create_app()
    url = f"http://localhost:{port}"
    print(f"\n  RetainIQ is running at {url}\n  Demo login: admin@stylekart.in / demo123\n")
    if "--no-browser" not in sys.argv:
        Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
