"""Tests for core/analyze_tool.py — 분석 순수 함수."""

import pytest
from weekly_project_report.core.analyze_tool import (
    _COMPLETION_RATE_THRESHOLD,
    _STATUS_SUMMARY_TEXT,
    _determine_overall_status,
    _has_delay_signal,
    _impact_from_issue,
    _status_label,
)


class TestHasDelaySignal:
    @pytest.mark.parametrize("status", ["지연", "RED", "🔴"])
    def test_delay_status(self, status):
        assert _has_delay_signal(status, []) is True

    def test_delay_keyword_in_issue(self):
        assert _has_delay_signal("진행", ["일정 지체 발생"]) is True

    def test_no_delay(self):
        assert _has_delay_signal("진행", ["API 연동 작업"]) is False


class TestStatusLabel:
    def test_green(self):
        assert _status_label(1.0, 0, False) == "GREEN"

    def test_yellow_low_completion(self):
        assert _status_label(_COMPLETION_RATE_THRESHOLD - 0.01, 0, False) == "YELLOW"

    def test_yellow_has_issue(self):
        assert _status_label(1.0, 1, False) == "YELLOW"

    def test_red_overrides_all(self):
        assert _status_label(1.0, 0, True) == "RED"

    def test_boundary_completion_rate(self):
        # 정확히 임계값은 YELLOW (< 조건)
        assert _status_label(_COMPLETION_RATE_THRESHOLD, 0, False) == "GREEN"


class TestDetermineOverallStatus:
    def test_red_wins(self):
        assert _determine_overall_status({"RED": 1, "YELLOW": 1, "GREEN": 1}) == "RED"

    def test_yellow_over_green(self):
        assert _determine_overall_status({"RED": 0, "YELLOW": 1, "GREEN": 3}) == "YELLOW"

    def test_all_green(self):
        assert _determine_overall_status({"RED": 0, "YELLOW": 0, "GREEN": 5}) == "GREEN"


class TestStatusSummaryText:
    def test_all_statuses_have_message(self):
        for status in ("RED", "YELLOW", "GREEN"):
            assert status in _STATUS_SUMMARY_TEXT
            assert isinstance(_STATUS_SUMMARY_TEXT[status], str)
            assert len(_STATUS_SUMMARY_TEXT[status]) > 0


class TestImpactFromIssue:
    @pytest.mark.parametrize("text,expected", [
        ("일정 지체로 인한 문제", "SCHEDULE"),
        ("납기 초과 예상", "SCHEDULE"),
        ("예산 초과 발생", "COST"),
        ("리소스 부족", "COST"),
        ("코드 품질 이슈", "QUALITY"),
    ])
    def test_impact(self, text, expected):
        assert _impact_from_issue(text) == expected
