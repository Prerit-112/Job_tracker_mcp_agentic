"""Structured logging for MCP tools (assignment-friendly \"agent\" traces)."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

_LOGGERS_CONFIGURED = False

_TOOL_LOG = logging.getLogger("job_tracker_mcp.tools")
_AGENT_LOG = logging.getLogger("job_tracker_mcp.agent")


def configure_agent_logging() -> None:
    """Enable verbose tool + agent-style traces when JOB_TRACKER_AGENT_LOG is truthy."""
    global _LOGGERS_CONFIGURED
    if _LOGGERS_CONFIGURED:
        return
    flag = os.environ.get("JOB_TRACKER_AGENT_LOG", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        _LOGGERS_CONFIGURED = True
        return

    level = logging.DEBUG if os.environ.get("JOB_TRACKER_LOG_DEBUG") else logging.INFO
    fmt = "%(asctime)sZ | %(levelname)-5s | %(name)s | %(message)s"

    class UtcFormatter(logging.Formatter):
        def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
            return (
                datetime.fromtimestamp(record.created, tz=timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%S")
            )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(UtcFormatter(fmt))

    root = logging.getLogger("job_tracker_mcp")
    root.setLevel(level)
    root.addHandler(handler)

    _LOGGERS_CONFIGURED = True


def _safe_preview(obj: Any, max_len: int = 800) -> str:
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        s = repr(obj)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def trace_tool_start(name: str, **arguments: Any) -> None:
    configure_agent_logging()
    _TOOL_LOG.info("BEGIN %s args=%s", name, _safe_preview(arguments))


def trace_tool_end(name: str, result: Any = None, error: BaseException | None = None) -> None:
    configure_agent_logging()
    if error:
        _TOOL_LOG.error("END %s ERROR %s", name, error)
    else:
        _TOOL_LOG.info("END %s result=%s", name, _safe_preview(result))


def agent_step(step: int, thought: str, tool: str | None = None) -> None:
    """Demo Narrator / pseudo-planner line (used by agent_demo.py)."""
    configure_agent_logging()
    if tool:
        _AGENT_LOG.info("STEP %d | %s | invoking_tool=%s", step, thought, tool)
    else:
        _AGENT_LOG.info("STEP %d | %s", step, thought)
