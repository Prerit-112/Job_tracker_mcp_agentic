"""FastMCP entrypoint: seven tools + optional agent-style tracing on stderr."""

from __future__ import annotations

from typing import Any, Literal

from dotenv import load_dotenv
from fastmcp import FastMCP
from prefab_ui.app import PrefabApp

from job_tracker_mcp.adzuna import search_jobs as adzuna_search
from job_tracker_mcp.agent_trace import trace_tool_end, trace_tool_start
from job_tracker_mcp.dashboard import build_prefab_dashboard
from job_tracker_mcp.deadlines import check_deadlines as compute_deadlines
from job_tracker_mcp.followup import draft_followup_for_job
from job_tracker_mcp.intel import get_company_intel as fetch_intel
from job_tracker_mcp.scoring import read_resume, score_resume_detail
from job_tracker_mcp.storage import crud_tracker as storage_crud
from job_tracker_mcp.storage import load_jobs_raw
from job_tracker_mcp.user_fit import enrich_jobs_with_user_fit, enrich_job_with_user_fit
from job_tracker_mcp.user_profile import profile_for_fit_text

load_dotenv()


def _enrich_crud_payload(out: dict[str, Any]) -> dict[str, Any]:
    if not out.get("ok"):
        return out
    if "jobs" in out:
        return {**out, "jobs": enrich_jobs_with_user_fit(list(out["jobs"]))}
    if "job" in out:
        return {**out, "job": enrich_job_with_user_fit(dict(out["job"]))}
    return out


def _auto_resume_match_for_job(job: dict[str, Any]) -> dict[str, Any]:
    """Compute and persist fit_percent for sourced/saved jobs when JD + resume are available."""
    jid = str(job.get("id") or "")
    jd_text = str(job.get("jd_text") or job.get("jd_summary") or "").strip()
    if not jid or not jd_text:
        return job
    resume = read_resume(None)
    if not (resume or "").strip():
        resume = profile_for_fit_text()
    if not (resume or "").strip():
        return job
    detail = score_resume_detail(resume, jd_text, mode="auto")
    pct = float(detail.get("fit_percent") or 0.0)
    storage_crud("update", {"id": jid, "fit_percent": pct})
    fresh = storage_crud("get", {"id": jid})
    if fresh.get("ok") and isinstance(fresh.get("job"), dict):
        return dict(fresh["job"])
    return {**job, "fit_percent": pct}


mcp = FastMCP(
    "Job application tracker",
    instructions=(
        "This server tracks job applications in local jobs.json, searches Adzuna (or offline stubs), "
        "scores resume fit (semantic LLM when OPENAI_API_KEY is set, else keyword overlap), checks staleness, drafts follow-ups, "
        "fetches company intel, and can push a Prefab dashboard (push_dashboard). "
        "When data/user_profile.json is filled (via the Streamlit client), job payloads include user_fit_percent "
        "and user_fit_summary (profile-vs-role overlap). "
        "For a full demo, use the Tier-1 prompt in ASSIGNMENT_PROMPTS.txt."
    ),
)

CrudOp = Literal["list", "get", "create", "update", "delete"]


@mcp.tool
def search_jobs(
    query: str,
    location: str | None = None,
    company: str | None = None,
) -> dict[str, Any]:
    """Search jobs via Adzuna API (or offline fixtures). Does not write to disk."""
    trace_tool_start("search_jobs", query=query, location=location, company=company)
    try:
        out = adzuna_search(query, location=location, company=company)
        if out.get("results"):
            out = {**out, "results": enrich_jobs_with_user_fit(list(out["results"]))}
        trace_tool_end("search_jobs", result={"source": out.get("source"), "count": len(out.get("results", []))})
        return out
    except Exception as e:  # noqa: BLE001
        trace_tool_end("search_jobs", error=e)
        raise


@mcp.tool
def get_company_intel(company: str) -> dict[str, Any]:
    """Structured company intel (mock or Wikipedia via COMPANY_INTEL_MODE)."""
    trace_tool_start("get_company_intel", company=company)
    try:
        out = fetch_intel(company)
        trace_tool_end("get_company_intel", result={"ok": out.get("ok"), "mode": out.get("mode")})
        return out
    except Exception as e:  # noqa: BLE001
        trace_tool_end("get_company_intel", error=e)
        raise


