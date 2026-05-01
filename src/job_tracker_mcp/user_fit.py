"""Profile–job fit: same keyword/Jaccard demo as resume scoring, computed when jobs are returned."""

from __future__ import annotations

from typing import Any

from job_tracker_mcp.scoring import score_resume_against_text
from job_tracker_mcp.user_profile import profile_for_fit_text, profile_is_blank


def job_text_for_fit(job: dict[str, Any]) -> str:
    parts = [
        str(job.get("title", "") or ""),
        str(job.get("company", "") or ""),
        str(job.get("jd_text", "") or ""),
        str(job.get("jd_summary", "") or ""),
        str(job.get("description", "") or ""),
    ]
    return "\n".join(parts)


def enrich_job_with_user_fit(job: dict[str, Any], *, profile_blob: str | None = None) -> dict[str, Any]:
    out = dict(job)
    blob = profile_blob if profile_blob is not None else profile_for_fit_text()
    if not blob.strip():
        out["user_fit_percent"] = None
        out["user_fit_summary"] = (
            "Save your profile (skills, experience, preferences) in the Streamlit sidebar to see fit."
        )
        return out
    pct = score_resume_against_text(blob, job_text_for_fit(out))
    out["user_fit_percent"] = pct
    out["user_fit_summary"] = (
        f"Profile match (keyword overlap vs title, company, and description): about {pct}%."
    )
    return out


def enrich_jobs_with_user_fit(
    jobs: list[dict[str, Any]],
    *,
    profile_blob: str | None = None,
) -> list[dict[str, Any]]:
    if not jobs:
        return []
    blob: str | None
    if profile_blob is not None:
        blob = profile_blob
    else:
        blob = profile_for_fit_text() if not profile_is_blank() else ""

    if not (blob or "").strip():
        return [enrich_job_with_user_fit(dict(j), profile_blob="") for j in jobs]
    return [enrich_job_with_user_fit(dict(j), profile_blob=blob) for j in jobs]
