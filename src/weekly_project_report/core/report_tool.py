"""Report writing/rendering tools for weekly report pipeline."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import tempfile

from google.adk.tools.tool_context import ToolContext

from ._utils import parse_json_dict

_TRUNCATE_MAX_LEN = 120
_MAX_TASK_ITEMS = 5
_MAX_SUMMARY_WEEKLY_CHANGES = 2
_MAX_PLAN_ITEMS_PER_PROJECT = 1


def _truncate_note(text: str, max_len: int = _TRUNCATE_MAX_LEN) -> str:
    t = (text or "").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _build_project_table(project_aggregates: list[dict]) -> str:
    summary_lines = [
        "| 프로젝트 | 상태 | 진행 요지 |",
        "|---|---|---|",
    ]
    detail_lines = ["", "### 프로젝트별 작업 리스트"]

    for p in project_aggregates:
        name = str(p.get("project_name") or "-")
        status = str(p.get("status") or "-")
        issues = p.get("issues") or []
        plans = p.get("next_week_plans") or []
        parts: list[str] = []
        if issues:
            parts.append("이슈: " + _truncate_note("; ".join(str(x) for x in issues[:3])))
        if plans:
            parts.append("다음 주: " + _truncate_note("; ".join(str(x) for x in plans[:2])))
        narrative = " / ".join(parts) if parts else "특이사항 없음"
        summary_lines.append(f"| {name} | {status} | {narrative} |")

        tasks: list[dict] = []
        for group in p.get("groups") or []:
            for task in group.get("tasks") or []:
                if isinstance(task, dict):
                    tasks.append(task)

        detail_lines.append(f"- {name}")
        if not tasks:
            detail_lines.append("  - 작업 내역 없음")
            continue

        completed = [str(t.get("summary") or "").strip() for t in tasks if str(t.get("status") or "").strip() == "완료"]
        in_progress = [str(t.get("summary") or "").strip() for t in tasks if str(t.get("status") or "").strip() in {"진행", "진행중", "진행 중"}]

        if completed:
            detail_lines.append("  - 완료 작업")
            for item in completed[:_MAX_TASK_ITEMS]:
                if item:
                    detail_lines.append(f"    - {item}")
        if in_progress:
            detail_lines.append("  - 진행 작업")
            for item in in_progress[:_MAX_TASK_ITEMS]:
                if item:
                    detail_lines.append(f"    - {item}")

        if not completed and not in_progress:
            for task in tasks[:_MAX_TASK_ITEMS]:
                summary = str(task.get("summary") or "").strip()
                if summary:
                    detail_lines.append(f"  - {summary}")

    return "\n".join(summary_lines + detail_lines)


async def write_report_tool(
    analysis_result_json: str = "",
    aggregated_data_json: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """Write markdown weekly report from analysis/aggregate outputs."""
    analysis_result: dict = {}
    aggregated_data: dict = {}

    state = tool_context.state if tool_context is not None else None

    if analysis_result_json.strip():
        analysis_result, err = parse_json_dict(analysis_result_json, "analysis_result_json")
        if err:
            return err
    elif state is not None:
        value = state.get("analysis_result")
        analysis_result = value if isinstance(value, dict) else {}

    if aggregated_data_json.strip():
        aggregated_data, err = parse_json_dict(aggregated_data_json, "aggregated_data_json")
        if err:
            return err
    elif state is not None:
        value = state.get("aggregated_data")
        aggregated_data = value if isinstance(value, dict) else {}

    project_aggregates = aggregated_data.get("project_aggregates") or []
    if not project_aggregates:
        diagnostics = {
            "has_analysis_result_json_arg": bool(analysis_result_json.strip()),
            "has_aggregated_data_json_arg": bool(aggregated_data_json.strip()),
            "state_has_analysis_result": bool(
                tool_context is not None and isinstance(tool_context.state.get("analysis_result"), dict)
            ),
            "state_has_aggregated_data": bool(
                tool_context is not None and isinstance(tool_context.state.get("aggregated_data"), dict)
            ),
            "state_parsed_records_count": (
                len((tool_context.state.get("parsed_records") or {}).get("records") or [])
                if tool_context is not None and isinstance(tool_context.state.get("parsed_records"), dict)
                else 0
            ),
            "project_aggregates_count": 0,
            "expected_path": "parser -> aggregate_tool -> analyze_tool -> write_report_tool",
        }
        return {
            "error": "리포트 작성을 위한 데이터가 없습니다. aggregated_data_json이 비어 있습니다.",
            "diagnostics": diagnostics,
        }

    raw_executive_summary = analysis_result.get("executive_summary")
    executive_summary = raw_executive_summary if isinstance(raw_executive_summary, dict) else {}
    raw_projects_from_analysis = analysis_result.get("projects")
    projects_from_analysis = raw_projects_from_analysis if isinstance(raw_projects_from_analysis, list) else []
    raw_risks = analysis_result.get("risks")
    risks = raw_risks if isinstance(raw_risks, list) else []
    raw_next_week_focus = analysis_result.get("next_week_focus")
    next_week_focus = raw_next_week_focus if isinstance(raw_next_week_focus, list) else []
    # backward compatibility: 예전 analyze_tool 출력도 허용
    raw_highlights = analysis_result.get("highlights")
    highlights = raw_highlights if isinstance(raw_highlights, list) else []
    raw_risk_projects = analysis_result.get("risk_projects")
    risk_projects = raw_risk_projects if isinstance(raw_risk_projects, list) else []
    raw_recommendations = analysis_result.get("recommendations")
    recommendations = raw_recommendations if isinstance(raw_recommendations, list) else []

    summary_lines: list[str] = []
    status_summary = str(executive_summary.get("status_summary") or "").strip()
    if status_summary:
        summary_lines.append(status_summary)
    raw_weekly_changes = executive_summary.get("weekly_changes")
    weekly_changes = raw_weekly_changes if isinstance(raw_weekly_changes, list) else []
    for change in weekly_changes[:_MAX_SUMMARY_WEEKLY_CHANGES]:
        if not isinstance(change, dict):
            continue
        project_name = str(change.get("project_name") or "-")
        detail = str(change.get("detail") or "").strip()
        if detail:
            summary_lines.append(f"{project_name}: {detail}")
    if not summary_lines and highlights:
        summary_lines = [str(line) for line in highlights[:3]]
    if not summary_lines:
        summary_lines = ["핵심 요약 데이터가 없습니다."]

    risk_lines: list[str] = []
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        risk_text = str(risk.get("risk") or "").strip()
        mitigation = str(risk.get("mitigation") or "").strip()
        if risk_text and mitigation:
            risk_lines.append(f"- {risk_text} / 대응: {mitigation}")
        elif risk_text:
            risk_lines.append(f"- {risk_text}")
    if not risk_lines:
        for risk in risk_projects:
            if not isinstance(risk, dict):
                continue
            pname = str(risk.get("project_name") or "-")
            reason = str(risk.get("risk_reason") or "리스크 요인 확인 필요")
            risk_lines.append(f"- {pname}: {reason}")
    if not risk_lines:
        risk_lines = ["- 주요 리스크 없음"]

    recommendation_lines = [f"- {line}" for line in next_week_focus if str(line).strip()]
    if not recommendation_lines and recommendations:
        recommendation_lines = [f"- {line}" for line in recommendations if str(line).strip()]
    if not recommendation_lines and projects_from_analysis:
        for p in projects_from_analysis[:3]:
            if not isinstance(p, dict):
                continue
            raw_plan_list = p.get("next_week_plan")
            plan_list = raw_plan_list if isinstance(raw_plan_list, list) else []
            for plan in plan_list[:_MAX_PLAN_ITEMS_PER_PROJECT]:
                if str(plan).strip():
                    recommendation_lines.append(f"- {plan}")
    if not recommendation_lines:
        recommendation_lines = ["- 권고 사항 없음"]

    markdown_report = (
        "# 주간 프로젝트 보고서\n\n"
        "## 1. 요약 (Executive Summary)\n"
        + "\n".join(f"- {line}" for line in summary_lines)
        + "\n\n## 2. 프로젝트별 주간 실적 및 작업 내역\n"
        + _build_project_table(project_aggregates)
        + "\n\n## 3. 주요 이슈 및 리스크\n"
        + "\n".join(risk_lines)
        + "\n\n## 4. 다음 주 계획 및 권고 사항\n"
        + "\n".join(recommendation_lines)
        + "\n"
    )

    result = {"markdown_report": markdown_report}
    if tool_context is not None:
        tool_context.state["markdown_report"] = markdown_report
    return result


def _write_basic_pdf(path: Path, title: str, body: str) -> None:
    escaped_body = body.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content_stream = f"BT /F1 12 Tf 50 780 Td ({title}) Tj 0 -20 Td ({escaped_body[:500]}) Tj ET"
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        + f"4 0 obj << /Length {len(content_stream)} >> stream\n{content_stream}\nendstream endobj\n".encode("utf-8")
        + b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"0000000010 00000 n \n0000000060 00000 n \n0000000117 00000 n \n"
        b"0000000243 00000 n \n0000000000 00000 n \n"
        b"trailer << /Root 1 0 R /Size 6 >>\nstartxref\n0\n%%EOF\n"
    )
    path.write_bytes(pdf_bytes)


def _write_pdf_with_cups(path: Path, markdown: str) -> None:
    """
    macOS 기본 cupsfilter를 사용해 UTF-8 텍스트를 PDF로 렌더링한다.
    시스템 폰트 엔진을 쓰므로 한글 깨짐을 크게 줄일 수 있다.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
        tmp.write(markdown)
        tmp_path = Path(tmp.name)

    try:
        cmd = ["/usr/sbin/cupsfilter", str(tmp_path)]
        proc = subprocess.run(cmd, check=True, capture_output=True)
        path.write_bytes(proc.stdout)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


