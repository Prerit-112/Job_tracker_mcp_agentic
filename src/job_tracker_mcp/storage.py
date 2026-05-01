"""Atomic JSON storage for `data/jobs.json` with a process file lock (cross-platform)."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from job_tracker_mcp.paths import data_dir

Operation = Literal["list", "get", "create", "update", "delete"]

DEFAULT_STATUSES = (
    "Applied",
    "Screen",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
)


def jobs_path() -> Path:
    return data_dir() / "jobs.json"


def _lock_path() -> Path:
    return data_dir() / "jobs.json.lock"


def _ensure_parent() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)


def _empty_store() -> dict[str, Any]:
    return {"jobs": []}


def load_jobs_raw() -> dict[str, Any]:
    path = jobs_path()
    if not path.exists():
        return _empty_store()
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if "jobs" not in data:
        data = {"jobs": data.get("jobs", [])}
    return data


def atomic_save(data: dict[str, Any]) -> None:
    """Write JSON atomically (temp in same directory + replace)."""
    _ensure_parent()
    path = jobs_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    # Windows + OneDrive: replacing an open/existing target sometimes raises WinError 5.
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
    raise last_err if last_err else RuntimeError("atomic_save failed")


def crud_tracker(operation: Operation, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    CRUD for jobs.json under a file lock.

    Operations:
    - list: return { jobs }
    - get: payload { id } -> { job } or 404 semantics via error key
    - create: payload job fields (id optional — generated)
    - update: payload must include id + fields to merge
    - delete: payload { id }
    """
    payload = payload or {}
    _ensure_parent()
    lock = FileLock(str(_lock_path()), timeout=30)
    with lock:
        data = load_jobs_raw()
        jobs: list[dict[str, Any]] = data.setdefault("jobs", [])

        if operation == "list":
            return {"ok": True, "jobs": list(jobs)}

        if operation == "get":
            jid = str(payload.get("id", ""))
            for j in jobs:
                if str(j.get("id")) == jid:
                    return {"ok": True, "job": j}
            return {"ok": False, "error": f"job not found: {jid}"}

        if operation == "create":
            rec = dict(payload)
            rid = str(rec.get("id") or uuid.uuid4())
            rec["id"] = rid
            jobs.append(rec)
            atomic_save(data)
            return {"ok": True, "job": rec}

        if operation == "update":
            jid = str(payload.get("id", ""))
            patch = {k: v for k, v in payload.items() if k != "id"}
            for i, j in enumerate(jobs):
                if str(j.get("id")) == jid:
                    j.update(patch)
                    jobs[i] = j
                    atomic_save(data)
                    return {"ok": True, "job": j}
            return {"ok": False, "error": f"job not found: {jid}"}

        if operation == "delete":
            jid = str(payload.get("id", ""))
            new_list = [j for j in jobs if str(j.get("id")) != jid]
            if len(new_list) == len(jobs):
                return {"ok": False, "error": f"job not found: {jid}"}
            data["jobs"] = new_list
            atomic_save(data)
            return {"ok": True, "deleted_id": jid}

    return {"ok": False, "error": f"unknown operation: {operation}"}
