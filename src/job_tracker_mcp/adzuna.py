"""Adzuna job search; offline stub when API keys are missing."""

from __future__ import annotations

import os
from typing import Any

import httpx

OFFLINE_FIXTURE = [
    {
        "company": "Zomato",
        "title": "Software Engineer — Platform",
        "source_url": "https://example.com/jobs/zomato-se-1",
        "jd_summary": "Build scalable services, Python/Go, distributed systems.",
        "location": "Gurgaon, IN",
    },
    {
        "company": "Zomato",
        "title": "Backend Engineer",
        "source_url": "https://example.com/jobs/zomato-be-2",
        "jd_summary": "APIs, microservices, PostgreSQL, Kafka.",
        "location": "Bangalore, IN",
    },
]


def search_jobs(
    query: str,
    location: str | None = None,
    company: str | None = None,
    country: str | None = None,
) -> dict[str, Any]:
    app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
    app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()
    loc = (location or "").strip()
    comp_filter = (company or "").strip().lower()
    q = query.strip()
    cc = (country or os.environ.get("ADZUNA_COUNTRY", "gb")).strip().lower()

    if not app_id or not app_key:
        rows = []
        for item in OFFLINE_FIXTURE:
            blob = f"{item['title']} {item['company']} {item.get('jd_summary','')}".lower()
            if q.lower() not in blob and q:
                continue
            if comp_filter and comp_filter not in item["company"].lower():
                continue
            if loc and loc.lower() not in (item.get("location") or "").lower():
                continue
            rows.append(dict(item))
        return {
            "ok": True,
            "source": "stub",
            "message": "ADZUNA_APP_ID/ADZUNA_APP_KEY not set — returning offline fixtures.",
            "results": rows,
        }

    # Adzuna public search API (v1)
    url = f"https://api.adzuna.com/v1/api/jobs/{cc}/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 15,
        "what": q,
    }
    if loc:
        params["where"] = loc

    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        payload = r.json()

    results = []
    for hit in payload.get("results", []):
        company_name = (hit.get("company") or {}).get("display_name", "")
        if comp_filter and comp_filter not in company_name.lower():
            continue
        adz_url = hit.get("redirect_url") or ""
        results.append(
            {
                "company": company_name,
                "title": hit.get("title", ""),
                "source_url": adz_url,
                "jd_summary": (hit.get("description") or "")[:2000],
                "location": (hit.get("location") or {}).get("display_name", ""),
            }
        )

    return {"ok": True, "source": "adzuna", "results": results, "count": len(results)}
