"""Streamlit UI: chat + LLM agent that calls the local job_tracker_mcp server."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from dotenv import load_dotenv
from streamlit import config as st_config

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from client.agent import run_agent_turn
from client.mcp_utils import compose_system_instruction, humanize_assistant_reply
from job_tracker_mcp.dashboard import build_prefab_dashboard
from job_tracker_mcp.storage import load_jobs_raw
from job_tracker_mcp.user_fit import enrich_jobs_with_user_fit
from job_tracker_mcp.user_profile import (
    load_user_profile,
    profile_is_blank,
    save_user_profile,
)

CLIENT_DIR = Path(__file__).resolve().parent
PREFAB_HTML_PATH = CLIENT_DIR / "static" / "prefab_dashboard.html"


def _write_prefab_html_file(html: str) -> None:
    """Write dashboard HTML next to this script for /app/static/ serving."""
    PREFAB_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PREFAB_HTML_PATH.with_suffix(PREFAB_HTML_PATH.suffix + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(PREFAB_HTML_PATH)


def _session_profile_dict() -> dict[str, str]:
    return {
        "skills": str(st.session_state.get("profile_skills", "") or ""),
        "experience": str(st.session_state.get("profile_experience", "") or ""),
        "company_preferences": str(st.session_state.get("profile_company_preferences", "") or ""),
    }


def _markdown_jobs_table(rows: list[dict[str, Any]]) -> None:
    """Render rows as a markdown table (no PyArrow — works when DLLs are blocked by App Control)."""
    if not rows:
        return
    keys = list(rows[0].keys())

    def cell(v: Any) -> str:
        if v is None:
            s = ""
        else:
            s = str(v)
        return s.replace("|", "·").replace("\n", " ").strip()

    header = "| " + " | ".join(keys) + " |"
    sep = "| " + " | ".join("---" for _ in keys) + " |"
    body_lines = ["| " + " | ".join(cell(r.get(k)) for k in keys) + " |" for r in rows]
    st.markdown("\n".join([header, sep, *body_lines]))


def _trace_has_dashboard_push(trace: list[Any]) -> bool:
    return any(getattr(e, "name", "") == "push_dashboard" for e in trace)


def _embed_prefab_dashboard(html_page: str) -> None:
    """Embed Prefab: same-origin URL first, then optional inline st.html, plus a native table fallback."""
    _write_prefab_html_file(html_page)
    digest = hashlib.sha256(html_page.encode("utf-8")).hexdigest()[:16]
    rel = f"/app/static/prefab_dashboard.html?h={digest}"

    iframe_src = rel
    try:
        ctx_url = getattr(st.context, "url", None)
        if ctx_url:
            pr = urlparse(str(ctx_url))
            if pr.scheme and pr.netloc:
                iframe_src = f"{pr.scheme}://{pr.netloc}{rel}"
    except Exception:
        pass

    use_static = bool(st_config.get_option("server.enableStaticServing"))
    if not use_static:
        st.warning(
            "Turn on static serving in `.streamlit/config.toml` (`enableStaticServing = true`) so the "
            "Prefab iframe can load from `/app/static/`. Fallbacks below still work."
        )

    st.caption(
        "If the frame stays empty (browser security or corporate proxy), use **inline dashboard** or the "
        "**Streamlit table**."
    )

    try:
        st.iframe(iframe_src, width="stretch", height=920)
    except Exception as exc:
        st.error(f"Could not render iframe: {exc}")

    with st.expander("Inline dashboard (JavaScript — use if iframe is blank)", expanded=False):
        try:
            st.html(html_page, unsafe_allow_javascript=True)
        except Exception as exc:
            st.caption(f"Inline Prefab failed: {exc}")

    st.download_button(
        label="Download dashboard as HTML",
        data=html_page,
        file_name="job_tracker_prefab.html",
        mime="text/html",
        key="download_prefab_html",
    )


st.set_page_config(
    page_title="Job Tracker · Agent",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,600;0,9..40,700;1,9..40,400&family=JetBrains+Mono:wght@400;500&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    .block-container { padding-top: 1.1rem; max-width: 100%; padding-left: 1.6rem; padding-right: 1.6rem; }
    [data-testid="stAppViewContainer"] {
        background: radial-gradient(circle at 15% 10%, #ede9fe 0%, transparent 36%),
                    radial-gradient(circle at 82% 8%, #cffafe 0%, transparent 38%),
                    linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(165deg, #0f172a 0%, #312e81 52%, #0e7490 100%);
        border-right: 1px solid rgba(165, 180, 252, 0.28);
    }
    [data-testid="stSidebar"] * { color: #e2e8f0 !important; }
    [data-testid="stSidebar"] .stTextInput label, [data-testid="stSidebar"] label { color: #94a3b8 !important; }
    div[data-testid="stVerticalBlockBorderWrapper"] > div {
        border-radius: 14px;
        border: 1px solid rgba(148, 163, 184, 0.2);
        background: rgba(15, 23, 42, 0.35);
    }
    .hero {
        background: linear-gradient(115deg, #06b6d4 0%, #4f46e5 38%, #7c3aed 72%, #ec4899 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        font-weight: 700;
        font-size: 1.85rem;
        letter-spacing: -0.03em;
        margin-bottom: 0.25rem;
    }
    .subtle { color: #64748b; font-size: 0.95rem; }
    .joy-card {
        border-radius: 14px;
        background: linear-gradient(135deg, rgba(99,102,241,.15), rgba(6,182,212,.15));
        border: 1px solid rgba(99,102,241,.25);
        padding: 0.8rem 0.95rem;
        color: #312e81;
        font-weight: 600;
    }
    .dashboard-hint {
        border-radius: 12px;
        padding: .65rem .8rem;
        background: rgba(37, 99, 235, 0.1);
        border: 1px solid rgba(37, 99, 235, 0.22);
        color: #1e3a8a;
        font-size: .93rem;
    }
</style>
""",
    unsafe_allow_html=True,
)

