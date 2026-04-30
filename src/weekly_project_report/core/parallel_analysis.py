"""Parallel analysis workflow using Workflow fan-out and JoinNode.

Aggregator 결과를 두 에이전트가 동시에 분석한 뒤 JoinNode에서 합산한다.

Workflow 구조:
    START
     ├─ risk_analyzer_agent       (리스크·이슈·의사결정 사항 병렬 분석)
     └─ achievement_analyzer_agent (성과·진척·다음 주 계획 병렬 분석)
         └─ analysis_join_node    (JoinNode: 두 브랜치 완료 대기)
             └─ merge_analysis_results_node (결과 병합 → state["analysis_result"])
"""

from __future__ import annotations

from datetime import date
import json
from typing import Any

from google.adk import Agent
from google.adk.agents.context import Context
from google.adk.tools.tool_context import ToolContext
from google.adk.workflow import FunctionNode, JoinNode, Workflow
from pydantic import ValidationError

from ..guardrails import GUARDRAIL_CALLBACKS
from ..schemas.model import AnalysisResult, ProjectAggregate
from .analyze_tool import (
    _STATUS_SUMMARY_TEXT,
    _determine_overall_status,
    _format_validation_error,
    _has_delay_signal,
    _impact_from_issue,
    _status_label,
)
from .model_config import AGENT_MODEL

_AGENT_GUARDRAIL_KWARGS = {
    "before_model_callback": GUARDRAIL_CALLBACKS["before_model_call"],
    "after_model_callback": GUARDRAIL_CALLBACKS["after_model_call"],
    "before_agent_callback": GUARDRAIL_CALLBACKS["before_agent_call"],
    "after_tool_callback": GUARDRAIL_CALLBACKS["after_tool_call"],
}


# ══════════════════════════════════════════════════════════════
#  Branch 1 — 리스크·이슈·의사결정 분석 도구
# ══════════════════════════════════════════════════════════════

async def analyze_risks_tool(
    tool_context: ToolContext | None = None,
) -> dict:
    """집계 데이터에서 리스크·이슈·의사결정 사항을 추출한다."""
    aggregated_data = (
        tool_context.state.get("aggregated_data") if tool_context else {}
    )
    if not isinstance(aggregated_data, dict):
        return {"error": "aggregated_data가 없습니다."}

    project_aggregates = aggregated_data.get("project_aggregates") or []
    if not project_aggregates:
        return {"error": "project_aggregates가 없습니다."}

    try:
        project_aggregates = [
            ProjectAggregate.model_validate(p).model_dump(mode="json")
            for p in project_aggregates
        ]
    except ValidationError as exc:
        return {"error": f"입력 검증 실패: {_format_validation_error(exc)}"}

    issue_details: list[dict] = []
    risks: list[dict] = []
    top_issues: list[dict] = []
    decisions_needed: list[str] = []
    status_counter: dict[str, int] = {"GREEN": 0, "YELLOW": 0, "RED": 0}

    for project in project_aggregates:
        project_name = str(project.get("project_name") or "").strip() or "미분류"
        completion_rate = float(project.get("completion_rate") or 0.0)
        issues = [
            str(i).strip()
            for i in (project.get("issues") or [])
            if str(i).strip()
        ]
        project_status = str(project.get("status") or "").strip()

        has_delay = _has_delay_signal(project_status, issues)
        status = _status_label(completion_rate, len(issues), has_delay)
        status_counter[status] += 1

        for issue in issues:
            impact = _impact_from_issue(issue)
            decision_required = status == "RED"
            decision_detail = (
                f"{project_name}의 우선순위 조정 및 지원 여부 판단이 필요합니다."
                if decision_required
                else f"{project_name}는 현 대응 체계로 관리 가능합니다."
            )
            issue_details.append({
                "project_name": project_name,
                "issue": issue,
                "impact": impact,
                "current_state": "ONGOING",
                "response_plan": "우선순위를 확정하고 담당자를 지정해 대응합니다.",
                "decision_required": {
                    "required": decision_required,
                    "detail": decision_detail,
                },
            })
            top_issues.append({
                "project_name": project_name,
                "issue": issue,
                "impact": impact,
                "summary": "프로젝트 진행 안정성에 영향 가능성이 있습니다.",
            })
            if decision_required:
                decisions_needed.append(decision_detail)
            risks.append({
                "risk": f"{project_name}: {issue}로 인한 진행 차질 가능성",
                "impact_level": "HIGH" if status == "RED" else "MEDIUM",
                "likelihood": "HIGH" if status == "RED" else "MEDIUM",
                "status": "ONGOING",
                "mitigation": "이슈 대응 우선순위를 명확히 하고 주 단위로 점검합니다.",
            })

    overall_status = _determine_overall_status(status_counter)

    if not top_issues:
        top_issues = [{
            "project_name": "",
            "issue": "주요 이슈 없음",
            "impact": "QUALITY",
            "summary": "주요 이슈 없음",
        }]
    if not risks:
        risks = [{
            "risk": "현재 확인된 주요 리스크는 없습니다.",
            "impact_level": "LOW",
            "likelihood": "LOW",
            "status": "MITIGATED",
            "mitigation": "현재 운영 방식을 유지하며 변화 신호를 모니터링합니다.",
        }]
    if not decisions_needed:
        decisions_needed = ["즉시 필요한 추가 의사결정 없음"]

    result = {
        "overall_status": overall_status,
        "status_summary": _STATUS_SUMMARY_TEXT[overall_status],
        "top_issues": top_issues[:10],
        "decisions_needed": decisions_needed[:10],
        "issue_details": issue_details,
        "risks": risks,
    }
    if tool_context:
        tool_context.state["risk_analysis"] = result
    return result


