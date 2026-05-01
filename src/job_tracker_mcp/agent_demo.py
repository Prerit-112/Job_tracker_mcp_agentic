"""Runnable Tier-1-style pipeline with narrator logs (no MCP host required)."""

from __future__ import annotations

import logging
import os
import sys
from datetime import date

from job_tracker_mcp.adzuna import search_jobs as adzuna_search
from job_tracker_mcp.agent_trace import agent_step, configure_agent_logging
from job_tracker_mcp.dashboard import build_dashboard_app
from job_tracker_mcp.deadlines import check_deadlines as compute_deadlines
from job_tracker_mcp.followup import draft_followup_for_job
from job_tracker_mcp.intel import get_company_intel
from job_tracker_mcp.paths import data_dir
from job_tracker_mcp.scoring import read_resume, score_resume_against_text
from job_tracker_mcp.storage import atomic_save, crud_tracker, load_jobs_raw

os.environ["JOB_TRACKER_AGENT_LOG"] = "1"
configure_agent_logging()
log = logging.getLogger("job_tracker_mcp.agent_demo")


def run_scenario() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    atomic_save({"jobs": []})

    step = 1
    agent_step(step, "Goal: reproduce assignment Tier-1 flow with observable tool traces", None)
    step += 1

    agent_step(step, "Planner: discover roles matching Zomato + software", "search_jobs")
    step += 1
    sr = adzuna_search("software engineer", company="Zomato")
    log.info("search_jobs snapshot: %s", sr.get("message") or "ok")

    pick = (sr.get("results") or [{}])[0]
    agent_step(step, "Planner: persist one listing as Applied (today)", "crud_tracker")
    step += 1
    cr = crud_tracker(
        "create",
        {
            "company": pick.get("company") or "Zomato",
            "title": pick.get("title") or "Software Engineer",
            "source_url": pick.get("source_url") or "https://example.com",
            "status": "Applied",
            "applied_date": date.today().isoformat(),
            "jd_summary": pick.get("jd_summary") or "Demo JD: Python, distributed systems.",
            "jd_text": pick.get("jd_summary") or "Demo JD: Python, distributed systems.",
        },
    )
    jid = str(cr["job"]["id"])
    log.info("created job id=%s", jid)

    agent_step(step, "Planner: quantify pipeline staleness heat", "check_deadlines")
    step += 1
    deadlines = compute_deadlines(load_jobs_raw().get("jobs", []))
    log.info("deadline flags=%s", deadlines.get("flags"))

    agent_step(step, "Planner: compute resume vs JD overlap score", "score_resume_fit")
    step += 1
    resume_txt = read_resume()
    job_row = next(j for j in load_jobs_raw()["jobs"] if str(j["id"]) == jid)
    pct = score_resume_against_text(
        resume_txt,
        str(job_row.get("jd_text") or job_row.get("jd_summary") or ""),
        mode="auto",
    )
    crud_tracker("update", {"id": jid, "fit_percent": pct})
    log.info("fit_percent=%s (persisted)", pct)

    agent_step(step, "Planner: produce outreach draft", "draft_followup")
    step += 1
    fu = draft_followup_for_job(job_row, tone="brief")
    log.info("follow-up chars=%s", len(str(fu.get("markdown", ""))))

    agent_step(step, "Planner: enrich employer context", "get_company_intel")
    step += 1
    intel = get_company_intel(str(job_row.get("company", "Zomato")))
    crud_tracker(
        "update",
        {
            "id": jid,
            "intel_snippet": str(intel.get("snippet") or "")[:500],
            "intel_rating": str(intel.get("rating") or ""),
        },
    )
    log.info("intel mode=%s", intel.get("mode"))

    agent_step(step, "Planner: render Prefab dashboard (Applied filter)", "push_dashboard")
    step += 1
    jobs = load_jobs_raw().get("jobs", [])
    dl = compute_deadlines(jobs)
    enriched_by_id = {str(j.get("id")): j for j in dl["jobs"]}
    merged: list[dict] = []
    for j in jobs:
        base = dict(j)
        base.update(enriched_by_id.get(str(j.get("id")), {}))
        merged.append(base)

    def cnt(status: str) -> int:
        return sum(1 for j in dl["jobs"] if str(j.get("status")) == status)

    metrics = {
        "total": len(dl["jobs"]),
        "applied": cnt("Applied"),
        "screen": cnt("Screen"),
        "interview": cnt("Interview"),
        "offer": cnt("Offer"),
        "rejected": cnt("Rejected"),
        "withdrawn": cnt("Withdrawn"),
        "stale_red": int(dl["flags"].get("stale_red", 0)),
    }
    filtered = [j for j in merged if str(j.get("status")) == "Applied"]
    app = build_dashboard_app("Applied", filtered, metrics)
    log.info(
        "push_dashboard built PrefabApp title=%r state_keys=%s",
        getattr(app, "title", None),
        list((getattr(app, "state", None) or {}).keys()),
    )

    agent_step(step, "Done: all seven tools exercised; stderr shows MCP-style traces when the server runs.", None)
    print(
        "---\nagent_demo finished. Re-run with: python -m job_tracker_mcp.agent_demo\n---",
        file=sys.stderr,
    )


if __name__ == "__main__":
    run_scenario()