if "profile_skills" not in st.session_state:
    _prof = load_user_profile()
    st.session_state.profile_skills = _prof["skills"]
    st.session_state.profile_experience = _prof["experience"]
    st.session_state.profile_company_preferences = _prof["company_preferences"]

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": compose_system_instruction(load_user_profile())}]
if "last_trace" not in st.session_state:
    st.session_state.last_trace = []
if "show_prefab_hint" not in st.session_state:
    st.session_state.show_prefab_hint = False


with st.sidebar:
    st.markdown("### Your profile")
    with st.expander("Edit profile details", expanded=True):
        st.caption("Share your skills, experience, and ideal company preferences.")
        st.text_area(
            "Skills & stack",
            key="profile_skills",
            height=90,
            placeholder="e.g. Python, LLMs, data pipelines, TypeScript…",
        )
        st.text_area(
            "Experience & seniority",
            key="profile_experience",
            height=90,
            placeholder="Years, domains, titles, industries…",
        )
        st.text_area(
            "Company & culture fit",
            key="profile_company_preferences",
            height=72,
            placeholder="e.g. remote-first, early startup, clear work-life boundaries…",
        )
        if st.button("Save profile", type="primary"):
            save_user_profile(_session_profile_dict())
            st.session_state.messages[0] = {"role": "system", "content": compose_system_instruction(_session_profile_dict())}
            st.success("Saved. Profile fit scoring will use this context on future tool calls.")
            st.rerun()

    st.markdown("---")
    st.markdown("### Agent & MCP")
    api_key = st.text_input(
        "OpenAI API key",
        type="password",
        value="",
        help="Or set OPENAI_API_KEY in .env at the project root.",
        placeholder="sk-...",
    )
    env_key = os.environ.get("OPENAI_API_KEY", "")
    effective_key = api_key.strip() or env_key

    model = st.selectbox(
        "Model",
        ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
        index=0,
    )
    project_root = st.text_input(
        "MCP server cwd (repo root)",
        value=str(ROOT),
        help="Must contain data/jobs.json and the installed package.",
    )
    max_steps = st.slider("Max tool rounds / turn", 2, 24, 14)
    st.caption(
        "Each chat turn spawns `python -m job_tracker_mcp.server` and runs tools until the model answers."
    )
    if st.button("Clear conversation"):
        st.session_state.messages = [{"role": "system", "content": compose_system_instruction(_session_profile_dict())}]
        st.session_state.last_trace = []
        st.rerun()

st.markdown('<p class="hero">Job Tracker · MCP Agent</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="subtle">Chat runs tools on your tracker; use the <b>Prefab UI (Full Window)</b> tab for the interactive dashboard.</p>',
    unsafe_allow_html=True,
)

if profile_is_blank(_session_profile_dict()):
    st.info(
        "**Welcome — start with your profile** in the sidebar (skills, experience, company preferences), "
        "then click **Save profile**. Profile-based match scoring applies automatically whenever jobs are loaded."
    )