# ══════════════════════════════════════════════════════════════
#  Branch 2 — 성과·진척·다음 주 계획 분석 도구
# ══════════════════════════════════════════════════════════════

async def analyze_achievements_tool(
    tool_context: ToolContext | None = None,
) -> dict:
    """집계 데이터에서 프로젝트별 성과·진척·다음 주 계획을 추출한다."""
    aggregated_data = (
        tool_context.state.get("aggregated_data") if tool_context else {}
    )
    if not isinstance(aggregated_data, dict):
        return {"error": "aggregated_data가 없습니다."}

    project_aggregates = aggregated_data.get("project_aggregates") or []
    if not project_aggregates:
        return {"error": "project_aggregates가 없습니다."}

    try:
        project_aggregates = [
            ProjectAggregate.model_validate(p).model_dump(mode="json")
            for p in project_aggregates
        ]
    except ValidationError as exc:
        return {"error": f"입력 검증 실패: {_format_validation_error(exc)}"}

    projects: list[dict] = []
    next_week_focus: list[str] = []
    seen_plans: set[str] = set()

    for project in project_aggregates:
        project_name = str(project.get("project_name") or "").strip() or "미분류"
        completion_rate = float(project.get("completion_rate") or 0.0)
        issues = [
            str(i).strip()
            for i in (project.get("issues") or [])
            if str(i).strip()
        ]
        project_status = str(project.get("status") or "").strip()
        plans = [
            str(i).strip()
            for i in (project.get("next_week_plans") or [])
            if str(i).strip()
        ]
        groups = project.get("groups") or []

        has_delay = _has_delay_signal(project_status, issues)
        status = _status_label(completion_rate, len(issues), has_delay)

        key_achievements: list[str] = []
        project_task_summaries: list[str] = []
        for group in groups:
            for task in (group.get("tasks") or []):
                task_status = str(task.get("status") or "").strip()
                summary = str(task.get("summary") or "").strip()
                if summary:
                    project_task_summaries.append(summary)
                if task_status == "완료" and summary:
                    key_achievements.append(summary)
            if len(key_achievements) >= 3:
                break

        if not key_achievements:
            key_achievements = project_task_summaries[:3] or ["작업 내용 없음"]

        key_issues = issues[:3] or ["주요 이슈 없음"]

        for plan in plans[:2]:
            if plan not in seen_plans:
                seen_plans.add(plan)
                next_week_focus.append(plan)

        projects.append({
            "project_name": project_name,
            "status": status,
            "key_achievements": key_achievements[:3],
            "key_issues": key_issues,
            "next_week_plan": plans[:3] or project_task_summaries[:3] or [],
        })

    result = {
        "projects": projects,
        "next_week_focus": next_week_focus[:20],
    }
    if tool_context:
        tool_context.state["achievement_analysis"] = result
    return result


# ══════════════════════════════════════════════════════════════
#  에이전트 정의
# ══════════════════════════════════════════════════════════════

