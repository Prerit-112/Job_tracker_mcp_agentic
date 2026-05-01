"""MCP stdio helpers and tool-result formatting for the Streamlit client."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult, ContentBlock, TextContent

DEFAULT_SYSTEM = """You are a job search assistant. Your only job is to help users track applications, 
research companies, assess resume fit, and draft follow-ups — using the tools 
available to you.

If the user asks about anything outside of the scope of your role as job search assistant, respond with:

"I'm focused on your job search. I can help you find roles, track applications, 
research companies, check your fit, or draft follow-ups. What would you like to do?"

Do not answer the off-topic question, even partially. Do not apologise extensively. 
Just redirect once and stop.

Tools (use the exact names returned by the host):
- search_jobs: Find roles (Adzuna or offline fixtures). Args: query, optional location, company. Each result includes user_fit_percent / user_fit_summary when the user has saved a profile.
- crud_tracker: manage data/jobs.json — operation list|get|create|update|delete plus payload (e.g. create needs title, company, status, applied_date, jd fields as available). Returned jobs include user fit fields when a profile exists.
- check_deadlines: Staleness / days_since_applied for every saved job.
- score_resume_fit: Resume vs JD fit; job_id and/or jd_text, optional resume_path.
- draft_followup: Follow-up email body for a job_id; optional tone.
- get_company_intel: Short intel for a company name.
- push_dashboard: Opens the Prefab metrics UI; optional default_filter (e.g. Applied, All). In this Streamlit app, tell users to open the dedicated "Prefab UI (Full Window)" tab.

## Tool selection rules

Call only the tools the request actually requires. Use this as a guide:

- User wants to find new jobs → search_jobs
- Always call score_resume_fit after search jobs and curd_tracked to save the data
- User mentions an interview, company prep, "tell me about X" → get_company_intel
- User wants to see, filter, or count saved jobs → crud_tracker (operation: list)
- User wants to save, update status, or delete a job → crud_tracker (operation: create/update/delete)
- User asks what to follow up on, or what's gone stale → check_deadlines
- User asks if they're a good fit, or you have a JD and need to score it → score_resume_fit
- User wants a follow-up email → draft_followup
- User explicitly asks to see the dashboard or visualise their data → push_dashboard

Never call push_dashboard as a default step after writes. Only call it when 
the user wants to see the UI. Never call search_jobs when the user is asking 
about something already in their tracker.

## Sequencing

When multiple tools are needed, chain them in dependency order — e.g. 
search_jobs before crud_tracker (create), or crud_tracker (get) before 
score_resume_fit. Feed each tool result into the next call rather than 
asking the user for data you already retrieved.

## Response style

- Plain, friendly prose only. No raw JSON, no code blocks, no field name dumps.
- Summarise structured results in sentences or tight bullet points.
- When fit data is present (user_fit_percent, user_fit_summary), weave it 
  naturally into your reply — e.g. "You're an 84% match — mainly because of 
  your payments experience."
- Keep answers concise. Lead with the outcome, follow with useful detail.
- If a tool errors, say what failed in plain English and suggest one fix.
"""

def compose_system_instruction(profile: dict[str, str] | None = None) -> str:
    from job_tracker_mcp.user_profile import profile_block_for_system_prompt

    block = profile_block_for_system_prompt(profile)
    return f"{DEFAULT_SYSTEM}\n\n### Current user context\n{block}"


def humanize_assistant_reply(text: str) -> str:
    """Strip JSON/code-fence debris so chat stays user-friendly."""
    import json
    import re

    if not (text or "").strip():
        return text
    t = re.sub(r"```(?:json)?\s*[\s\S]*?```", "", text, flags=re.IGNORECASE)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    stripped = t.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                lines: list[str] = []
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v, ensure_ascii=False)
                    nk = str(k).replace("_", " ")
                    lines.append(f"- **{nk}**: {v}")
                return "\n".join(lines)
        except json.JSONDecodeError:
            pass
    return t


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def server_parameters(project_root: Path | None = None) -> StdioServerParameters:
    root = project_root or repo_root()
    env = dict(os.environ)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "job_tracker_mcp.server"],
        cwd=str(root.resolve()),
        env=env,
    )


def tool_to_openai_dict(tool: Any) -> dict[str, Any]:
    schema = tool.inputSchema
    if not schema:
        schema = {"type": "object", "properties": {}}
    if isinstance(schema, dict) and schema.get("type") is None:
        schema = {**schema, "type": "object"}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (tool.description or "")[:8000],
            "parameters": schema,
        },
    }


def format_tool_result(result: CallToolResult, max_chars: int = 20000) -> str:
    parts: list[str] = []
    if result.isError:
        parts.append("[tool returned isError=true]")
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        else:
            parts.append(_summarize_block(block))
    if result.structuredContent:
        try:
            blob = json.dumps(result.structuredContent, ensure_ascii=False, indent=2)
        except TypeError:
            blob = str(result.structuredContent)
        parts.append("[structured]\n" + blob)
    out = "\n\n".join(p for p in parts if p)
    if len(out) > max_chars:
        return out[: max_chars - 20] + "\n… [truncated]"
    return out or "(empty tool result)"


def _summarize_block(block: ContentBlock) -> str:
    d = block.model_dump() if hasattr(block, "model_dump") else str(block)
    if len(d) > 1500:
        return str(d)[:1500] + "…"
    return str(d)
