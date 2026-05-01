"""Company intel: mock, or lightweight Wikipedia summary (no Glassdoor dependency)."""

from __future__ import annotations

import os
import re
from html import unescape
from typing import Any

import httpx


def _strip_html(s: str) -> str:
    s = re.sub(r"(?s)<script.*?>.*?</script>", " ", s, flags=re.I)
    s = re.sub(r"(?s)<style.*?>.*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return unescape(re.sub(r"\s+", " ", s)).strip()


def _wikipedia_summary(company: str) -> dict[str, Any]:
    q = company.strip()
    api = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "titles": q,
        "prop": "extracts",
        "exintro": True,
        "explaintext": True,
        "redirects": 1,
    }
    with httpx.Client(timeout=20.0, headers={"User-Agent": "job-tracker-mcp/0.1 (champ.prerit@gmail.com)"}) as client:
        r = client.get(api, params=params)
        r.raise_for_status()
        data = r.json()
    pages = (data.get("query") or {}).get("pages") or {}
    extract = ""
    title = q
    for _pid, page in pages.items():
        title = page.get("title", title)
        extract = (page.get("extract") or "").strip()
        break
    snippet = (extract[:800] + "…") if len(extract) > 800 else extract
    rating = None
    if extract:
        rating = "see summary"
    return {
        "mode": "wikipedia",
        "company": company,
        "title": title,
        "rating": rating,
        "size_band": None,
        "snippet": snippet or "No Wikipedia extract found.",
        "source_url": f"https://en.wikipedia.org/wiki/{q.replace(' ', '_')}",
    }


def get_company_intel(company: str) -> dict[str, Any]:
    mode = os.environ.get("COMPANY_INTEL_MODE", "mock").strip().lower()
    if mode == "wikipedia":
        try:
            return {"ok": True, **_wikipedia_summary(company)}
        except Exception as e:  # noqa: BLE001 — demo boundary; surface error
            return {"ok": False, "error": str(e), "mode": "wikipedia"}

    # mock / default
    c = company.strip()
    return {
        "ok": True,
        "mode": "mock",
        "company": c,
        "rating": "4.1 / 5 (demo)",
        "size_band": "1k–5k employees (demo)",
        "snippet": (
            f"Structured mock intel for {c}. "
            "Set COMPANY_INTEL_MODE=wikipedia for live Wikipedia summaries."
        ),
        "source_url": "https://example.com/intel-mock",
    }
