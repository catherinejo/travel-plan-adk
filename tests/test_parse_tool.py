"""Tests for core/parse_tool.py — Excel 파싱 순수 함수."""

import pytest
from weekly_project_report.core.parse_tool import (
    _build_column_map,
    _extract_member_names,
    _is_member_name,
    _is_valid_status_text,
    _normalize_status_text,
    _parse_short_date,
    _strip_bullet_prefix,
)


class TestIsMemberName:
    def test_valid_two_char(self):
        assert _is_member_name("홍길") is True

    def test_valid_three_char(self):
        assert _is_member_name("홍길동") is True

    def test_four_char_rejected(self):
        assert _is_member_name("홍길동동") is False

    def test_english_rejected(self):
        assert _is_member_name("Kim") is False

    def test_mixed_rejected(self):
        assert _is_member_name("홍A") is False


class TestNormalizeStatusText:
    @pytest.mark.parametrize("raw,expected", [
        ("진행중", "진행"),
        ("진행 중", "진행"),
        ("[진행]", "진행"),
        ("(완료)", "완료"),
        ("예정", "예정"),
        ("지연", "지연"),
        ("완료", "완료"),
    ])
    def test_normalize(self, raw, expected):
        assert _normalize_status_text(raw) == expected


class TestIsValidStatusText:
    @pytest.mark.parametrize("text", ["진행", "진행중", "진행 중", "완료", "예정", "지연"])
    def test_valid(self, text):
        assert _is_valid_status_text(text) is True

    @pytest.mark.parametrize("text", ["", "모름", "오류", "취소"])
    def test_invalid(self, text):
        assert _is_valid_status_text(text) is False


class TestParseShortDate:
    @pytest.mark.parametrize("raw,expected", [
        ("2025-04-24", "2025-04-24"),
        ("2025.04.24", "2025-04-24"),
        ("25.04.24", "2025-04-24"),
        ("25/04/24", "2025-04-24"),
        ("", ""),
        ("-", ""),
        ("상시대응", ""),
        ("미정", ""),
    ])
    def test_parse(self, raw, expected):
        assert _parse_short_date(raw) == expected


class TestExtractMemberNames:
    def test_parenthesized_names(self):
        names = _extract_member_names("작업(홍길동, 김철수)")
        assert "홍길동" in names
        assert "김철수" in names

    def test_no_korean_names(self):
        # 영문/숫자만 있는 경우 이름 없음
        assert _extract_member_names("API endpoint v2") == []

    def test_stopword_excluded(self):
        names = _extract_member_names("전체 업무 관리")
        assert "전체" not in names
        assert "업무" not in names

    def test_deduplication(self):
        names = _extract_member_names("홍길동", "홍길동")
        assert names.count("홍길동") == 1


class TestStripBulletPrefix:
    @pytest.mark.parametrize("text,expected", [
        ("- 작업내용", "작업내용"),
        ("> 프로젝트명", "프로젝트명"),
        ("* 항목", "항목"),
        (": 설명", "설명"),
        ("일반 텍스트", "일반 텍스트"),
        ("", ""),
    ])
    def test_strip(self, text, expected):
        assert _strip_bullet_prefix(text) == expected


class TestBuildColumnMap:
    def test_korean_headers(self):
        headers = ["프로젝트명", "상태", "업무내용", "담당자", "센터"]
        col_map = _build_column_map(headers)
        assert col_map["project_name"] == 0
        assert col_map["status"] == 1
        assert col_map["summary"] == 2
        assert col_map["member_name"] == 3
        assert col_map["center_name"] == 4

    def test_partial_headers(self):
        headers = ["프로젝트", "진행상태"]
        col_map = _build_column_map(headers)
        assert "project_name" in col_map
        assert "status" in col_map
        assert "member_name" not in col_map