if st.session_state.show_prefab_hint:
    st.markdown(
        '<div class="dashboard-hint">Your dashboard is ready. Open <b>Prefab UI (Full Window)</b> tab to view and interact with it.</div>',
        unsafe_allow_html=True,
    )

tab_chat, tab_trace, tab_prefab = st.tabs(["Chat", "Tool trace", "Prefab UI (Full Window)"])

with tab_chat:
    for m in st.session_state.messages:
        if m["role"] in ("system", "tool"):
            continue
        role = m["role"]
        with st.chat_message(role):
            raw = m.get("content") or " "
            shown = humanize_assistant_reply(raw) if role == "assistant" else raw
            st.markdown(shown)

    if st.session_state.messages:
        st.session_state.messages[0] = {
            "role": "system",
            "content": compose_system_instruction(_session_profile_dict()),
        }

    prompt = st.chat_input("What do you want to do with your job search?")
    if prompt:
        if not effective_key:
            st.error("Add an OpenAI API key in the sidebar or set OPENAI_API_KEY in `.env`.")
        else:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                joy = st.empty()
                joy.markdown(
                    '<div class="joy-card">✨ Building your result... pulling data, scoring fit, and crafting the best next step.</div>',
                    unsafe_allow_html=True,
                )
                with st.spinner("Spawning MCP server & reasoning..."):
                    result = asyncio.run(
                        run_agent_turn(
                            st.session_state.messages[:-1],
                            prompt,
                            project_root=Path(project_root),
                            api_key=effective_key,
                            model=model,
                            max_steps=max_steps,
                        )
                    )
                joy.empty()
                st.session_state.messages = result.messages
                st.session_state.last_trace = result.trace
                st.session_state.show_prefab_hint = _trace_has_dashboard_push(result.trace)
                if result.error and result.error != "max_steps":
                    st.error(f"MCP / agent error: {result.error}")
                if result.assistant_text:
                    st.markdown(humanize_assistant_reply(result.assistant_text))
                elif result.error == "max_steps":
                    st.warning(humanize_assistant_reply(result.messages[-1].get("content", "")))

with tab_trace:
    st.markdown("#### Tool trace")
    st.caption("Technical detail for debugging. The Chat tab stays plain-language only.")
    if st.session_state.last_trace:
        for i, e in enumerate(st.session_state.last_trace, 1):
            with st.expander(f"{i}. `{e.name}`", expanded=i == len(st.session_state.last_trace)):
                st.write("**Arguments**")
                st.code(repr(e.arguments), language=None)
                st.write("**Result preview**")
                st.code(e.result_preview, language=None)
    else:
        st.info("Run a chat turn to see MCP tool calls here.")

with tab_prefab:
    st.markdown("#### Prefab dashboard")
    st.caption(
        "Dedicated full-window area for the interactive dashboard."
    )
    filt_opts = ["All", "Applied", "Screen", "Interview", "Offer", "Rejected", "Withdrawn"]
    dash_filt = st.selectbox("Status filter (refreshes embed)", filt_opts, key="prefab_dash_filter")

    try:
        app = build_prefab_dashboard(dash_filt, None)
        html_page = app.html()
        _embed_prefab_dashboard(html_page)
    except Exception as e:
        st.exception(e)

    st.subheader("Quick table fallback")
    jobs = load_jobs_raw().get("jobs", [])
    enriched = enrich_jobs_with_user_fit([dict(j) for j in jobs]) if jobs else []
    slim = [
        {
            "Company": j.get("company", ""),
            "Title": j.get("title", ""),
            "Status": j.get("status", ""),
            "Resume %": j.get("fit_percent"),
            "Summary": (j.get("user_fit_summary") or "")[:120],
        }
        for j in enriched
    ]
    if slim:
        _markdown_jobs_table(slim)
    else:
        st.caption("No jobs in `data/jobs.json` yet.")

    if jobs:
        with st.expander("Copy-paste prompts for Chat (replaces Prefab row buttons here)"):
            pick = st.selectbox(
                "Job",
                enriched,
                format_func=lambda j: f"{j.get('company', '')} — {j.get('title', '')} (`{j.get('id', '')}`)",
            )
            jid = str(pick.get("id", ""))
            c, t = str(pick.get("company", "")), str(pick.get("title", ""))
            uf = pick.get("user_fit_percent")
            uline = f" My profile fit for this role is about {uf}%." if uf is not None else ""
            st.code(
                f"I applied at {c} for {t} (job id `{jid}`).{uline} Draft a concise follow-up email.",
                language="text",
            )
            st.code(f"Refresh company intel for {c} and summarize highlights.", language="text")

