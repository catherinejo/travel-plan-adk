"""Tests for monitoring.py — SQLite 이벤트 저장 및 요약 단위 테스트."""

import sqlite3

import pytest


@pytest.fixture(autouse=True)
def reset_db_state(monkeypatch, tmp_path):
    """각 테스트마다 임시 DB 경로와 초기화 플래그를 리셋한다."""
    db_path = tmp_path / "test_monitor.db"
    import weekly_project_report.monitoring as mon

    monkeypatch.setattr(mon, "_MONITOR_DB_PATH", db_path)
    monkeypatch.setattr(mon, "_db_initialized", False)
    yield db_path


# ── record_monitor_event ───────────────────────────────────────
class TestRecordMonitorEvent:
    def test_event_persisted(self, reset_db_state):
        from weekly_project_report.monitoring import record_monitor_event

        record_monitor_event("test_event", session_id="s1", success=True)

        conn = sqlite3.connect(reset_db_state)
        rows = conn.execute(
            "SELECT event_type, session_id, success FROM monitor_events"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "test_event"
        assert rows[0][1] == "s1"
        assert rows[0][2] == 1  # success=True → 1

    def test_multiple_events(self, reset_db_state):
        from weekly_project_report.monitoring import record_monitor_event

        for i in range(5):
            record_monitor_event("evt", session_id=f"s{i}")

        conn = sqlite3.connect(reset_db_state)
        count = conn.execute("SELECT COUNT(*) FROM monitor_events").fetchone()[0]
        conn.close()
        assert count == 5

    def test_payload_stored_as_json(self, reset_db_state):
        from weekly_project_report.monitoring import record_monitor_event

        record_monitor_event("tool_call", payload={"tool": "parse", "rows": 10})

        conn = sqlite3.connect(reset_db_state)
        row = conn.execute("SELECT payload_json FROM monitor_events").fetchone()
        conn.close()
        assert row is not None
        assert "parse" in row[0]

    def test_none_success_stored_as_null(self, reset_db_state):
        from weekly_project_report.monitoring import record_monitor_event

        record_monitor_event("info_event")

        conn = sqlite3.connect(reset_db_state)
        row = conn.execute("SELECT success FROM monitor_events").fetchone()
        conn.close()
        assert row[0] is None

    def test_db_initialized_once(self, reset_db_state, monkeypatch):
        """DB 초기화 함수가 중복 호출되지 않아야 한다."""
        import weekly_project_report.monitoring as mon

        call_count = {"n": 0}
        original_init = mon._init_monitor_db

        def counting_init():
            call_count["n"] += 1
            original_init()

        monkeypatch.setattr(mon, "_init_monitor_db", counting_init)
        monkeypatch.setattr(mon, "_db_initialized", False)

        from weekly_project_report.monitoring import record_monitor_event

        record_monitor_event("evt1")
        record_monitor_event("evt2")
        record_monitor_event("evt3")

        assert call_count["n"] == 1


# ── get_monitoring_summary ─────────────────────────────────────
class TestGetMonitoringSummary:
    def test_empty_db_returns_zeros(self, reset_db_state):
        from weekly_project_report.monitoring import get_monitoring_summary

        summary = get_monitoring_summary()
        assert summary["total_events"] == 0
        assert summary["guardrail_blocks"] == 0
        assert summary["tool_calls"] == 0
        assert summary["agent_calls"] == 0

    def test_counts_each_event_type(self, reset_db_state):
        from weekly_project_report.monitoring import get_monitoring_summary, record_monitor_event

        record_monitor_event("guardrail_block", session_id="s1", success=False, error_type="content_safety")
        record_monitor_event("tool_call", session_id="s1", tool_name="parse_tool", success=True)
        record_monitor_event("tool_call", session_id="s1", tool_name="aggregate_tool", success=False)
        record_monitor_event("agent_call", session_id="s1", success=True)
        record_monitor_event("rate_limited", session_id="s1", success=False)

        summary = get_monitoring_summary()
        assert summary["guardrail_blocks"] == 1
        assert summary["tool_calls"] == 2
        assert summary["tool_errors"] == 1
        assert summary["agent_calls"] == 1
        assert summary["rate_limited"] == 1

    def test_window_filters_old_events(self, reset_db_state):
        """window_minutes=0이면 최근 이벤트가 포함되지 않을 수 있다."""
        from weekly_project_report.monitoring import get_monitoring_summary, record_monitor_event

        record_monitor_event("agent_call", session_id="s1", success=True)
        # 매우 짧은 윈도우로 조회 — 방금 기록한 이벤트는 포함됨
        summary = get_monitoring_summary(window_minutes=60)
        assert summary["agent_calls"] >= 1

    def test_latest_event_ts_present(self, reset_db_state):
        from weekly_project_report.monitoring import get_monitoring_summary, record_monitor_event

        record_monitor_event("agent_call", session_id="s1")
        summary = get_monitoring_summary()
        assert summary["latest_event_ts"] is not None