risk_analyzer_agent = Agent(
    name="risk_analyzer_agent",
    model=AGENT_MODEL,
    description=(
        "집계된 프로젝트 데이터에서 리스크·이슈·의사결정 사항을 병렬로 분석한다. "
        "parallel_analysis_workflow의 팬아웃 브랜치 1."
    ),
    instruction=(
        "analyze_risks_tool을 호출하여 리스크 및 이슈 분석을 수행하라.\n"
        "도구 호출 후 반환된 JSON을 그대로 출력하라. 추가 설명은 하지 않는다."
    ),
    tools=[analyze_risks_tool],
    output_key="risk_analysis",
    **_AGENT_GUARDRAIL_KWARGS,
)

achievement_analyzer_agent = Agent(
    name="achievement_analyzer_agent",
    model=AGENT_MODEL,
    description=(
        "집계된 프로젝트 데이터에서 성과·진척·다음 주 계획을 병렬로 분석한다. "
        "parallel_analysis_workflow의 팬아웃 브랜치 2."
    ),
    instruction=(
        "analyze_achievements_tool을 호출하여 성과 및 계획 분석을 수행하라.\n"
        "도구 호출 후 반환된 JSON을 그대로 출력하라. 추가 설명은 하지 않는다."
    ),
    tools=[analyze_achievements_tool],
    output_key="achievement_analysis",
    **_AGENT_GUARDRAIL_KWARGS,
)


# ══════════════════════════════════════════════════════════════
#  Fan-in: 두 브랜치 결과 병합
# ══════════════════════════════════════════════════════════════

def merge_analysis_results(ctx: Context, node_input: Any) -> dict:
    """``risk_analysis`` 와 ``achievement_analysis`` 를 합쳐 ``analysis_result`` 를 구성한다.

    두 에이전트는 각각 ``output_key`` 로 state에 결과를 저장하므로
    ``node_input`` 값과 무관하게 state에서 직접 읽는다.
    """
    risk: Any = ctx.state.get("risk_analysis", {})
    achievement: Any = ctx.state.get("achievement_analysis", {})

    # LLM output_key로 저장된 값이 JSON 문자열일 수 있다.
    if isinstance(risk, str):
        try:
            risk = json.loads(risk)
        except Exception:
            risk = {}
    if isinstance(achievement, str):
        try:
            achievement = json.loads(achievement)
        except Exception:
            achievement = {}

    if not isinstance(risk, dict) or not isinstance(achievement, dict):
        return {"error": "분석 결과를 병합할 수 없습니다."}

    if risk.get("error") or achievement.get("error"):
        return {"error": risk.get("error") or achievement.get("error")}

    overall_status: str = risk.get("overall_status", "GREEN")
    result: dict[str, Any] = {
        "report_date": date.today().isoformat(),
        "overall_status": overall_status,
        "executive_summary": {
            "status_summary": risk.get("status_summary", _STATUS_SUMMARY_TEXT[overall_status]),
            "top_issues": risk.get("top_issues", [])[:10],
            "decisions_needed": risk.get("decisions_needed", [])[:10],
        },
        "projects": achievement.get("projects", []),
        "issue_details": risk.get("issue_details", []),
        "risks": risk.get("risks", []),
        "next_week_focus": achievement.get("next_week_focus", [])[:20],
    }

    try:
        validated = AnalysisResult.model_validate(result).model_dump(mode="json")
    except ValidationError as exc:
        return {"error": f"분석 결과 검증 실패(AnalysisResult): {_format_validation_error(exc)}"}

    ctx.state["analysis_result"] = validated
    return validated


analysis_join_node = JoinNode(name="analysis_join")

merge_analysis_results_node = FunctionNode(
    func=merge_analysis_results,
    name="merge_analysis_results",
)


# ══════════════════════════════════════════════════════════════
#  Workflow 정의
# ══════════════════════════════════════════════════════════════

# START → (risk_analyzer_agent, achievement_analyzer_agent)  ← 팬아웃
#       → analysis_join_node                                  ← JoinNode 팬인
#       → merge_analysis_results_node
parallel_analysis_workflow = Workflow(
    name="ParallelAnalysisWorkflow",
    edges=[
        ("START", (risk_analyzer_agent, achievement_analyzer_agent)),
        ((risk_analyzer_agent, achievement_analyzer_agent), analysis_join_node),
        (analysis_join_node, merge_analysis_results_node),
    ],
)
