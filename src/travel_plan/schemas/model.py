from __future__ import annotations

from pydantic import BaseModel
from datetime import date
from typing import Optional


# 리포트 (수치 필드 없음 — 상태·서술 중심)
class ProjectReport(BaseModel):
    project_name: str
    status: str
    progress_summary: str = ""  # 진척·진행 요지 (정성 서술)
    section_summary: str  # 1. 프로젝트 요약
    section_achievements: str  # 2. 주요 성과
    section_schedule: str  # 3. 일정·진행 상황
    section_issues: str  # 4. 이슈 및 리스크 (없으면 빈 문자열)
    section_changes: str  # 5. 변경사항 (없으면 빈 문자열)
    section_decisions: str  # 6. 의사결정 필요사항 (없으면 빈 문자열)
    section_next_plan: str  # 7. 다음 계획


class TaskItem(BaseModel):
    project_name: str
    center_name: str  # 센터
    member_name: str  # 개인(작성자/담당자)
    status: str  # 완료 / 진행 / 예정
    summary: str
    prev_start: Optional[date]
    prev_end: Optional[date]
    next_start: Optional[date]
    next_end: Optional[date]
    is_delayed: bool  # 종료일 초과 & 진행 중
    has_issue_keyword: bool  # 이슈 키워드 감지


class GroupSummary(BaseModel):
    group_name: str
    tasks: list[TaskItem]


class CenterSummary(BaseModel):
    center_name: str


class MemberSummary(BaseModel):
    member_name: str
    center_name: str


# 리포트 취합 (대외 리포트용 — 건수·비율 필드 없음)
class ProjectAggregate(BaseModel):
    project_name: str
    groups: list[GroupSummary]
    centers: list[CenterSummary]
    members: list[MemberSummary]
    status: str  # 🟢 / 🟡 / 🔴
    issues: list[str]
    next_week_plans: list[str]
