"""FastAPI Tier 2 UI: single-origin Prefab dashboard + chat using the same agent loop as Streamlit."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

from client.agent import AgentTurnResult, run_agent_turn
from client.mcp_utils import compose_system_instruction, humanize_assistant_reply, repo_root
from job_tracker_mcp.dashboard import build_prefab_dashboard
from job_tracker_mcp.storage import load_jobs_raw
from job_tracker_mcp.user_profile import load_user_profile, save_user_profile
from prefab_ui.app import PrefabApp
from prefab_ui.components import Column, Heading, Text


app = FastAPI(title="Job Tracker Web", version="0.1.0")


def _strip_client_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        i += 1
    return messages[i:]


def _inject_system(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    system_content = compose_system_instruction(load_user_profile()) + (
        "\n\n### Web UI routing preferences\n"
        "- If the user asks for job fit, match score, or suitability, call `score_resume_fit`.\n"
        "- Do not call `check_deadlines` for fit-only questions.\n"
        "- Call `check_deadlines` only for staleness, follow-up timing, or aging pipeline questions."
    )
    return [{"role": "system", "content": system_content}, *messages]


class ProfilePayload(BaseModel):
    skills: str = ""
    experience: str = ""
    company_preferences: str = ""


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    user_message: str
    model: str = "gpt-4o-mini"
    project_root: str | None = None
    max_steps: int = Field(default=14, ge=2, le=64)
    api_key: str | None = None


class ChatResponse(BaseModel):
    messages: list[dict[str, Any]]
    assistant_text: str
    assistant_display: str
    trace: list[dict[str, Any]]
    error: str | None = None


class StatsResponse(BaseModel):
    applied: int
    interviews: int
    offers: int
    fit_avg: float


def _humanize_assistant_bubbles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") != "assistant" or m.get("tool_calls"):
            out.append(m)
            continue
        m2 = dict(m)
        c = m2.get("content")
        if isinstance(c, str):
            m2["content"] = humanize_assistant_reply(c)
        out.append(m2)
    return out


@app.on_event("startup")
def _startup() -> None:
    """Ensure repo root is on path after any fork (e.g. uvicorn reload)."""
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/profile", response_model=ProfilePayload)
def get_profile() -> ProfilePayload:
    p = load_user_profile()
    return ProfilePayload(
        skills=p.get("skills", ""),
        experience=p.get("experience", ""),
        company_preferences=p.get("company_preferences", ""),
    )


@app.put("/api/profile")
def put_profile(body: ProfilePayload) -> dict[str, str]:
    save_user_profile(
        {
            "skills": body.skills,
            "experience": body.experience,
            "company_preferences": body.company_preferences,
        }
    )
    return {"status": "saved"}


@app.get("/api/stats", response_model=StatsResponse)
def get_stats() -> StatsResponse:
    jobs = load_jobs_raw().get("jobs", [])
    applied = sum(1 for j in jobs if str(j.get("status", "")).strip() == "Applied")
    interviews = sum(1 for j in jobs if str(j.get("status", "")).strip() == "Interview")
    offers = sum(1 for j in jobs if str(j.get("status", "")).strip() == "Offer")
    fit_vals: list[float] = []
    for j in jobs:
        v = j.get("fit_percent")
        try:
            if v is not None:
                fit_vals.append(float(v))
        except (TypeError, ValueError):
            continue
    fit_avg = round((sum(fit_vals) / len(fit_vals)) if fit_vals else 0.0, 1)
    return StatsResponse(applied=applied, interviews=interviews, offers=offers, fit_avg=fit_avg)


@app.get("/prefab/dashboard.html", response_class=HTMLResponse)
def prefab_dashboard(
    dash_filter: str = Query("All", alias="filter"),
) -> HTMLResponse:
    allowed = {"All", "Applied", "Screen", "Interview", "Offer", "Rejected", "Withdrawn"}
    filt = dash_filter if dash_filter in allowed else "All"
    prefab_app = build_prefab_dashboard(filt, None)
    return HTMLResponse(content=prefab_app.html(), media_type="text/html; charset=utf-8")


@app.get("/prefab/smoke.html", response_class=HTMLResponse)
def prefab_smoke() -> HTMLResponse:
    """Tiny Prefab page to isolate renderer/runtime issues from dashboard complexity."""
    with PrefabApp(title="Prefab smoke test") as app_ui:
        with Column(gap=3, css_class="p-6"):
            Heading("Prefab smoke test")
            Text("If this does not render, the issue is the browser/webview runtime, not dashboard data.")
    return HTMLResponse(content=app_ui.html(), media_type="text/html; charset=utf-8")


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    api_key = (req.api_key or "").strip() or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Missing API key: set OPENAI_API_KEY in .env or pass api_key in the request.",
        )
    root = Path(req.project_root).expanduser() if req.project_root else repo_root()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"project_root is not a directory: {root}")

    history = _inject_system(_strip_client_system(req.messages))
    try:
        result: AgentTurnResult = await run_agent_turn(
            history,
            req.user_message,
            project_root=root.resolve(),
            api_key=api_key,
            model=req.model,
            max_steps=req.max_steps,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e

    assistant_display = humanize_assistant_reply(result.assistant_text)
    trace_out = [
        {"name": e.name, "arguments": e.arguments, "result_preview": e.result_preview}
        for e in result.trace
    ]
    return ChatResponse(
        messages=_humanize_assistant_bubbles(result.messages),
        assistant_text=result.assistant_text,
        assistant_display=assistant_display,
        trace=trace_out,
        error=result.error,
    )


def _index_html() -> str:
    default_root = str(repo_root()).replace("\\", "/")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Job Tracker · Web</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Syne:wght@500;700;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #030714;
      --panel: #111827;
      --panel-2: #111a34;
      --border: rgba(34, 211, 238, 0.22);
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #2ee8ff;
      --accent-2: #8b5cf6;
      --good: #10b981;
      --ink: #dbeafe;
    }}
    @keyframes nebulaDriftA {{
      0% {{ transform: translate(-4%, -3%) scale(1); }}
      50% {{ transform: translate(2%, 2%) scale(1.08); }}
      100% {{ transform: translate(-4%, -3%) scale(1); }}
    }}
    @keyframes nebulaDriftB {{
      0% {{ transform: translate(3%, -2%) scale(1); }}
      50% {{ transform: translate(-3%, 3%) scale(1.1); }}
      100% {{ transform: translate(3%, -2%) scale(1); }}
    }}
    @keyframes pulseLive {{
      0% {{ box-shadow: 0 0 0 0 rgba(57, 255, 174, 0.45); }}
      70% {{ box-shadow: 0 0 0 8px rgba(57, 255, 174, 0); }}
      100% {{ box-shadow: 0 0 0 0 rgba(57, 255, 174, 0); }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      display: flex;
      flex-direction: column;
      font-family: "DM Sans", system-ui, sans-serif;
      position: relative;
      overflow-x: hidden;
      overflow-y: auto;
    }}
    .nebula {{
      position: fixed;
      inset: -14vh -12vw;
      pointer-events: none;
      z-index: 0;
    }}
    .nebula::before,
    .nebula::after {{
      content: "";
      position: absolute;
      width: 62vw;
      height: 62vw;
      border-radius: 50%;
      filter: blur(58px);
      opacity: 0.34;
    }}
    .nebula::before {{
      left: -10vw;
      top: -16vh;
      background: radial-gradient(circle, rgba(45, 212, 191, 0.42) 0%, rgba(45, 212, 191, 0) 72%);
      animation: nebulaDriftA 26s ease-in-out infinite;
    }}
    .nebula::after {{
      right: -14vw;
      top: 16vh;
      background: radial-gradient(circle, rgba(139, 92, 246, 0.4) 0%, rgba(139, 92, 246, 0) 70%);
      animation: nebulaDriftB 30s ease-in-out infinite;
    }}
    .nebula-3 {{
      position: fixed;
      right: 12vw;
      bottom: -24vh;
      width: 56vw;
      height: 56vw;
      pointer-events: none;
      border-radius: 50%;
      filter: blur(64px);
      opacity: 0.28;
      z-index: 0;
      background: radial-gradient(circle, rgba(56, 189, 248, 0.36) 0%, rgba(56, 189, 248, 0) 72%);
      animation: nebulaDriftA 34s ease-in-out infinite reverse;
    }}
    .grid-overlay {{
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: 1;
      background-image:
        linear-gradient(rgba(34, 211, 238, 0.085) 1px, transparent 1px),
        linear-gradient(90deg, rgba(34, 211, 238, 0.085) 1px, transparent 1px);
      background-size: 44px 44px;
      mask-image: radial-gradient(circle at 50% 40%, black 20%, transparent 100%);
      opacity: 0.42;
    }}
    header {{
      padding: 0.75rem 1.25rem;
      border-bottom: 1px solid var(--border);
      background: rgba(5, 10, 28, 0.85);
      color: #eef2ff;
      position: relative;
      z-index: 3;
      backdrop-filter: blur(8px);
    }}
    header h1 {{
      margin: 0;
      font-size: 1.15rem;
      font-weight: 700;
      font-family: "Syne", "DM Sans", sans-serif;
      letter-spacing: 0.02em;
    }}
    header p {{ margin: 0.35rem 0 0; font-size: 0.85rem; color: #9bc4ff; }}
    .brand-line {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.8rem;
    }}
    .brand-mark {{
      width: 38px;
      height: 38px;
      border-radius: 12px;
      display: inline-grid;
      place-items: center;
      color: #e0f2fe;
      font-weight: 800;
      font-family: "Syne", sans-serif;
      background: linear-gradient(135deg, #22d3ee 0%, #6366f1 45%, #a855f7 100%);
      box-shadow: 0 0 18px rgba(99, 102, 241, 0.5);
      flex: none;
    }}
    .brand-title {{
      display: flex;
      align-items: center;
      gap: 0.65rem;
    }}
    .live-indicator {{
      border: 1px solid rgba(57, 255, 174, 0.3);
      color: #c4ffe1;
      background: rgba(16, 185, 129, 0.12);
      border-radius: 999px;
      padding: 0.28rem 0.72rem;
      font-size: 0.78rem;
      font-weight: 700;
      font-family: "Syne", sans-serif;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
    }}
    .live-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #39ffae;
      animation: pulseLive 2.2s infinite;
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin-top: 0.65rem;
    }}
    .tabs {{
      display: inline-flex;
      border: 1px solid rgba(199,210,254,0.3);
      border-radius: 10px;
      overflow: hidden;
      background: rgba(15, 23, 42, 0.25);
    }}
    .tab-btn {{
      border: none;
      background: transparent;
      color: #dbeafe;
      padding: 0.45rem 0.85rem;
      cursor: pointer;
      font-weight: 600;
      font-size: 0.82rem;
      font-family: "Syne", "DM Sans", sans-serif;
      letter-spacing: 0.02em;
    }}
    .tab-btn.active {{
      background: rgba(99, 102, 241, 0.5);
      color: #fff;
    }}
    .hint {{
      display: none;
      border: 1px solid rgba(34, 211, 238, 0.35);
      border-radius: 10px;
      background: rgba(34, 211, 238, 0.12);
      color: #ecfeff;
      padding: 0.4rem 0.6rem;
      font-size: 0.78rem;
      max-width: 420px;
    }}
    .hint.show {{ display: block; }}
    main {{
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
      min-width: 0;
      overflow: hidden;
      position: relative;
      z-index: 2;
    }}
    .stats-strip {{
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 0.75rem;
      padding: 0.8rem 1.25rem;
      background: rgba(4, 12, 34, 0.84);
      border-bottom: 1px solid var(--border);
      backdrop-filter: blur(8px);
      position: sticky;
      top: 0;
      z-index: 3;
    }}
    .stat-card {{
      border: 1px solid rgba(99, 102, 241, 0.28);
      border-radius: 12px;
      background: linear-gradient(145deg, rgba(30,41,59,0.6), rgba(12,21,41,0.6));
      padding: 0.58rem 0.78rem;
    }}
    .stat-label {{
      font-size: 0.73rem;
      color: #9ca3af;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-family: "Syne", "DM Sans", sans-serif;
    }}
    .stat-value {{
      margin-top: 0.2rem;
      font-size: 1.65rem;
      line-height: 1;
      color: #f1f5f9;
      font-family: "Syne", "DM Sans", sans-serif;
      font-weight: 700;
    }}
    .stat-fit .stat-value {{ color: #f472b6; }}
    @media (max-width: 900px) {{
      .stats-strip {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
    }}
    .tab-content {{
      display: none;
      flex: 1;
      min-height: 0;
    }}
    .tab-content.active {{ display: flex; }}
    .pane {{
      display: flex;
      flex-direction: column;
      min-height: 0;
      border-right: 1px solid var(--border);
      background: rgba(2, 8, 25, 0.58);
      backdrop-filter: blur(8px);
    }}
    .tab-content.pane {{ display: none; }}
    .tab-content.active.pane {{ display: flex; }}
    .pane:last-child {{ border-right: none; }}
    .pane h2 {{
      margin: 0;
      padding: 0.5rem 1rem;
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #94a3b8;
      border-bottom: 1px solid var(--border);
      font-family: "Syne", "DM Sans", sans-serif;
    }}
    iframe {{ flex: 1; width: 100%; border: none; min-height: 480px; }}
    .chat-body {{ flex: 1; min-height: 0; overflow-y: auto; padding: 0.75rem 1rem; }}
    .msg {{
      margin-bottom: 0.78rem;
      font-size: 0.92rem;
      line-height: 1.5;
      display: flex;
      flex-direction: column;
      max-width: 76%;
      gap: 0.25rem;
    }}
    .msg-role {{
      font-family: "Syne", "DM Sans", sans-serif;
      font-size: 0.74rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
    }}
    .msg-bubble {{
      border-radius: 14px;
      padding: 0.72rem 0.9rem;
      border: 1px solid transparent;
      white-space: pre-wrap;
    }}
    .msg.user {{
      margin-left: auto;
      align-items: flex-end;
    }}
    .msg.user .msg-role {{
      color: #c4b5fd;
    }}
    .msg.user .msg-bubble {{
      color: #ede9fe;
      background: linear-gradient(135deg, rgba(99,102,241,0.34), rgba(168,85,247,0.32));
      border-color: rgba(167, 139, 250, 0.35);
      text-align: right;
    }}
    .msg.assistant {{
      margin-right: auto;
      align-items: flex-start;
    }}
    .msg.assistant .msg-role {{
      color: #67e8f9;
    }}
    .msg.assistant .msg-bubble {{
      color: #dff7ff;
      background: linear-gradient(135deg, rgba(6,182,212,0.2), rgba(15,23,42,0.5));
      border-color: rgba(34, 211, 238, 0.3);
    }}
    .msg.system {{ display: none; }}
    .joy {{
      border-radius: 12px;
      border: 1px solid rgba(99, 102, 241, 0.28);
      background: linear-gradient(135deg, rgba(99,102,241,.15), rgba(34,211,238,.16));
      color: #e0f2fe;
      padding: 0.55rem 0.7rem;
      margin-bottom: 0.65rem;
      font-size: 0.86rem;
      font-weight: 600;
      display: none;
    }}
    .joy.show {{ display: block; }}
    .chat-input {{
      padding: 0.65rem 1rem;
      border-top: 1px solid var(--border);
      display: flex;
      gap: 0.5rem;
      position: sticky;
      bottom: 0;
      background: rgba(3, 9, 28, 0.96);
      backdrop-filter: blur(6px);
      z-index: 2;
    }}
    .chat-input input[type="text"] {{
      flex: 1;
      padding: 0.5rem 0.65rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.76);
      color: #e2e8f0;
    }}
    .chat-input button {{
      padding: 0.5rem 1rem;
      border-radius: 8px;
      border: none;
      background: linear-gradient(120deg, var(--accent) 0%, var(--accent-2) 100%);
      color: #fff;
      font-weight: 600;
      cursor: pointer;
      font-family: "Syne", "DM Sans", sans-serif;
      box-shadow: 0 0 14px rgba(99, 102, 241, 0.45);
    }}
    .chat-input button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    aside {{
      width: 280px;
      max-width: 100%;
      background: linear-gradient(170deg, #0f172a 0%, #312e81 48%, #0e7490 100%);
      border-right: 1px solid var(--border);
      padding: 1rem 0.9rem;
      overflow-y: auto;
      font-size: 0.82rem;
      color: var(--text);
      transition: width .24s ease, padding .24s ease;
    }}
    aside.collapsed {{
      width: 56px;
      padding: 0.8rem 0.35rem;
      overflow: hidden;
    }}
    .aside-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.5rem;
    }}
    .collapse-btn {{
      border: 1px solid rgba(226, 232, 240, 0.3);
      background: rgba(15, 23, 42, 0.35);
      color: #e2e8f0;
      border-radius: 8px;
      width: 28px;
      height: 28px;
      cursor: pointer;
      font-weight: 700;
    }}
    .aside-content.hidden {{ display: none; }}
    aside label {{ display: block; margin-top: 0.65rem; color: var(--muted); }}
    aside input, aside select, aside textarea {{
      width: 100%;
      margin-top: 0.2rem;
      padding: 0.35rem 0.45rem;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: #0c1222;
      color: var(--text);
    }}
    aside textarea {{ min-height: 56px; resize: vertical; }}
    .layout {{ display: flex; flex: 1; min-height: 0; }}
    #chatPane {{
      min-height: 420px;
      max-height: calc(100vh - 260px);
      overflow: hidden;
    }}
    .warn {{ color: #fbbf24; font-size: 0.8rem; margin-top: 0.75rem; }}
    details {{ margin-top: 0.75rem; }}
    details pre {{
      font-size: 0.72rem;
      overflow: auto;
      max-height: 160px;
      background: #081327;
      padding: 0.5rem;
      border-radius: 6px;
      color: #67e8f9;
      border: 1px solid rgba(34, 211, 238, 0.2);
      font-family: "JetBrains Mono", "Consolas", monospace;
    }}
  </style>
</head>
<body>
  <div class="nebula"></div>
  <div class="nebula-3"></div>
  <div class="grid-overlay"></div>
  <header>
    <div class="brand-line">
      <div class="brand-title">
        <div class="brand-mark">JT</div>
        <div>
          <h1>Job Tracker</h1>
          <p>Your Partner in Job Hunt</p>
        </div>
      </div>
      <div class="live-indicator"><span class="live-dot"></span>Agent live</div>
    </div>
    <div class="topbar">
      <div class="tabs">
        <button id="tabChat" class="tab-btn active" type="button">Chat workspace</button>
        <button id="tabDash" class="tab-btn" type="button">Prefab UI (Full Window)</button>
      </div>
      <div id="dashHint" class="hint">Dashboard is ready. Open <b>Prefab UI (Full Window)</b> to interact with it.</div>
    </div>
  </header>
  <div class="layout">
    <aside id="leftAside">
      <div class="aside-top">
        <strong id="asideTitle">Session</strong>
        <button id="collapseAside" class="collapse-btn" type="button" aria-label="Collapse sidebar">◀</button>
      </div>
      <div id="asideContent" class="aside-content">
        <label>OpenAI API key<input id="apiKey" type="password" autocomplete="off" placeholder="or use OPENAI_API_KEY in .env"/></label>
        <label>Model
          <select id="model">
            <option>gpt-4o-mini</option>
            <option>gpt-4o</option>
            <option>gpt-4.1-mini</option>
            <option>gpt-4.1</option>
          </select>
        </label>
        <label>MCP / data cwd<input id="projectRoot" type="text" value="{default_root}"/></label>
        <label>Max tool rounds<input id="maxSteps" type="number" min="2" max="64" value="14"/></label>
        <details open>
          <summary><strong>Profile</strong></summary>
          <label>Skills &amp; stack<textarea id="pfSkills"></textarea></label>
          <label>Experience<textarea id="pfExp"></textarea></label>
          <label>Company preferences<textarea id="pfCo"></textarea></label>
          <button type="button" id="saveProfile" style="margin-top:0.6rem;width:100%;padding:0.45rem;border-radius:8px;border:none;background:#6366f1;color:#fff;font-weight:600;cursor:pointer;">Save profile</button>
        </details>
        <details style="margin-top:1rem;">
          <summary>Tool trace (last turn)</summary>
          <pre id="traceBox">—</pre>
        </details>
      </div>
    </aside>
    <main>
      <div class="stats-strip">
        <div class="stat-card">
          <div class="stat-label">Applied</div>
          <div class="stat-value" id="statApplied">0</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Interviews</div>
          <div class="stat-value" id="statInterviews">0</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Offers</div>
          <div class="stat-value" id="statOffers">0</div>
        </div>
        <div class="stat-card stat-fit">
          <div class="stat-label">Fit avg</div>
          <div class="stat-value" id="statFitAvg">0%</div>
        </div>
      </div>
      <section id="chatPane" class="tab-content active pane">
        <h2>Chat</h2>
        <div class="chat-body" id="chatLog">
          <div id="joyCard" class="joy">✨ Building your result... gathering data, matching jobs, and preparing your next best move.</div>
        </div>
        <form class="chat-input" id="chatForm">
          <input type="text" id="chatText" placeholder="Ask about your tracker…" autocomplete="off"/>
          <button type="submit" id="sendBtn">Send</button>
        </form>
      </section>
      <section id="dashPane" class="tab-content pane">
        <h2>Prefab dashboard <span style="font-weight:400;text-transform:none;color:#6b7280;">· filter</span>
          <select id="dashFilter" style="float:right;max-width:130px;font-size:0.75rem;">
            <option>All</option>
            <option>Applied</option>
            <option>Screen</option>
            <option>Interview</option>
            <option>Offer</option>
            <option>Rejected</option>
            <option>Withdrawn</option>
          </select>
        </h2>
        <iframe id="dashFrame" title="Prefab dashboard" src="/prefab/dashboard.html?filter=All"></iframe>
      </section>
    </main>
  </div>
  <script>
    const state = {{ messages: [] }};
    const tabChat = document.getElementById("tabChat");
    const tabDash = document.getElementById("tabDash");
    const chatPane = document.getElementById("chatPane");
    const dashPane = document.getElementById("dashPane");
    const dashHint = document.getElementById("dashHint");
    const joyCard = document.getElementById("joyCard");
    const leftAside = document.getElementById("leftAside");
    const asideContent = document.getElementById("asideContent");
    const collapseAside = document.getElementById("collapseAside");

    function setTab(tab) {{
      const onChat = tab === "chat";
      tabChat.classList.toggle("active", onChat);
      tabDash.classList.toggle("active", !onChat);
      chatPane.classList.toggle("active", onChat);
      dashPane.classList.toggle("active", !onChat);
      chatPane.style.display = onChat ? "flex" : "none";
      dashPane.style.display = onChat ? "none" : "flex";
      if (!onChat) {{
        dashHint.classList.remove("show");
      }}
    }}

    tabChat.addEventListener("click", () => setTab("chat"));
    tabDash.addEventListener("click", () => setTab("dash"));

    collapseAside.addEventListener("click", () => {{
      const collapsed = leftAside.classList.toggle("collapsed");
      asideContent.classList.toggle("hidden", collapsed);
      collapseAside.textContent = collapsed ? "▶" : "◀";
      collapseAside.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    }});

    function renderChat() {{
      const el = document.getElementById("chatLog");
      el.innerHTML = "";
      el.appendChild(joyCard);
      for (const m of state.messages) {{
        if (m.role === "system" || m.role === "tool") continue;
        const div = document.createElement("div");
        div.className = "msg " + m.role;
        let text = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
        const role = document.createElement("div");
        role.className = "msg-role";
        role.textContent = m.role === "assistant" ? "Agent" : "You";
        const bubble = document.createElement("div");
        bubble.className = "msg-bubble";
        bubble.textContent = text;
        div.appendChild(role);
        div.appendChild(bubble);
        el.appendChild(div);
      }}
      el.scrollTop = el.scrollHeight;
    }}

    async function refreshStats() {{
      try {{
        const r = await fetch("/api/stats");
        if (!r.ok) return;
        const s = await r.json();
        document.getElementById("statApplied").textContent = String(s.applied ?? 0);
        document.getElementById("statInterviews").textContent = String(s.interviews ?? 0);
        document.getElementById("statOffers").textContent = String(s.offers ?? 0);
        document.getElementById("statFitAvg").textContent = `${{Math.round(Number(s.fit_avg ?? 0))}}%`;
      }} catch (_err) {{
      }}
    }}

    function refreshIframe() {{
      const f = document.getElementById("dashFilter").value;
      const iframe = document.getElementById("dashFrame");
      const u = new URL("/prefab/dashboard.html", window.location.origin);
      u.searchParams.set("filter", f);
      u.searchParams.set("t", String(Date.now()));
      iframe.src = u.toString();
    }}

    document.getElementById("dashFilter").addEventListener("change", refreshIframe);

    async function loadProfile() {{
      const r = await fetch("/api/profile");
      const p = await r.json();
      document.getElementById("pfSkills").value = p.skills || "";
      document.getElementById("pfExp").value = p.experience || "";
      document.getElementById("pfCo").value = p.company_preferences || "";
    }}

    document.getElementById("saveProfile").addEventListener("click", async () => {{
      const body = {{
        skills: document.getElementById("pfSkills").value,
        experience: document.getElementById("pfExp").value,
        company_preferences: document.getElementById("pfCo").value,
      }};
      const r = await fetch("/api/profile", {{ method: "PUT", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(body) }});
      if (!r.ok) alert("Save failed");
    }});

    document.getElementById("chatForm").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const input = document.getElementById("chatText");
      const text = input.value.trim();
      if (!text) return;
      const btn = document.getElementById("sendBtn");
      btn.disabled = true;
      joyCard.classList.add("show");
      input.value = "";
      try {{
        const res = await fetch("/api/chat", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            messages: state.messages,
            user_message: text,
            model: document.getElementById("model").value,
            project_root: document.getElementById("projectRoot").value || null,
            max_steps: parseInt(document.getElementById("maxSteps").value, 10),
            api_key: document.getElementById("apiKey").value || null,
          }}),
        }});
        const data = await res.json();
        if (!res.ok) {{
          alert(data.detail || JSON.stringify(data));
          return;
        }}
        state.messages = data.messages;
        document.getElementById("traceBox").textContent = data.trace && data.trace.length
          ? JSON.stringify(data.trace, null, 2)
          : "—";
        const openedDashboard = (data.trace || []).some((x) => x && x.name === "push_dashboard");
        if (openedDashboard) {{
          dashHint.classList.add("show");
        }}
        if (data.error && data.error !== "max_steps") {{
          alert("Error: " + data.error);
        }}
      }} catch (err) {{
        alert(String(err));
      }} finally {{
        btn.disabled = false;
        joyCard.classList.remove("show");
        renderChat();
        refreshIframe();
        refreshStats();
      }}
    }});

    loadProfile();
    refreshIframe();
    refreshStats();
    setTab("chat");
    renderChat();
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(content=_index_html(), media_type="text/html; charset=utf-8")
