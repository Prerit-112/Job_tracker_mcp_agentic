"""Resolve project `data/` directory relative to the package (no hard-coded OneDrive paths)."""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    # job_tracker_mcp/paths.py -> parents[2] = repo root (contains data/)
    return Path(__file__).resolve().parent.parent.parent


def data_dir() -> Path:
    return project_root() / "data"
