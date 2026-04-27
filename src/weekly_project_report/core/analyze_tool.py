"""Analysis tool for weekly report pipeline."""

from __future__ import annotations

from datetime import date
import json

from google.adk.tools.tool_context import ToolContext
from pydantic import ValidationError

from ..schemas.model import AnalysisResult
from ..schemas.model import ProjectAggregate


def _has_delay_signal(project_status: str, issues: list[str]) -> bool:
    """RED는 '지연' 신호가 있을 때만 부여한다."""
    normalized_status = (project_status or "").strip()
    if normalized_status in {"지연", "RED", "🔴"}:
        return True
    delay_keywords = (
        "지연",
        "차질",
        "납기",
        "마일스톤",
        "일정 미준수",
        "기한 미준수",
        "일정 지체",
        "딜레이",
    )
    return any(any(keyword in issue for keyword in delay_keywords) for issue in issues)


def _status_label(completion_rate: float, issue_count: int, has_delay: bool) -> str:
    if has_delay:
        return "RED"
    if completion_rate < 0.8 or issue_count > 0:
        return "YELLOW"
    return "GREEN"


def _impact_from_issue(text: str) -> str:
    normalized = text.lower()
    if any(k in normalized for k in ("지연", "일정", "납기", "차질")):
        return "SCHEDULE"
    if any(k in normalized for k in ("비용", "예산", "투입", "리소스")):
        return "COST"
    return "QUALITY"


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts[:5])


