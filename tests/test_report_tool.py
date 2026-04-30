"""Tests for core/report_tool.py — 리포트 작성 도구 단위 테스트."""

import pytest
from weekly_project_report.core.report_tool import _build_project_table, _truncate_note


# ── Mock 객체 ──────────────────────────────────────────────────
class _MockState(dict):
    pass


class _MockToolContext:
    def __init__(self, state: dict | None = None):
        self.state = _MockState(state or {})


# ── _truncate_note ─────────────────────────────────────────────
class TestTruncateNote:
    def test_short_text_unchanged(self):
        assert _truncate_note("짧은 내용") == "짧은 내용"

    def test_long_text_truncated(self):
        long_text = "가" * 200
        result = _truncate_note(long_text)
        assert len(result) <= 120
        assert result.endswith("…")

    def test_newlines_replaced_with_space(self):
        result = _truncate_note("줄\n바꿈")
        assert "\n" not in result

    def test_empty_string(self):
        assert _truncate_note("") == ""

    def test_none_treated_as_empty(self):
        assert _truncate_note(None) == ""  # type: ignore[arg-type]

    def test_exact_max_length_not_truncated(self):
        text = "a" * 120
        result = _truncate_note(text, max_len=120)
        assert not result.endswith("…")


# ── _build_project_table ───────────────────────────────────────
def _make_project(
    name: str = "테스트 프로젝트",
    status: str = "🔵",
    issues: list | None = None,
    plans: list | None = None,
    tasks: list | None = None,
) -> dict:
    return {
        "project_name": name,
        "status": status,
        "issues": issues or [],
        "next_week_plans": plans or [],
        "groups": [{"tasks": tasks or []}],
    }


class TestBuildProjectTable:
    def test_section_header_contains_project_name(self):
        result = _build_project_table([_make_project(name="테스트 프로젝트")])
        assert "테스트 프로젝트" in result
        assert "###" in result          # 섹션 헤더

    def test_task_table_columns_present(self):
        result = _build_project_table([_make_project()])
        assert "| 구분 | 작업 내용 |" in result

    def test_issue_shown_as_blockquote(self):
        project = _make_project(issues=["API 연동 지연"])
        result = _build_project_table([project])
        assert "**이슈**" in result
        assert "API 연동 지연" in result

    def test_next_week_plan_shown_as_blockquote(self):
        project = _make_project(plans=["기능 개발 완료 예정"])
        result = _build_project_table([project])
        assert "**다음 주**" in result
        assert "기능 개발 완료 예정" in result

    def test_completed_task_listed(self):
        tasks = [{"status": "완료", "summary": "DB 설계 완료"}]
        result = _build_project_table([_make_project(tasks=tasks)])
        assert "DB 설계 완료" in result
        assert "완료" in result

    def test_in_progress_task_listed(self):
        tasks = [{"status": "진행", "summary": "API 개발 중"}]
        result = _build_project_table([_make_project(tasks=tasks)])
        assert "API 개발 중" in result

    def test_no_tasks_shows_fallback(self):
        result = _build_project_table([_make_project(tasks=[])])
        assert "작업 내역 없음" in result

    def test_empty_project_list_returns_table_header(self):
        result = _build_project_table([])
        assert "| 구분 | 작업 내용 |" in result

    def test_multiple_projects_separated_by_divider(self):
        projects = [
            _make_project(name="프로젝트A"),
            _make_project(name="프로젝트B"),
        ]
        result = _build_project_table(projects)
        assert "프로젝트A" in result
        assert "프로젝트B" in result
        assert "---" in result           # 프로젝트 간 구분선

    def test_tasks_sorted_completed_first(self):
        tasks = [
            {"status": "진행", "summary": "진행 작업"},
            {"status": "완료", "summary": "완료 작업"},
        ]
        result = _build_project_table([_make_project(tasks=tasks)])
        assert result.index("완료 작업") < result.index("진행 작업")

    def test_status_icon_in_section_header(self):
        project = _make_project(status="🔵")
        result = _build_project_table([project])
        assert "🔵" in result

    def test_pipe_in_summary_escaped(self):
        tasks = [{"status": "진행", "summary": "A|B 처리"}]
        result = _build_project_table([_make_project(tasks=tasks)])
        # 파이프 문자가 이스케이프되어 테이블 구조를 깨지 않아야 함
        lines_with_ab = [l for l in result.splitlines() if "A" in l and "B" in l and "|" in l]
        assert lines_with_ab  # 행은 존재
        # 원시 '|' (이스케이프 안 된)가 셀 내용에 없어야 함
        for line in lines_with_ab:
            cells = line.split("|")
            for cell in cells[1:-1]:  # 테이블 셀 내부
                assert "A|B" not in cell  # 이스케이프 없이 파이프가 셀 내에 없어야 함


# ── write_report_tool (비동기) ─────────────────────────────────
class TestWriteReportTool:
    async def test_missing_aggregated_data_returns_error(self):
        from weekly_project_report.core.report_tool import write_report_tool

        result = await write_report_tool(tool_context=_MockToolContext())
        assert "error" in result

    async def test_valid_state_returns_markdown(self):
        from weekly_project_report.core.report_tool import write_report_tool

        aggregated = {
            "project_aggregates": [
                {
                    "project_name": "테스트프로젝트",
                    "status": "🔵",
                    "issues": [],
                    "next_week_plans": ["기능 배포"],
                    "groups": [{"tasks": [{"status": "완료", "summary": "설계 완료"}]}],
                }
            ]
        }
        ctx = _MockToolContext({"aggregated_data": aggregated})
        result = await write_report_tool(tool_context=ctx)
        assert "markdown_report" in result
        assert "테스트프로젝트" in result["markdown_report"]

    async def test_markdown_stored_in_state(self):
        from weekly_project_report.core.report_tool import write_report_tool

        aggregated = {
            "project_aggregates": [
                {
                    "project_name": "P1",
                    "status": "🟢",
                    "issues": [],
                    "next_week_plans": [],
                    "groups": [{"tasks": []}],
                }
            ]
        }
        ctx = _MockToolContext({"aggregated_data": aggregated})
        await write_report_tool(tool_context=ctx)
        assert "markdown_report" in ctx.state

    async def test_inline_aggregated_data_json(self):
        import json
        from weekly_project_report.core.report_tool import write_report_tool

        aggregated = {
            "project_aggregates": [
                {
                    "project_name": "인라인프로젝트",
                    "status": "🔴",
                    "issues": ["납기 지연"],
                    "next_week_plans": [],
                    "groups": [],
                }
            ]
        }
        ctx = _MockToolContext()
        result = await write_report_tool(
            aggregated_data_json=json.dumps(aggregated, ensure_ascii=False),
            tool_context=ctx,
        )
        assert "markdown_report" in result
        assert "인라인프로젝트" in result["markdown_report"]

    async def test_issue_appears_in_risk_section(self):
        from weekly_project_report.core.report_tool import write_report_tool

        analysis = {
            "risks": [
                {"risk": "일정 차질 위험", "mitigation": "주간 점검 강화"}
            ]
        }
        aggregated = {
            "project_aggregates": [
                {
                    "project_name": "P2",
                    "status": "🔴",
                    "issues": [],
                    "next_week_plans": [],
                    "groups": [],
                }
            ]
        }
        ctx = _MockToolContext({"analysis_result": analysis, "aggregated_data": aggregated})
        result = await write_report_tool(tool_context=ctx)
        assert "일정 차질 위험" in result["markdown_report"]
