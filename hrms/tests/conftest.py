"""Give every HRMS test its own fresh, auto-seeded database and a fixed 'today'."""

import pytest


@pytest.fixture(autouse=True)
def hrms_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HRMS_DB_PATH", str(tmp_path / "hrms_test.db"))
    monkeypatch.setenv("HRMS_TODAY", "2026-11-16")