async def render_pdf_function(
    markdown: str = "",
    tool_context: ToolContext | None = None,
) -> dict:
    """Render markdown report to a PDF file path."""
    markdown_value = markdown.strip()
    if not markdown_value and tool_context is not None:
        state = tool_context.state
        # writer 단계 출력 키 변형을 모두 수용한다.
        candidates = [
            state.get("markdown_report"),
            state.get("final_report"),
            state.get("report_markdown"),
        ]
        rendered = state.get("rendered_report")
        if isinstance(rendered, dict):
            candidates.append(rendered.get("markdown_report"))
            candidates.append(rendered.get("report_markdown"))
        markdown_value = next(
            (str(value).strip() for value in candidates if isinstance(value, str) and value.strip()),
            "",
        )
    if not markdown_value:
        return {
            "error": (
                "PDF로 변환할 markdown 내용이 없습니다. "
                "write_report_tool 실행 후 tool_context.state['markdown_report']를 확인해 주세요."
            )
        }

    output_dir = Path("uploads")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = output_dir / f"weekly_report_{stamp}.md"
    pdf_path = output_dir / f"weekly_report_{stamp}.pdf"
    md_path.write_text(markdown_value, encoding="utf-8")

    try:
        _write_pdf_with_cups(pdf_path, markdown_value)
    except Exception as exc:
        # cupsfilter 실패 시 레거시 fallback 유지
        try:
            _write_basic_pdf(pdf_path, "Weekly Report", markdown_value.replace("\n", " "))
        except Exception:
            return {"error": f"PDF 렌더링 실패: {exc}", "markdown_path": str(md_path)}

    result = {"pdf_path": str(pdf_path), "markdown_path": str(md_path)}
    if tool_context is not None:
        tool_context.state["rendered_report"] = result
    return result

