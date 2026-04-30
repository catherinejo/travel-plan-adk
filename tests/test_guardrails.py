"""Tests for guardrails.py — 콜백 가드레일 단위 테스트."""

import time

import pytest


# ── Mock 객체 ──────────────────────────────────────────────────
class _MockState(dict):
    pass


class _MockCallbackContext:
    def __init__(self, session_id: str = "test-session"):
        self.state = _MockState({"session_id": session_id})


class _MockPart:
    def __init__(self, text: str):
        self.text = text


class _MockContent:
    def __init__(self, text: str):
        self.parts = [_MockPart(text)]
        self.role = "model"


class _MockLlmRequest:
    def __init__(self, text: str = ""):
        self.contents = [_MockContent(text)]


class _MockLlmResponse:
    def __init__(self, text: str = ""):
        self.content = _MockContent(text)


# ── content_safety_guard ───────────────────────────────────────
class TestContentSafetyGuard:
    def test_clean_input_passes(self):
        from weekly_project_report.guardrails import content_safety_guard

        ctx = _MockCallbackContext()
        result = content_safety_guard(ctx, _MockLlmRequest("일반적인 업무 보고 내용입니다."))
        assert result is None
        assert not ctx.state.get("guardrail_blocked")

    @pytest.mark.parametrize("text", [
        "password: secret123",
        "api_key=abc123def",
        "주민번호 123456-1234567",
        "카드번호 1234 5678 9012 3456",
    ])
    def test_sensitive_patterns_blocked(self, text):
        from weekly_project_report.guardrails import content_safety_guard

        ctx = _MockCallbackContext()
        result = content_safety_guard(ctx, _MockLlmRequest(text))
        assert result is not None
        assert ctx.state.get("guardrail_blocked") is True

    def test_empty_request_passes(self):
        from weekly_project_report.guardrails import content_safety_guard

        ctx = _MockCallbackContext()
        assert content_safety_guard(ctx, _MockLlmRequest("")) is None

    def test_no_contents_passes(self):
        from weekly_project_report.guardrails import content_safety_guard

        ctx = _MockCallbackContext()
        req = _MockLlmRequest()
        req.contents = None
        assert content_safety_guard(ctx, req) is None


# ── rate_limiter ───────────────────────────────────────────────
class TestRateLimiter:
    def test_normal_call_allowed(self):
        from weekly_project_report.guardrails import _call_counts, rate_limiter

        session_id = "rl-allow-test"
        _call_counts[session_id] = []
        ctx = _MockCallbackContext(session_id=session_id)
        result = rate_limiter(ctx)
        assert result is None

    def test_rate_limit_exceeded(self):
        from weekly_project_report.guardrails import (
            RATE_LIMIT_MAX_CALLS,
            _call_counts,
            rate_limiter,
        )

        session_id = "rl-exceed-test"
        now = time.time()
        _call_counts[session_id] = [now] * RATE_LIMIT_MAX_CALLS
        ctx = _MockCallbackContext(session_id=session_id)
        result = rate_limiter(ctx)
        assert result is not None

    def test_expired_sessions_cleaned(self):
        from weekly_project_report.guardrails import (
            RATE_LIMIT_WINDOW,
            _call_counts,
            rate_limiter,
        )

        # 오래된 세션 항목 삽입 (모두 만료)
        old_session = "rl-old-session"
        expired_time = time.time() - RATE_LIMIT_WINDOW - 1
        _call_counts[old_session] = [expired_time]

        ctx = _MockCallbackContext(session_id="rl-new-session")
        rate_limiter(ctx)

        # 만료된 세션 key가 제거됐는지 확인
        assert old_session not in _call_counts


# ── tool_audit_logger ──────────────────────────────────────────
class TestToolAuditLogger:
    def setup_method(self):
        from weekly_project_report.guardrails import _audit_log

        _audit_log.clear()

    def test_success_result_logged(self):
        from weekly_project_report.guardrails import _audit_log, get_audit_log, tool_audit_logger

        ctx = _MockCallbackContext()
        tool_audit_logger(ctx, "test_tool", {"result": "ok"})
        log = get_audit_log()
        assert len(log) == 1
        assert log[-1]["tool_name"] == "test_tool"
        assert log[-1]["success"] is True

    def test_error_result_marked_failed(self):
        from weekly_project_report.guardrails import get_audit_log, tool_audit_logger

        ctx = _MockCallbackContext()
        tool_audit_logger(ctx, "failing_tool", {"error": "something went wrong"})
        log = get_audit_log()
        assert log[-1]["success"] is False

    def test_log_size_capped_at_1000(self):
        from weekly_project_report.guardrails import _audit_log, tool_audit_logger

        ctx = _MockCallbackContext()
        for _ in range(1010):
            tool_audit_logger(ctx, "t", {"result": "ok"})
        assert len(_audit_log) <= 1000


# ── output_sanitizer ───────────────────────────────────────────
class TestOutputSanitizer:
    def test_ssn_redacted(self):
        from weekly_project_report.guardrails import output_sanitizer

        ctx = _MockCallbackContext()
        resp = _MockLlmResponse("SSN: 123-45-6789 입니다.")
        result = output_sanitizer(ctx, resp)
        assert result is not None
        assert "[SSN-REDACTED]" in resp.content.parts[0].text

    def test_email_redacted(self):
        from weekly_project_report.guardrails import output_sanitizer

        ctx = _MockCallbackContext()
        resp = _MockLlmResponse("이메일: user@example.com 입니다.")
        result = output_sanitizer(ctx, resp)
        assert result is not None
        assert "[EMAIL-REDACTED]" in resp.content.parts[0].text

    def test_phone_redacted(self):
        from weekly_project_report.guardrails import output_sanitizer

        ctx = _MockCallbackContext()
        resp = _MockLlmResponse("전화: 010-1234-5678 입니다.")
        result = output_sanitizer(ctx, resp)
        assert result is not None
        assert "[PHONE-REDACTED]" in resp.content.parts[0].text

    def test_clean_output_returns_none(self):
        from weekly_project_report.guardrails import output_sanitizer

        ctx = _MockCallbackContext()
        resp = _MockLlmResponse("일반적인 업무 보고 내용입니다.")
        result = output_sanitizer(ctx, resp)
        assert result is None

    def test_no_content_returns_none(self):
        from weekly_project_report.guardrails import output_sanitizer

        ctx = _MockCallbackContext()
        resp = _MockLlmResponse()
        resp.content = None
        result = output_sanitizer(ctx, resp)
        assert result is None
