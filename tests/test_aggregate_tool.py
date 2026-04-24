"""Tests for core/aggregate_tool.py — 취합 순수 함수."""

from travel_plan.core.aggregate_tool import (
    _build_project_alias_map,
    _name_similarity,
    _project_tokens,
    _status_emoji,
)


class TestProjectTokens:
    def test_basic(self):
        assert "경영정보분석" in _project_tokens("경영정보분석AI")

    def test_stopwords_excluded(self):
        tokens = _project_tokens("인사시스템 구축 프로젝트")
        assert "구축" not in tokens
        assert "프로젝트" not in tokens

    def test_short_tokens_excluded(self):
        tokens = _project_tokens("A B CCC")
        assert "a" not in tokens
        assert "b" not in tokens

    def test_ai_suffix_base_added(self):
        tokens = _project_tokens("경영정보분석ai")
        # 'ai' 접미사 제거된 베이스 토큰도 포함
        assert "경영정보분석" in tokens


class TestNameSimilarity:
    def test_identical(self):
        assert _name_similarity("경영정보분석", "경영정보분석") == 1.0

    def test_completely_different(self):
        assert _name_similarity("인사시스템", "회계관리") == 0.0

    def test_partial_overlap(self):
        score = _name_similarity("경영정보분석AI", "경영정보분석")
        assert 0.0 < score <= 1.0

    def test_empty_string(self):
        assert _name_similarity("", "테스트") == 0.0


class TestBuildProjectAliasMap:
    def test_similar_names_merged(self):
        # 첫 핵심 토큰(anchor)이 같고 유사도 >= 0.28이면 병합된다
        names = ["경영정보 분석 시스템", "경영정보 분석"]
        alias = _build_project_alias_map(names)
        assert alias["경영정보 분석 시스템"] == alias["경영정보 분석"]

    def test_different_names_kept_separate(self):
        names = ["인사시스템구축", "회계관리시스템"]
        alias = _build_project_alias_map(names)
        assert alias["인사시스템구축"] != alias["회계관리시스템"]

    def test_single_name(self):
        alias = _build_project_alias_map(["테스트프로젝트"])
        assert alias["테스트프로젝트"] == "테스트프로젝트"


class TestStatusEmoji:
    def test_delayed_is_red(self):
        items = [{"status": "지연"}]
        assert _status_emoji(items) == "🔴"

    def test_in_progress_is_blue(self):
        items = [{"status": "진행"}]
        assert _status_emoji(items) == "🔵"

    def test_completed_is_green(self):
        items = [{"status": "완료"}]
        assert _status_emoji(items) == "🟢"

    def test_delay_takes_priority(self):
        items = [{"status": "완료"}, {"status": "지연"}]
        assert _status_emoji(items) == "🔴"

    def test_empty(self):
        assert _status_emoji([]) == "🔵"
