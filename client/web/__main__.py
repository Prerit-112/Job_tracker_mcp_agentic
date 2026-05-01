"""Run: python -m client.web (from repo root, with optional --reload)."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description="Job Tracker Tier 2 web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7861)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()
    uvicorn.run(
        "client.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=False,
    )


if __name__ == "__main__":
    main()
