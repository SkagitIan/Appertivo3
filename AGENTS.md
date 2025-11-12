## AGENTS

These instructions apply to all autonomous or semi-autonomous scripts, tasks, or LLM-driven agents in this repository.

### Core Principles

1. **Reproducibility over autonomy** – every action must be traceable, reversible, and logged.
2. **Human in the loop** – no write, delete, or deploy operation runs without explicit confirmation from an authorized account.
3. **Fail closed** – if an environment variable, API key, or required dependency is missing, the agent exits gracefully.

---
** When working with the outscraper API , refer to the docs here: /home/django/appertivo/outscraperAPI.json
## LOG FILES:  /home/django/appertivo/logs/app-log.json

### Development Guidelines

* Add or update **tests** for all new logic or API integrations.
* Keep commits focused; use descriptive, imperative messages.
* Follow **PEP 8**, include docstrings and type hints.
* Prefer `rg` for code searches.
* Small pull requests, clear change summaries, and evidence of testing.

---

### Secret Management

```python
import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables safely
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
if not STRIPE_API_KEY:
    raise EnvironmentError("STRIPE_API_KEY not found. Check your .env file.")
```

**Rules**

* Never hard-code secrets or tokens.
* `.env` must never be committed.
* Agents must **read only**; they cannot write `.env` or export secrets dynamically.
* All secrets should be scoped per environment (`dev`, `staging`, `prod`) via OS vars.

---

### Agent Control & Safety

1. **Bounded Scope**

   * Agents operate only within their registered Django apps or service modules.
   * Use explicit allow-lists for filesystem and network access.

2. **Execution Contracts**

   * All agent entrypoints require:

     * a clear task name (`task_name`),
     * a human readable `summary`,
     * and an optional `dry_run` flag defaulting to `True`.
   * Agents must log all output and decisions to `/logs/agents/<date>.log`.

3. **Rate & Cost Controls**

   * Respect `MAX_TOKENS_PER_MINUTE`, `MAX_CALLS_PER_HOUR`, etc. via environment vars.
   * Long-running jobs use Celery queues with back-pressure enabled.

4. **Mutation Guard**

   * No agent can write to the database or push to git without a checksum diff and explicit approval signal.
   * All destructive actions (`delete`, `drop`, `overwrite`) must require a `--force` flag or `confirm=True`.

5. **Observability**

   * Standardize structured logs (JSON or line-delimited text).
   * Include: timestamp, agent name, task, duration, and status.

---

### Agent Design Style 

* Keep them **functional**, not over-abstracted.
* Avoid “smart” error handling; fail loudly.
* Use plain classes or scripts — no heavy orchestration frameworks.
* Build modular pipelines: `ingest → enrich → output`, each callable in isolation.
* Each agent should have a **README** describing purpose, scope, and example runs.

