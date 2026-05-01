"""Resume ↔ JD fit score: semantic LLM scoring when configured; Jaccard fallback."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx

from job_tracker_mcp.paths import data_dir

ScoreMode = Literal["auto", "llm", "jaccard"]

_WORD = re.compile(r"[a-z0-9]+", re.I)

_SYSTEM_SEMANTIC = """You are an expert technical recruiter. Work in two explicit phases:

Phase A — Extract jd_skills from the job description only: languages, frameworks, databases,
cloud platforms, DevOps/tooling, data stacks, security/compliance tools, methodologies, and domain-specific
tech mentioned or clearly implied. Use concise noun phrases (max 10 words each). Prefer canonical names
(e.g. "Kubernetes" not just "container orchestration" unless JD says only the latter).

Phase B — Compare the candidate resume/profile text to jd_skills using semantic fit (not literal word match):
- Synonyms and abbreviations count fully (JavaScript ↔ JS; K8s ↔ Kubernetes; GCP ↔ Google Cloud).
- Adjacent / transferable skills earn partial credit when clearly related (Terraform vs Pulumi for IaC;
  Bash vs POSIX shell for scripting).
- Skills absent from both lists must not inflate the score.

Respond with ONLY valid JSON:
{"jd_skills":["string", ...],"candidate_skills":["evidence-backed phrases from resume", ...],"fit_score":0,"rationale":"one short paragraph"}

fit_score is an integer from 0 through 100 inclusive."""

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_TIMEOUT_S = 75
_JD_RESUME_CHUNK = 8000


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) * 100.0 if union else 0.0


def _legacy_jaccard_percent(resume_text: str, jd_text: str) -> float:
    rt, jt = _tokens(resume_text), _tokens(jd_text)
    stop = {
        "the",
        "and",
        "for",
        "with",
        "you",
        "our",
        "are",
        "this",
        "that",
        "from",
        "your",
        "will",
        "have",
        "has",
        "was",
        "not",
        "any",
        "all",
        "can",
        "may",
        "etc",
    }
    rt -= stop
    jt -= stop
    return round(_jaccard(rt, jt), 1)


def _truncate(s: str, max_chars: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 3] + "..."


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _parse_llm_payload(content: str) -> dict[str, Any]:
    data = json.loads(_strip_json_fence(content))
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    score = data.get("fit_score")
    if isinstance(score, float | int):
        pct = float(score)
    else:
        raise ValueError("missing fit_score")
    pct = max(0.0, min(100.0, pct))
    jd_skills = data.get("jd_skills") or []
    cand = data.get("candidate_skills") or []
    if not isinstance(jd_skills, list):
        jd_skills = []
    if not isinstance(cand, list):
        cand = []
    jd_skills = [str(x).strip() for x in jd_skills if str(x).strip()]
    cand = [str(x).strip() for x in cand if str(x).strip()]
    rationale = data.get("rationale") or ""
    if not isinstance(rationale, str):
        rationale = str(rationale)
    return {
        "fit_percent": round(pct, 1),
        "jd_skills": jd_skills,
        "candidate_skills": cand,
        "rationale": rationale.strip(),
    }


def _openai_chat_completion(messages: list[dict[str, str]], *, json_mode: bool) -> str:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = (os.environ.get("OPENAI_MODEL") or _DEFAULT_MODEL).strip()
    url = f"{base}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = float(os.environ.get("OPENAI_TIMEOUT_S") or _DEFAULT_TIMEOUT_S)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        body = r.json()

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("empty choices from chat completion")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if content is None:
        raise RuntimeError("no message content")
    return str(content)


def score_resume_semantic_llm(resume_text: str, jd_text: str) -> dict[str, Any]:
    """Extract JD skills and compute semantic fit via one LLM call. Raises on failure."""
    jd_part = _truncate(jd_text, _JD_RESUME_CHUNK)
    resume_part = _truncate(resume_text, _JD_RESUME_CHUNK)
    user_msg = (
        f"JOB DESCRIPTION:\n{jd_part}\n\n"
        f"CANDIDATE RESUME OR PROFILE:\n{resume_part}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_SEMANTIC},
        {"role": "user", "content": user_msg},
    ]
    raw = _openai_chat_completion(messages, json_mode=True)
    return _parse_llm_payload(raw)


def score_resume_detail(
    resume_text: str,
    jd_text: str,
    *,
    mode: ScoreMode = "llm",
) -> dict[str, Any]:
    """Fit score plus metadata: jd_skills and rationale when LLM path succeeds."""
    want_llm = mode == "llm" or (mode == "auto" and (os.environ.get("OPENAI_API_KEY") or "").strip())

    if want_llm:
        try:
            sem = score_resume_semantic_llm(resume_text, jd_text)
            return {
                "fit_percent": sem["fit_percent"],
                "mode_used": "llm",
                "jd_skills": sem["jd_skills"],
                "candidate_skills": sem["candidate_skills"],
                "rationale": sem["rationale"],
            }
        except Exception as err:  # noqa: BLE001
            pct = _legacy_jaccard_percent(resume_text, jd_text)
            return {
                "fit_percent": pct,
                "mode_used": "jaccard",
                "jd_skills": [],
                "candidate_skills": [],
                "rationale": "",
                "fallback_reason": str(err),
            }

    pct = _legacy_jaccard_percent(resume_text, jd_text)
    return {
        "fit_percent": pct,
        "mode_used": "jaccard",
        "jd_skills": [],
        "candidate_skills": [],
        "rationale": "",
    }


def score_resume_against_text(
    resume_text: str,
    jd_text: str,
    *,
    mode: ScoreMode = "llm",
) -> float:
    """Compatibility wrapper returning only the numeric score (0–100)."""
    return float(score_resume_detail(resume_text, jd_text, mode=mode)["fit_percent"])


def read_resume(path: str | None = None) -> str:
    p = Path(path or data_dir() / "resume.txt")
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")
