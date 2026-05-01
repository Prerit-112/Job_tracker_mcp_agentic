"""Plain follow-up text generator (shared by MCP tool and demos)."""

from __future__ import annotations

from typing import Any


def draft_followup_for_job(job: dict[str, Any], tone: str | None = None) -> dict[str, Any]:
    company = str(job.get("company", "there"))
    title = str(job.get("title", "the role"))
    recruiter = str(job.get("contact_name") or "Hiring Team")
    tone_l = (tone or "professional").lower()
    if tone_l not in ("professional", "warm", "brief"):
        tone_l = "professional"

    if tone_l == "brief":
        body = (
            f"Hi {recruiter},\n\n"
            f"I applied for **{title}** at **{company}** and wanted to reconfirm interest. "
            f"Happy to share more on fit or timing.\n\n"
            f"Best regards"
        )
    elif tone_l == "warm":
        body = (
            f"Hi {recruiter},\n\n"
            f"I hope you're doing well. I'm writing about my application for **{title}** at **{company}**. "
            f"I remain very interested and would appreciate any update when convenient.\n\n"
            f"Thank you for your time.\n\n"
            f"Best regards"
        )
    else:
        body = (
            f"Dear {recruiter},\n\n"
            f"I am following up regarding my application for the **{title}** position at **{company}**. "
            f"If helpful, I can provide additional materials or answer questions about my background.\n\n"
            f"Sincerely"
        )
    return {"ok": True, "markdown": body, "tone": tone_l}
