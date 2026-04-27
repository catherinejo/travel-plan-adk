"""Tests for core/_utils.py — JSON 파싱 유틸리티."""

from weekly_project_report.core._utils import load_json_records, parse_json_dict


class TestParseJsonDict:
    def test_valid_dict(self):
        data, err = parse_json_dict('{"key": 1}', "arg")
        assert data == {"key": 1}
        assert err is None

    def test_list_input_returns_error(self):
        data, err = parse_json_dict("[1, 2, 3]", "arg")
        assert data == {}
        assert err == {"error": "arg은 JSON 객체여야 합니다."}

    def test_invalid_json_returns_error(self):
        _, err = parse_json_dict("{bad json}", "arg")
        assert err is not None
        assert "파싱 실패" in err["error"]

    def test_nested_dict(self):
        data, err = parse_json_dict('{"a": {"b": 2}}', "arg")
        assert data == {"a": {"b": 2}}
        assert err is None


class TestLoadJsonRecords:
    def test_plain_list(self):
        assert load_json_records('[{"a": 1}]') == [{"a": 1}]

    def test_records_wrapper(self):
        assert load_json_records('{"records": [{"a": 2}]}') == [{"a": 2}]

    def test_trailing_text_after_json(self):
        result = load_json_records('[{"a": 3}] 이 내용은 설명입니다.')
        assert result == [{"a": 3}]

    def test_bracket_extraction_fallback(self):
        result = load_json_records('records=[{"a": 4}]')
        assert result == [{"a": 4}]

    def test_empty_string(self):
        assert load_json_records("") == []

    def test_whitespace_only(self):
        assert load_json_records("   ") == []

    def test_custom_records_key(self):
        result = load_json_records('{"items": [{"x": 1}]}', records_key="items")
        assert result == [{"x": 1}]

    def test_invalid_json_returns_empty(self):
        assert load_json_records("{not json at all}") == []
