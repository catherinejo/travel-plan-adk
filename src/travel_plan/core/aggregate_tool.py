"""Aggregation tool for weekly report pipeline."""

from __future__ import annotations

from collections import defaultdict
import re

from google.adk.tools.tool_context import ToolContext

from ._utils import load_json_records


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
            if similarity >= _PROJECT_SIMILARITY_THRESHOLD:
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
    # fallback: 첫 토큰을 회사명으로 사용
    tokens = _project_tokens(project_name)
    return tokens[0] if tokens else "기타"


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
    for aggregate in aggregates:
        source_names = aggregate.get("source_project_names") or [aggregate.get("project_name")]
        company_name = _infer_company_name(str(aggregate.get("project_name") or ""), list(source_names))
        company_groups_map[company_name].append(
            {
                "project_name": aggregate.get("project_name"),
                "source_project_names": source_names,
                "status": aggregate.get("status", "🔴"),
            }
        )

    company_groups = [
        {"company_name": company_name, "projects": projects}
        for company_name, projects in company_groups_map.items()
    ]

    result = {
        "project_aggregates": aggregates,
        "company_groups": company_groups,
    }
    if tool_context is not None:
        tool_context.state["aggregated_data"] = result
    return result

