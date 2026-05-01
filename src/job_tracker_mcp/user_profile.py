"""User profile stored in data/user_profile.json (shared by Streamlit UI and MCP server)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from job_tracker_mcp.paths import data_dir


def user_profile_path() -> Path:
    return data_dir() / "user_profile.json"


def default_profile() -> dict[str, str]:
    return {
        "skills": "",
        "experience": "",
        "company_preferences": "",
    }


def load_user_profile() -> dict[str, str]:
    path = user_profile_path()
    if not path.exists():
        return default_profile()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_profile()
    out = default_profile()
    for k in out:
        v = raw.get(k)
        out[k] = str(v).strip() if v is not None else ""
    return out


def save_user_profile(data: dict[str, str]) -> None:
    path = user_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "skills": str(data.get("skills", "") or ""),
        "experience": str(data.get("experience", "") or ""),
        "company_preferences": str(data.get("company_preferences", "") or ""),
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    last_err: OSError | None = None
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except OSError as e:
            last_err = e
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
            time.sleep(0.05)
    raise last_err if last_err else RuntimeError("save_user_profile failed")


def profile_for_fit_text(profile: dict[str, str] | None = None) -> str:
    p = profile if profile is not None else load_user_profile()
    parts = [
        f"Skills:\n{p.get('skills', '')}",
        f"Experience:\n{p.get('experience', '')}",
        f"Company / culture preferences:\n{p.get('company_preferences', '')}",
    ]
    return "\n\n".join(parts).strip()


def profile_is_blank(profile: dict[str, str] | None = None) -> bool:
    p = profile if profile is not None else load_user_profile()
    return not any(str(p.get(k, "")).strip() for k in ("skills", "experience", "company_preferences"))


def profile_block_for_system_prompt(profile: dict[str, str] | None = None) -> str:
    p = profile if profile is not None else load_user_profile()
    if profile_is_blank(p):
        return (
            "The user has not saved a profile yet. Gently remind them to fill "
            "skills, experience, and company preferences in the app sidebar so job fit scores apply."
        )
    return (
        "User profile (use for recommendations and interpreting job fit scores; do not dump as raw JSON):\n"
        f"- Skills: {p.get('skills', '') or '(none)'}\n"
        f"- Experience: {p.get('experience', '') or '(none)'}\n"
        f"- Company preferences: {p.get('company_preferences', '') or '(none)'}"
    )
