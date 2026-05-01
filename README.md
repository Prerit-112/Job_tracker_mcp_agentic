# Job Tracker MCP

FastMCP server ([FastMCP](https://github.com/jlowin/fastmcp)) for tracking job applications: local JSON storage (`data/jobs.json`), Adzuna job search (with offline stubs), Prefab dashboard in MCP-capable hosts, and optional tooling for resume fit scoring, staleness deadlines, follow-up drafts, and company intel.

## Requirements

- **Python ≥ 3.11**

## Virtual environment

Use a virtual environment so dependencies and the installed package stay isolated from your system Python. Create it once in the repo root (`job_app_tracker`), activate it in each new shell, then follow **Install** and **Run** using that same shell.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If activation fails with **UnauthorizedAccess** / “running scripts is disabled”, pick one of these:

- **This session only (no permanent change):**  
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`  
  then run `.\.venv\Scripts\Activate.ps1` again.

- **Persist for your user (does not require Administrator):**  
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

- **Skip PowerShell scripts:** use **Command Prompt** below (`activate.bat`), or call the venv interpreter directly, e.g. `.\.venv\Scripts\python.exe -m job_tracker_mcp.server` (no `Activate` needed).

**Windows (Command Prompt)**

```cmd
python -m venv .venv
.\.venv\Scripts\activate.bat
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

The prompt usually shows `(.venv)` while the environment is active. To exit: `deactivate`.

The repo’s `.gitignore` ignores `.venv/` so the folder is not committed.

**uv:** `uv sync` (see below) creates and uses a project `.venv` by default, so you can rely on that instead of manual `python -m venv` if you use `uv` for everything.

## Install

From the repo root (`job_app_tracker`), with the virtual environment **activated** (or after `uv sync`):

```bash
pip install -e ".[dev]"
```

To run the **[Streamlit](https://streamlit.io/) MCP agent UI** (`client/app.py`), also install:

```bash
pip install -e ".[dev,streamlit-client]"
```

For the **Tier 2 web UI** (FastAPI + same-origin Prefab in `client/web/`), install:

```bash
pip install -e ".[dev,web]"
```

You can install **both** clients with:

```bash
pip install -e ".[dev,streamlit-client,web]"
```

**uv** (manages `.venv` automatically):

```bash
uv sync --extra dev
# optional Streamlit UI
uv sync --extra dev --extra streamlit-client
# optional Tier 2 web UI
uv sync --extra dev --extra web
# both optional UIs
uv sync --extra dev --extra streamlit-client --extra web
```

## Environment

Copy the example env file and fill in what you use:

```bash
# Windows (PowerShell)
copy .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required for **`client/app.py`** and the **[Tier 2 web UI](#tier-2-web-ui-fastapi)** (sidebar / form, or `.env`). |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `ADZUNA_COUNTRY` | Job search (`search_jobs`). Without keys, offline fixtures are used. |
| `COMPANY_INTEL_MODE` | `mock` (default) or `wikipedia` for **`get_company_intel`**. |
| `JOB_TRACKER_AGENT_LOG` | Set to **`1`** to log MCP tool BEGIN/END lines on **stderr**. |

See **`.env.example`** for defaults.

## Run the MCP server (stdio)

With the venv activated (or `uv run`):

```bash
python -m job_tracker_mcp.server
```

Equivalent console entry point (after install):

```bash
job-tracker-mcp
```

With **uv** (no manual activate needed):

```bash
uv run python -m job_tracker_mcp.server
```

Point an MCP-capable host (e.g. Claude Desktop with MCP Apps) at the same command; use the repo root as **cwd** so `data/jobs.json` resolves. Example fragment and PowerShell trace capture are in **`ASSIGNMENT_PROMPTS.txt`**.

## Streamlit UI (chat + embedded Prefab)

Requires `.[streamlit-client]` and an OpenAI API key for the tool-calling agent. With the venv activated, from the **repo root**:

```bash
python -m streamlit run client/app.py
```

Use this form on **Windows** if you see *Application Control policy has blocked this file* (or similar) when running `streamlit.exe` from `.venv\Scripts` — policy often allows `python.exe` but blocks unsigned script launchers.

Optional shorthand (same effect when your system allows it):

```bash
streamlit run client/app.py
```

With **uv**: `uv run python -m streamlit run client/app.py`.

Run from the **repo root** (or set “MCP server cwd” in the sidebar to the folder that contains `data/` and the installed package). Each chat turn spawns `python -m job_tracker_mcp.server` and runs tools until the model replies.

## Tier 2 web UI (FastAPI + Prefab)

Use this when **embedded Prefab inside Streamlit** fails (iframe / static / policy issues) or you want a **single browser URL** with chat and dashboard side by side.

**Install:** `pip install -e ".[dev,web]"` (includes FastAPI, Uvicorn, MCP client, OpenAI SDK — not Streamlit).

**Run** from the **repo root** so `client` is importable and `data/` resolves:

```bash
python -m uvicorn client.web.app:app --host 127.0.0.1 --port 7861
```

Optional auto-reload while developing:

```bash
python -m uvicorn client.web.app:app --host 127.0.0.1 --port 7861 --reload
```

Or:

```bash
python -m client.web --host 127.0.0.1 --port 7861 --reload
```

**Windows / Application Control:** if a launcher is blocked, keep using `python -m …` as above.

Then open **http://127.0.0.1:7861/** (or your chosen port).

**What you get**

- **Left:** session fields (API key, model, MCP cwd, max tool rounds), profile (same `data/user_profile.json` as Streamlit), tool-trace panel, chat.
- **Right:** **Prefab** dashboard loaded from **`/prefab/dashboard.html`** on the **same origin** (reliable embed). Use the filter dropdown to refresh the iframe; it matches the status filters used in Streamlit.
- **API:** `POST /api/chat` (same agent as Streamlit), `GET`/`PUT /api/profile`, `GET /health`.

**Prefab row buttons** (`Follow up`, `Intel`, filter **CallTool**): still target **MCP host** semantics inside the Prefab bundle. In this web shell they do **not** automatically post into the chat pane unless you add a custom bridge (same limitation as standalone HTML). Use chat for actions, or Streamlit’s copy-paste prompts, or an MCP client + `push_dashboard` for full wired UI.

### Choosing Streamlit vs Tier 2 web

| | **Streamlit** (`client/app.py`) | **Tier 2 web** (`client/web/`) |
|--|----------------------------------|--------------------------------|
| Chat + MCP agent | Yes | Yes |
| Tool trace | Dedicated tab | Collapsible sidebar |
| Prefab | iframe / inline (can fail) | Same-origin `/prefab/dashboard.html` |
| Profile | Sidebar | Sidebar form (shared JSON file) |

You can keep **both** installed and pick per session; they share **`data/`** and **`job_tracker_mcp`**.

## MCP tools (summary)

| Tool | Role |
|------|------|
| `search_jobs` | Adzuna search or offline fixtures. |
| `crud_tracker` | `list` \| `get` \| `create` \| `update` \| `delete` on **`data/jobs.json`**. |
| `check_deadlines` | Days since apply + stale tiers. |
| `score_resume_fit` | Keyword overlap vs JD; uses **`data/resume.txt`** by default. |
| `draft_followup` | Deterministic follow-up body for a job id. |
| `get_company_intel` | Mock or Wikipedia-backed company blurb. |
| `push_dashboard` | Prefab UI (metrics, filters, table) in MCP Apps hosts. |

## Tests and offline agent demo

With the venv activated:

```bash
pytest -q
python -m job_tracker_mcp.agent_demo
```

Or with **uv**: `uv run pytest -q` and `uv run python -m job_tracker_mcp.agent_demo`.

`agent_demo` runs a scripted Tier‑1-style walkthrough with tracing; set `JOB_TRACKER_AGENT_LOG=1` when running the server to echo structured tool traces to stderr in normal use.
