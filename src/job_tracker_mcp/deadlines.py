"""Application staleness: days since applied + heat tier (tool owns the signal)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


def _parse_iso(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(d[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def heat_tier(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days <= 7:
        return "neutral"
    if days <= 14:
        return "amber"
    return "red"


def days_since_applied(applied: str | None, today: date | None = None) -> int | None:
    today = today or date.today()
    ad = _parse_iso(applied)
    if not ad:
        return None
    return (today - ad).days


def enrich_job(job: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    out = dict(job)
    d = days_since_applied(str(out.get("applied_date", "")), today=today)
    tier = heat_tier(d)
    out["days_since_applied"] = d
    out["stale_tier"] = tier
    return out


def check_deadlines(jobs: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    enriched = [enrich_job(j, today=today) for j in jobs]
    flags = {"stale_red": 0, "stale_amber": 0, "stale_neutral": 0, "unknown": 0}
    for j in enriched:
        t = j.get("stale_tier") or "unknown"
        if t == "red":
            flags["stale_red"] += 1
        elif t == "amber":
            flags["stale_amber"] += 1
        elif t == "neutral":
            flags["stale_neutral"] += 1
        else:
            flags["unknown"] += 1
    return {"jobs": enriched, "flags": flags}
