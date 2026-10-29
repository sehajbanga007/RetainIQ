"""Common base class for all agents."""
import time


class Agent:
    """Every agent has a name, a role and a run() method returning (output, summary)."""
    name = "Agent"
    role = ""

    def timed(self, fn, *args, **kwargs):
        t0 = time.perf_counter()
        out, summary = fn(*args, **kwargs)
        return out, summary, int((time.perf_counter() - t0) * 1000)