@mcp.tool
def crud_tracker(operation: CrudOp, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Local JSON tracker: list | get | create | update | delete on data/jobs.json."""
    trace_tool_start("crud_tracker", operation=operation, payload=payload)
    try:
        out_raw = storage_crud(operation, payload)
        if out_raw.get("ok") and operation in ("create", "update") and isinstance(out_raw.get("job"), dict):
            out_raw = {**out_raw, "job": _auto_resume_match_for_job(dict(out_raw["job"]))}
        out = _enrich_crud_payload(out_raw)
        trace_tool_end("crud_tracker", result={"ok": out.get("ok"), "operation": operation})
        return out
    except Exception as e:  # noqa: BLE001
        trace_tool_end("crud_tracker", error=e)
        raise


@mcp.tool
def check_deadlines() -> dict[str, Any]:
    """Attach days_since_applied + stale_tier (neutral / amber / red) to each job."""
    trace_tool_start("check_deadlines")
    try:
        jobs = load_jobs_raw().get("jobs", [])
        out = compute_deadlines(jobs)
        if out.get("jobs"):
            out = {**out, "jobs": enrich_jobs_with_user_fit(list(out["jobs"]))}
        trace_tool_end(
            "check_deadlines",
            result={"jobs": len(out.get("jobs", [])), "flags": out.get("flags")},
        )
        return out
    except Exception as e:  # noqa: BLE001
        trace_tool_end("check_deadlines", error=e)
        raise


@mcp.tool
def score_resume_fit(
    job_id: str | None = None,
    jd_text: str | None = None,
    resume_path: str | None = None,
) -> dict[str, Any]:
    """Semantic resume/profile vs JD fit (0-100); persists fit_percent when job_id is set."""
    trace_tool_start("score_resume_fit", job_id=job_id, jd_text_len=len(jd_text or ""), resume_path=resume_path)
    try:
        resume = read_resume(resume_path)
        if not resume.strip():
            resume = profile_for_fit_text()
        if not resume.strip():
            raise ValueError(
                "Resume/profile is empty — add text to data/resume.txt, pass resume_path, or fill data/user_profile.json."
            )

        text = jd_text
        if job_id:
            got = _enrich_crud_payload(storage_crud("get", {"id": job_id}))
            if not got.get("ok"):
                raise ValueError(got.get("error", "job not found"))
            job = got["job"]
            text = text or str(job.get("jd_text") or job.get("jd_summary") or "")
        if not (text or "").strip():
            raise ValueError("No JD text — pass jd_text or use a job with jd_text/jd_summary.")

        detail = score_resume_detail(resume, text, mode="llm")
        pct = float(detail["fit_percent"])
        out: dict[str, Any] = {
            "ok": True,
            "fit_percent": pct,
            "job_id": job_id,
            "scoring_mode": detail.get("mode_used"),
        }
        if detail.get("jd_skills"):
            out["jd_skills_extracted"] = detail["jd_skills"]
        if detail.get("candidate_skills"):
            out["candidate_skills_extracted"] = detail["candidate_skills"]
        if detail.get("rationale"):
            out["fit_rationale"] = detail["rationale"]
        if detail.get("fallback_reason"):
            out["scoring_fallback_reason"] = detail["fallback_reason"]
        if job_id:
            storage_crud("update", {"id": job_id, "fit_percent": pct})
        trace_tool_end("score_resume_fit", result=out)
        return out
    except Exception as e:  # noqa: BLE001
        trace_tool_end("score_resume_fit", error=e)
        raise


@mcp.tool
def draft_followup(job_id: str, tone: str | None = None) -> dict[str, Any]:
    """Deterministic follow-up draft you can paste into email."""
    trace_tool_start("draft_followup", job_id=job_id, tone=tone)
    try:
        got = _enrich_crud_payload(storage_crud("get", {"id": job_id}))
        if not got.get("ok"):
            raise ValueError(got.get("error", "job not found"))
        job = got["job"]
        body_payload = draft_followup_for_job(job, tone=tone)
        body = str(body_payload["markdown"])
        out = {"ok": True, "job_id": job_id, "markdown": body, "tone": body_payload["tone"]}
        trace_tool_end("draft_followup", result={"ok": True, "chars": len(body)})
        return out
    except Exception as e:  # noqa: BLE001
        trace_tool_end("draft_followup", error=e)
        raise


@mcp.tool(app=True)
def push_dashboard(
    default_filter: str = "All",
    jobs: list[dict[str, Any]] | None = None,
) -> PrefabApp:
    """Prefab UI: metrics, status filters (CallTool refresh), DataTable with SendMessage actions."""
    trace_tool_start("push_dashboard", default_filter=default_filter, jobs_custom=jobs is not None)
    try:
        app = build_prefab_dashboard(default_filter, jobs)
        filt = (default_filter or "All").strip()
        rows = int((app.state or {}).get("row_count", 0))
        trace_tool_end(
            "push_dashboard",
            result={"title": app.title, "rows": rows, "filter": filt},
        )
        return app
    except Exception as e:  # noqa: BLE001
        trace_tool_end("push_dashboard", error=e)
        raise


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
