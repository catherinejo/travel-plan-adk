"""파이프라인 공통 유틸리티."""

from __future__ import annotations

import json

from pydantic import ValidationError


def _format_validation_error(exc: ValidationError) -> str:
    """Pydantic ValidationError를 사람이 읽기 쉬운 문자열로 변환한다."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts[:5])


def parse_json_dict(json_str: str, arg_name: str) -> tuple[dict, dict | None]:
    """JSON 문자열을 dict로 파싱한다.

    Returns:
        (data, None)      — 성공
        ({}, error_dict)  — 실패
    """
    try:
        decoded = json.loads(json_str)
        if isinstance(decoded, dict):
            return decoded, None
        return {}, {"error": f"{arg_name}은 JSON 객체여야 합니다."}
    except Exception as exc:
        return {}, {"error": f"{arg_name} 파싱 실패: {exc}"}


def load_json_records(raw: str, records_key: str = "records") -> list:
    """LLM 출력에서 레코드 배열을 견고하게 추출한다.

    3단계 fallback:
      1) 정상 JSON 전체 파싱 (list 또는 {records_key: list})
      2) LLM이 JSON 뒤에 설명문을 덧붙인 경우 — 첫 JSON 값만 파싱
      3) 텍스트에서 '[...]' 블록만 잘라 파싱
    """
    payload = raw.strip()
    if not payload:
        return []

    def _extract_records(decoded: object) -> list | None:
        if isinstance(decoded, list):
            return decoded
        if isinstance(decoded, dict) and isinstance(decoded.get(records_key), list):
            return decoded[records_key]
        return None

    # 1) 정상 JSON 전체 파싱
    try:
        result = _extract_records(json.loads(payload))
        if result is not None:
            return result
    except Exception:
        pass

    # 2) 첫 JSON 값만 파싱 (설명문 혼재 대응)
    try:
        decoded, _ = json.JSONDecoder().raw_decode(payload)
        result = _extract_records(decoded)
        if result is not None:
            return result
    except Exception:
        pass

    # 3) '[...]' 블록 추출 fallback
    try:
        start = payload.find("[")
        end = payload.rfind("]")
        if start != -1 and end > start:
            result = _extract_records(json.loads(payload[start : end + 1]))
            if result is not None:
                return result
    except Exception:
        pass

    return []