async def analyze_tool(
    aggregated_data_json: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """Generate executive analysis JSON from project aggregates."""
    aggregated_data: dict = {}
    if aggregated_data_json.strip():
        try:
            decoded = json.loads(aggregated_data_json)
            if isinstance(decoded, dict):
                aggregated_data = decoded
            else:
                return {"error": "aggregated_data_json은 JSON 객체여야 합니다."}
        except Exception as exc:
            return {"error": f"aggregated_data_json 파싱 실패: {exc}"}
    elif tool_context is not None:
        state_value = tool_context.state.get("aggregated_data")
        aggregated_data = state_value if isinstance(state_value, dict) else {}
    else:
        return {"error": "분석할 취합 데이터가 없습니다."}

    project_aggregates = aggregated_data.get("project_aggregates") if isinstance(aggregated_data, dict) else None
    if not project_aggregates and tool_context is not None:
        # self-heal: aggregator 단계가 state를 남기지 못한 경우 parsed_records로 재취합 시도
        parsed = tool_context.state.get("parsed_records") or {}
        records = parsed.get("records") if isinstance(parsed, dict) else None
        if isinstance(records, list) and records:
            try:
                from .aggregate_tool import aggregate_tool

                reaggregated = await aggregate_tool(
                    records_json=json.dumps({"records": records}, ensure_ascii=False),
                    tool_context=tool_context,
                )
                if isinstance(reaggregated, dict):
                    aggregated_data = reaggregated
                    project_aggregates = aggregated_data.get("project_aggregates")
            except Exception:
                project_aggregates = None
    if not project_aggregates:
        diagnostics = {
            "has_aggregated_data_json_arg": bool(aggregated_data_json.strip()),
            "state_has_aggregated_data": bool(
                tool_context is not None and isinstance(tool_context.state.get("aggregated_data"), dict)
            ),
            "state_parsed_records_count": (
                len((tool_context.state.get("parsed_records") or {}).get("records") or [])
                if tool_context is not None and isinstance(tool_context.state.get("parsed_records"), dict)
                else 0
            ),
            "project_aggregates_count": 0,
            "expected_input_key": "aggregated_data_json",
        }
        return {
            "error": (
                "project_aggregates가 없어 분석을 수행할 수 없습니다. "
                "aggregated_data_json 또는 parsed_records.records를 확인해 주세요."
            ),
            "diagnostics": diagnostics,
        }
    try:
        project_aggregates = [
            ProjectAggregate.model_validate(project).model_dump(mode="json")
            for project in project_aggregates
        ]
    except ValidationError as exc:
        return {"error": f"분석 입력 검증 실패(ProjectAggregate): {_format_validation_error(exc)}"}

    projects: list[dict] = []
    issue_details: list[dict] = []
    risks: list[dict] = []
    top_issues: list[dict] = []
    decisions_needed: list[str] = []
    next_week_focus: list[str] = []
    status_counter = {"GREEN": 0, "YELLOW": 0, "RED": 0}

    for project in project_aggregates:
        project_name = str(project.get("project_name") or "").strip() or "미분류 프로젝트"
        completion_rate = float(project.get("completion_rate") or 0.0)
        issues = [str(i).strip() for i in (project.get("issues") or []) if str(i).strip()]
        project_status = str(project.get("status") or "").strip()
        plans = [str(i).strip() for i in (project.get("next_week_plans") or []) if str(i).strip()]
        groups = project.get("groups") or []

        has_delay = _has_delay_signal(project_status, issues)
        status = _status_label(completion_rate, len(issues), has_delay)
        status_counter[status] += 1
        key_achievements: list[str] = []
        project_task_summaries: list[str] = []
        for group in groups:
            tasks = group.get("tasks") or []
            for task in tasks:
                task_status = str(task.get("status") or "").strip()
                summary = str(task.get("summary") or "").strip()
                if summary:
                    project_task_summaries.append(summary)
                if task_status == "완료" and summary:
                    key_achievements.append(summary)
            if len(key_achievements) >= 3:
                break
        if not key_achievements:
            key_achievements = project_task_summaries[:3] if project_task_summaries else ["작업 내용 없음"]

        key_issues = issues[:3]
        if not key_issues:
            key_issues = ["주요 이슈 없음"]

        for issue in issues:
            impact = _impact_from_issue(issue)
            current_state = "ONGOING"
            response_plan = "우선순위를 확정하고 담당자를 지정해 대응합니다."
            decision_required = status == "RED"
            decision_detail = (
                f"{project_name}의 우선순위 조정 및 지원 여부 판단이 필요합니다."
                if decision_required
                else f"{project_name}는 현 대응 체계로 관리 가능합니다."
            )

            detail = {
                "project_name": project_name,
                "issue": issue,
                "impact": impact,
                "current_state": current_state,
                "response_plan": response_plan,
                "decision_required": {
                    "required": decision_required,
                    "detail": decision_detail,
                },
            }
            issue_details.append(detail)
            top_issues.append(
                {
                    "project_name": project_name,
                    "issue": issue,
                    "impact": impact,
                    "summary": "프로젝트 진행 안정성에 영향 가능성이 있습니다.",
                }
            )
            if decision_required:
                decisions_needed.append(decision_detail)
            risks.append(
                {
                    "risk": f"{project_name}: {issue}로 인한 진행 차질 가능성",
                    "impact_level": "HIGH" if status == "RED" else "MEDIUM",
                    "likelihood": "HIGH" if status == "RED" else "MEDIUM",
                    "status": "ONGOING",
                    "mitigation": "이슈 대응 우선순위를 명확히 하고 진행 상황을 주 단위로 점검합니다.",
                }
            )

        if plans:
            next_week_focus.extend(plans[:2])

        projects.append(
            {
                "project_name": project_name,
                "status": status,
                "key_achievements": key_achievements[:3],
                "key_issues": key_issues,
                "next_week_plan": plans[:3] if plans else (project_task_summaries[:3] if project_task_summaries else []),
            }
        )

    # 중복 제거 (순서 유지)
    seen: set[str] = set()
    dedup_focus: list[str] = []
    for item in next_week_focus:
        if item not in seen:
            seen.add(item)
            dedup_focus.append(item)

    overall_status = "GREEN"
    if status_counter["RED"] > 0:
        overall_status = "RED"
    elif status_counter["YELLOW"] > 0:
        overall_status = "YELLOW"

    status_summary = (
        "리스크 우선 대응이 필요한 프로젝트가 포함되어 있습니다."
        if overall_status == "RED"
        else "일부 이슈가 있어 모니터링이 필요합니다."
        if overall_status == "YELLOW"
        else "전반적으로 안정적인 진행 상태입니다."
    )

    if not top_issues:
        top_issues = [
            {
                "project_name": p["project_name"],
                "issue": "주요 이슈 없음",
                "impact": "QUALITY",
                "summary": "주요 이슈 없음",
            }
            for p in projects
        ]
    if not risks:
        risks = [
            {
                "risk": "현재 확인된 주요 리스크는 없습니다.",
                "impact_level": "LOW",
                "likelihood": "LOW",
                "status": "MITIGATED",
                "mitigation": "현재 운영 방식을 유지하며 변화 신호를 모니터링합니다.",
            }
        ]
    if not decisions_needed:
        decisions_needed = ["즉시 필요한 추가 의사결정 없음"]

    result = {
        "report_date": date.today().isoformat(),
        "overall_status": overall_status,
        "executive_summary": {
            "status_summary": status_summary,
            "top_issues": top_issues[:10],
            "decisions_needed": decisions_needed[:10],
        },
        "projects": projects,
        "issue_details": issue_details,
        "risks": risks,
        "next_week_focus": dedup_focus[:20],
    }
    try:
        validated_result = AnalysisResult.model_validate(result).model_dump(mode="json")
    except ValidationError as exc:
        return {"error": f"분석 결과 검증 실패(AnalysisResult): {_format_validation_error(exc)}"}

    if tool_context is not None:
        tool_context.state["analysis_result"] = validated_result
    return validated_result

