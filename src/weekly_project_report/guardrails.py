"""Lab 10: Callback/Plugin Guardrails — 안전장치 시스템.

ADK 2.0 Callback Hooks (6개):
    ┌─────────────────────────────────────────────────────────┐
    │  Request Lifecycle                                      │
    │                                                         │
    │  before_agent_call ──→ Agent LLM ──→ after_agent_call   │
    │                           │                             │
    │  before_tool_call  ──→ Tool Exec ──→ after_tool_call    │
    │                           │                             │
    │  before_model_call ──→ Model API ──→ after_model_call   │
    └─────────────────────────────────────────────────────────┘

각 Hook은 요청을 가로채서:
  - 검증 / 필터링 / 로깅 수행
  - 필요시 요청을 차단하거나 수정
  - None 반환 → 통과, 값 반환 → 대체/차단
"""

import re
import time
from collections import defaultdict

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from .monitoring import record_monitor_event


# ══════════════════════════════════════════════════════════════
#  1. Content Safety Guard (before_model_call)
# ══════════════════════════════════════════════════════════════
# 금지 패턴 목록 (정규식)
BLOCKED_PATTERNS = [
    r"(?i)(password|passwd|비밀번호)\s*[:=]",
    r"(?i)(api[_-]?key|secret[_-]?key)\s*[:=]",
    r"\b\d{6}-\d{7}\b",            # 주민등록번호 패턴
    r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",  # 카드 번호
]


def content_safety_guard(
    callback_context: CallbackContext,
    llm_request,
):
    """Block requests containing sensitive information.

    before_model_callback:
      - LLM에 전송되기 전에 모든 콘텐츠를 검사
      - 민감 정보(PII) 감지 시 `LlmResponse`로 즉시 대체 → 모델 호출 차단
      - None 반환 → 정상 진행
    """
    full_text = ""
    for content in getattr(llm_request, "contents", None) or []:
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                full_text += text + "\n"

    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, full_text):
            callback_context.state["guardrail_blocked"] = True
            callback_context.state["guardrail_reason"] = pattern
            record_monitor_event(
                "guardrail_block",
                session_id=str(callback_context.state.get("session_id", "default")),
                error_type="content_safety",
                payload={"pattern": pattern},
            )
            return LlmResponse(
                content=genai_types.Content(
                    role="model",
                    parts=[
                        genai_types.Part(
                            text=(
                                "⚠️ 보안 경고: 민감 정보가 감지되어 요청이 "
                                "차단되었습니다. 개인정보나 인증 정보를 "
                                "포함하지 않도록 수정해 주세요."
                            )
                        )
                    ],
                )
            )

    return None  # 통과


# ══════════════════════════════════════════════════════════════
#  2. Rate Limiter (before_agent_call)
# ══════════════════════════════════════════════════════════════
_call_counts: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_CALLS = 30


