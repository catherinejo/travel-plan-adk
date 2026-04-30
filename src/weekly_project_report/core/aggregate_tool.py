"""Aggregation tool for weekly report pipeline."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import re

from google.adk.tools.tool_context import ToolContext
from pydantic import ValidationError

from ._utils import _format_validation_error, load_json_records
from ..schemas.model import ProjectAggregate
from ..schemas.model import TaskItem


def _status_emoji(items: list[dict]) -> str:
    """
    상태 색상 매핑:
    - 진행: 🔵
    - 완료: 🟢
    - 예정: 🟣
    - 지연: 🔴
    우선순위는 리스크가 큰 상태를 먼저 반영한다.
    """
    statuses = [str(r.get("status") or "").strip() for r in items]
    if any(s == "지연" for s in statuses):
        return "🔴"
    if any(s in {"진행", "진행중", "진행 중"} for s in statuses):
        return "🔵"
    if any(s == "예정" for s in statuses):
        return "🟣"
    if any(s == "완료" for s in statuses):
        return "🟢"
    return "🔵"


_PROJECT_SIMILARITY_THRESHOLD = 0.28
_MIN_TOKEN_CHARS = 1  # 이 길이 이하의 토큰은 무의미한 단음절로 무시

_PROJECT_STOPWORDS = {
    "프로젝트",
    "사업",
    "구축",
    "지원",
    "개발",
    "업무",
    "진행",
    "완료",
    "작성",
    "세션",
}
_ISSUE_KEYWORDS = ("이슈", "리스크", "지연", "문제", "오류", "블로킹", "장애", "실패")

_COMPANY_SUFFIXES = (
    "홀딩스",
    "holdings",
    "그룹",
    "group",
    "주식회사",
    "㈜",
    "(주)",
)


def _coerce_date(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _normalize_task_record(record: dict) -> dict:
    """
    파서 출력 변형/누락 필드를 aggregate 단계에서 보정한다.
    - is_delayed / has_issue_keyword 누락 시 기본 규칙으로 채움
    - 날짜 필드가 비어있거나 문자열 포맷이 달라도 TaskItem 검증 가능한 형태로 정리
    """
    normalized = dict(record)
    status = str(normalized.get("status") or "").strip()
    summary = str(normalized.get("summary") or "").strip()

    for date_key in ("prev_start", "prev_end", "next_start", "next_end"):
        normalized[date_key] = _coerce_date(normalized.get(date_key))

    if "has_issue_keyword" not in normalized or normalized.get("has_issue_keyword") is None:
        lowered = summary.lower()
        normalized["has_issue_keyword"] = any(k in lowered for k in _ISSUE_KEYWORDS)

    if "is_delayed" not in normalized or normalized.get("is_delayed") is None:
        normalized["is_delayed"] = status == "지연"

    return normalized


def _project_tokens(name: str) -> list[str]:
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", (name or "").lower())
    normalized: list[str] = []
    for token in tokens:
        token = token.strip()
        if len(token) <= _MIN_TOKEN_CHARS:
            continue
        if token in _PROJECT_STOPWORDS:
            continue
        normalized.append(token)
        # 접미사 AI 변형(예: 경영정보분석ai)을 같은 토큰으로 보기 위한 보조 토큰
        if token.endswith("ai") and len(token) > 4:
            base = token[:-2]
            if base and base not in _PROJECT_STOPWORDS:
                normalized.append(base)
    return normalized


def _project_anchor(name: str) -> str:
    tokens = _project_tokens(name)
    return tokens[0] if tokens else ""


def _name_similarity(a: str, b: str) -> float:
    set_a = set(_project_tokens(a))
    set_b = set(_project_tokens(b))
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def _should_merge_by_anchor(a: str, b: str) -> bool:
    """
    유사도 외에 브랜드/앵커 기반 병합을 보강한다.
    - 같은 anchor를 공유하고
    - 한쪽 이름이 사실상 브랜드 단독명(토큰 1개)이면
      다른 쪽 확장 프로젝트명과 동일 프로젝트군으로 본다.
    """
    anchor_a = _project_anchor(a)
    anchor_b = _project_anchor(b)
    if not anchor_a or anchor_a != anchor_b:
        return False

    tokens_a = set(_project_tokens(a))
    tokens_b = set(_project_tokens(b))
    if not tokens_a or not tokens_b:
        return False

    # 예: "선일다이파스" <-> "선일다이파스 경영정보분석AI 업무자동화AI"
    if len(tokens_a) == 1 and anchor_a in tokens_b:
        return True
    if len(tokens_b) == 1 and anchor_b in tokens_a:
        return True
    return False


def _build_project_alias_map(project_names: list[str]) -> dict[str, str]:
    """
    프로젝트명을 동적으로 병합하기 위한 별칭 맵을 생성한다.
    - 동일 anchor(첫 핵심 토큰) 기준으로
    - 토큰 유사도가 높은 이름을 같은 canonical 프로젝트로 병합
    """
    alias_map: dict[str, str] = {}
    clusters: list[dict] = []

    # 긴 이름을 canonical 후보로 우선 사용
    for name in sorted(set(project_names), key=lambda x: (-len(x), x)):
        anchor = _project_anchor(name)
        assigned = False
        for cluster in clusters:
            if anchor and cluster["anchor"] and anchor != cluster["anchor"]:
                continue
            similarity = _name_similarity(name, cluster["canonical"])
            if similarity >= _PROJECT_SIMILARITY_THRESHOLD or _should_merge_by_anchor(name, cluster["canonical"]):
                cluster["names"].append(name)
                # 더 설명적인(긴) 이름을 canonical로 유지
                if len(name) > len(cluster["canonical"]):
                    cluster["canonical"] = name
                assigned = True
                break
        if not assigned:
            clusters.append({"anchor": anchor, "canonical": name, "names": [name]})

    for cluster in clusters:
        canonical = cluster["canonical"]
        for name in cluster["names"]:
            alias_map[name] = canonical
    return alias_map


def _infer_company_name(project_name: str, source_names: list[str]) -> str:
    company_key, company_label = _infer_company_identity(project_name, source_names)
    return company_label if company_label else (company_key or "기타")


def _normalize_company_token(token: str) -> str:
    value = (token or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    for suffix in _COMPANY_SUFFIXES:
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
            break
    return re.sub(r"[^가-힣a-z0-9]+", "", lowered)


def _extract_company_tokens(project_name: str, source_names: list[str]) -> list[str]:
    """
    회사 식별용 후보 토큰을 추출한다.
    - 우선 source_names(원본 이름)와 project_name에서 첫 토큰을 사용
    - 비어 있으면 기존 _project_tokens fallback 사용
    """
    raw_candidates: list[str] = []
    for name in [project_name, *source_names]:
        m = re.search(r"[가-힣A-Za-z0-9]+", (name or ""))
        if m:
            raw_candidates.append(m.group(0).strip())
    if raw_candidates:
        return raw_candidates
    return _project_tokens(project_name)


def _prefer_company_label(candidates: list[str]) -> str:
    if not candidates:
        return ""
    unique = []
    for item in candidates:
        if item and item not in unique:
            unique.append(item)
    if not unique:
        return ""
    # 법인/그룹 표기가 있는 이름을 우선해 사람이 읽기 쉬운 라벨을 유지한다.
    with_suffix = [c for c in unique if any(s in c.lower() for s in _COMPANY_SUFFIXES)]
    pool = with_suffix or unique
    return sorted(pool, key=lambda x: (-len(x), x))[0]


def _infer_company_identity(project_name: str, source_names: list[str]) -> tuple[str, str]:
    candidates = _extract_company_tokens(project_name, source_names)
    if not candidates:
        return "기타", "기타"

    normalized_keys = [_normalize_company_token(c) for c in candidates if c]
    normalized_keys = [k for k in normalized_keys if k]
    company_key = normalized_keys[0] if normalized_keys else "기타"

    # 동일 key 후보 중 가장 설명적인 라벨을 선택
    same_key_labels = [
        c for c in candidates
        if _normalize_company_token(c) == company_key
    ]
    company_label = _prefer_company_label(same_key_labels or candidates) or "기타"
    return company_key, company_label


async def aggregate_tool(
    records_json: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """Aggregate parsed task rows by project."""
    records: list = []

    if records_json.strip():
        records = load_json_records(records_json)
        if not records and tool_context is None:
            return {"error": "records_json 파싱 실패: 유효한 records 배열을 찾지 못했습니다."}
    elif tool_context is not None:
        parsed = tool_context.state.get("parsed_records") or {}
        if isinstance(parsed, dict):
            raw = parsed.get("records") or []
            records = raw if isinstance(raw, list) else []
    else:
        return {"error": "취합할 입력이 없습니다."}

    if not records and tool_context is not None:
        parsed = tool_context.state.get("parsed_records") or {}
        if isinstance(parsed, dict):
            raw = parsed.get("records") or []
            records = raw if isinstance(raw, list) else []

    if not records:
        return {"error": "파싱된 레코드가 없어 취합을 수행할 수 없습니다."}
    try:
        records = [
            TaskItem.model_validate(_normalize_task_record(record)).model_dump(mode="json")
            for record in records
        ]
    except ValidationError as exc:
        return {"error": f"입력 레코드 검증 실패(TaskItem): {_format_validation_error(exc)}"}

    project_names = [str(r.get("project_name") or "").strip() for r in records if str(r.get("project_name") or "").strip()]
    alias_map = _build_project_alias_map(project_names)

    by_project: dict[str, list[dict]] = defaultdict(list)
    source_names_by_project: dict[str, set[str]] = defaultdict(set)
    for row in records:
        name = str(row.get("project_name") or "").strip()
        if not name:
            continue
        canonical_name = alias_map.get(name, name)
        by_project[canonical_name].append(row)
        source_names_by_project[canonical_name].add(name)

    aggregates: list[dict] = []
    for project_name, items in by_project.items():
        total = len(items)
        completed = sum(1 for r in items if str(r.get("status") or "").strip() == "완료")
        completion_rate = (completed / total) if total else 0.0

        center_counter: dict[str, dict[str, int]] = defaultdict(lambda: {"completed": 0, "total": 0})
        member_counter: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"completed": 0, "total": 0})
        group_counter: dict[str, dict] = defaultdict(
            lambda: {"tasks": [], "completed": 0, "total": 0}
        )
        issues: list[str] = []
        next_week_plans: list[str] = []

        for row in items:
            status = str(row.get("status") or "").strip()
            is_completed = status == "완료"
            center_name = str(row.get("center_name") or "").strip() or "미분류"
            member_name = str(row.get("member_name") or "").strip() or "미분류"
            group_name = str(row.get("group_name") or "").strip() or "기타"
            summary = str(row.get("summary") or "").strip()

            center_counter[center_name]["total"] += 1
            member_counter[(member_name, center_name)]["total"] += 1
            group_counter[group_name]["total"] += 1
            group_counter[group_name]["tasks"].append(row)
            if is_completed:
                center_counter[center_name]["completed"] += 1
                member_counter[(member_name, center_name)]["completed"] += 1
                group_counter[group_name]["completed"] += 1

            if row.get("has_issue_keyword"):
                issues.append(summary)
            if row.get("next_start") or row.get("next_end"):
                if summary:
                    next_week_plans.append(summary)

        centers = [
            {"center_name": center, "completed": c["completed"], "total": c["total"]}
            for center, c in center_counter.items()
        ]
        members = [
            {
                "member_name": member,
                "center_name": center,
                "completed": c["completed"],
                "total": c["total"],
            }
            for (member, center), c in member_counter.items()
        ]
        groups = [
            {
                "group_name": group,
                "tasks": value["tasks"],
                "completed": value["completed"],
                "total": value["total"],
            }
            for group, value in group_counter.items()
        ]

        aggregates.append(
            {
                "project_name": project_name,
                "source_project_names": sorted(source_names_by_project.get(project_name, {project_name})),
                "groups": groups,
                "centers": centers,
                "members": members,
                "total_tasks": total,
                "completed_tasks": completed,
                "completion_rate": round(completion_rate, 4),
                "status": _status_emoji(items),
                "issues": issues,
                "next_week_plans": next_week_plans,
            }
        )

    company_groups_map: dict[str, list[dict]] = defaultdict(list)
    company_label_map: dict[str, str] = {}
    for aggregate in aggregates:
        source_names = aggregate.get("source_project_names") or [aggregate.get("project_name")]
        company_key, company_label = _infer_company_identity(
            str(aggregate.get("project_name") or ""),
            list(source_names),
        )
        company_label_map[company_key] = company_label
        company_groups_map[company_key].append(
            {
                "project_name": aggregate.get("project_name"),
                "source_project_names": source_names,
                "status": aggregate.get("status", "🔴"),
            }
        )

    company_groups = [
        {
            "company_name": company_label_map.get(company_key, company_key),
            "projects": projects,
        }
        for company_key, projects in company_groups_map.items()
    ]

    result = {
        "project_aggregates": aggregates,
        "company_groups": company_groups,
    }
    try:
        validated_aggregates = [
            ProjectAggregate.model_validate(aggregate).model_dump(mode="json")
            for aggregate in aggregates
        ]
        result["project_aggregates"] = validated_aggregates
    except ValidationError as exc:
        return {"error": f"취합 결과 검증 실패(ProjectAggregate): {_format_validation_error(exc)}"}

    if tool_context is not None:
        tool_context.state["aggregated_data"] = result
    return result

