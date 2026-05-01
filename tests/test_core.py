from __future__ import annotations

import json

import pytest

from datetime import date

from job_tracker_mcp.deadlines import check_deadlines
from job_tracker_mcp.scoring import score_resume_against_text
from job_tracker_mcp.storage import crud_tracker


@pytest.fixture(autouse=True)
def isolate_jobs_file(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    store = data / "jobs.json"
    store.write_text(json.dumps({"jobs": []}), encoding="utf-8")

    def fake_data_dir():
        return data

    monkeypatch.setattr("job_tracker_mcp.storage.data_dir", fake_data_dir)
    monkeypatch.setattr("job_tracker_mcp.paths.data_dir", fake_data_dir)
    yield store


def test_crud_roundtrip():
    r = crud_tracker(
        "create",
        {"company": "Acme", "title": "SE", "status": "Applied", "applied_date": "2026-04-01"},
    )
    assert r["ok"]
    jid = r["job"]["id"]
    listed = crud_tracker("list")
    assert len(listed["jobs"]) == 1
    crud_tracker("update", {"id": jid, "status": "Interview"})
    got = crud_tracker("get", {"id": jid})
    assert got["job"]["status"] == "Interview"
    crud_tracker("delete", {"id": jid})
    assert crud_tracker("list")["jobs"] == []


def test_deadline_tiers():
    jobs = [
        {"id": "1", "company": "A", "title": "t", "applied_date": "2026-04-28", "status": "Applied"},
        {"id": "2", "company": "B", "title": "t", "applied_date": "2026-04-16", "status": "Applied"},
        {"id": "3", "company": "C", "title": "t", "applied_date": "2026-04-01", "status": "Applied"},
    ]
    out = check_deadlines(jobs, today=date(2026, 4, 29))
    tiers = {str(j["id"]): j["stale_tier"] for j in out["jobs"]}
    assert tiers["1"] == "neutral"
    assert tiers["2"] == "amber"
    assert tiers["3"] == "red"


def test_score_overlap():
    resume = "Python kubernetes python kubernetes api microservices"
    jd = "Python kubernetes api backend engineer microservices"
    s = score_resume_against_text(resume, jd)
    assert s >= 35