def rate_limiter(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Rate-limit agent calls per session.

    before_agent_call 훅:
      - 세션별 호출 빈도 추적
      - 제한 초과 시 Content 반환하여 에이전트 실행 차단
      - 슬라이딩 윈도우 방식으로 카운트
    """
    session_id = callback_context.state.get("session_id", "default")
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # 만료된 호출 기록 제거
    _call_counts[session_id] = [
        t for t in _call_counts[session_id] if t > window_start
    ]

    # 윈도우 내 호출이 전혀 없는 세션 key 정리 — 무제한 key 증가 방지
    expired_sessions = [
        sid for sid, times in _call_counts.items()
        if not any(t > window_start for t in times)
    ]
    for sid in expired_sessions:
        del _call_counts[sid]

    if len(_call_counts[session_id]) >= RATE_LIMIT_MAX_CALLS:
        record_monitor_event(
            "rate_limited",
            session_id=str(session_id),
            success=False,
            error_type="rate_limit_exceeded",
        )
        return genai_types.Content(
            parts=[
                genai_types.Part(
                    text=(
                        f"⚠️ 요청 제한 초과: {RATE_LIMIT_WINDOW}초 내 "
                        f"최대 {RATE_LIMIT_MAX_CALLS}회까지 호출 가능합니다. "
                        "잠시 후 다시 시도해 주세요."
                    )
                )
            ],
            role="model",
        )

    _call_counts[session_id].append(now)
    record_monitor_event(
        "agent_call",
        session_id=str(session_id),
        success=True,
    )
    return None  # 통과


# ══════════════════════════════════════════════════════════════
#  3. Tool Audit Logger (after_tool_call)
# ══════════════════════════════════════════════════════════════
_audit_log: list[dict] = []


def tool_audit_logger(
    *args,
    **kwargs,
):
    """Log all tool executions for audit trail.

    Supports two call patterns:
      - ADK 2.0 after_tool_callback:
        `tool=<BaseTool>, args=<dict>, tool_context=<ToolContext>,
         tool_response=<dict>` → None to keep the original response.
      - Unit-test call: `tool_audit_logger(context, tool_name, tool_result)`.
    """
    # Unit-test positional call
    if args and not kwargs:
        callback_context = args[0] if len(args) > 0 else None
        tool_name = args[1] if len(args) > 1 else "unknown"
        tool_result = args[2] if len(args) > 2 else None
    else:
        tool_obj = kwargs.get("tool")
        callback_context = kwargs.get("tool_context") or kwargs.get("callback_context")
        tool_name = getattr(tool_obj, "name", None) or kwargs.get("tool_name") or "unknown"
        tool_result = kwargs.get("tool_response") or kwargs.get("tool_result")

    state = getattr(callback_context, "state", {}) if callback_context is not None else {}
    session_id = state.get("session_id", "unknown") if hasattr(state, "get") else "unknown"

    entry = {
        "timestamp": time.time(),
        "tool_name": tool_name,
        "session_id": session_id,
        "success": "error" not in str(tool_result).lower(),
        "result_preview": str(tool_result)[:200],
    }
    _audit_log.append(entry)
    record_monitor_event(
        "tool_call",
        session_id=str(session_id),
        tool_name=str(tool_name),
        success=entry["success"],
        error_type=None if entry["success"] else "tool_error",
        payload={"result_preview": entry["result_preview"]},
    )

    # 로그 크기 제한 (최근 1000건)
    if len(_audit_log) > 1000:
        _audit_log.pop(0)

    return None  # 원본 결과 유지


def get_audit_log() -> list[dict]:
    """Retrieve the audit log."""
    return list(_audit_log)


# ══════════════════════════════════════════════════════════════
#  4. Output Sanitizer (after_model_call)
# ══════════════════════════════════════════════════════════════
REDACT_PATTERNS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN-REDACTED]"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL-REDACTED]"),
    (r"\b\d{2,3}-\d{3,4}-\d{4}\b", "[PHONE-REDACTED]"),
]


def output_sanitizer(
    callback_context: CallbackContext,
    llm_response,
):
    """Redact sensitive patterns from model output.

    after_model_callback:
      - ADK 2.0의 `LlmResponse` 에는 `.content: types.Content` 필드가 있음
      - Parts 내 text를 순회하며 민감 정보 마스킹
      - 수정 시 같은 응답 객체를 반환, 변경 없으면 None
    """
    modified = False

    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if parts:
        for part in parts:
            text = getattr(part, "text", None)
            if not text:
                continue
            original = text
            for pattern, replacement in REDACT_PATTERNS:
                text = re.sub(pattern, replacement, text)
            if text != original:
                part.text = text
                modified = True

    if modified:
        record_monitor_event(
            "output_sanitized",
            session_id=str(callback_context.state.get("session_id", "default")),
            success=True,
        )
    return llm_response if modified else None


# ══════════════════════════════════════════════════════════════
#  Guardrails Registry (Agent에 적용하기 위한 콜백 모음)
# ══════════════════════════════════════════════════════════════
GUARDRAIL_CALLBACKS = {
    "before_model_call": content_safety_guard,
    "after_model_call": output_sanitizer,
    "before_agent_call": rate_limiter,
    "after_tool_call": tool_audit_logger,
}
