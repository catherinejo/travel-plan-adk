# Travel Plan ADK — 주간 업무 보고서 자동화 파이프라인

Google ADK 2.0 기반 멀티 에이전트 파이프라인.  
센터별 엑셀 파일을 업로드하면 파싱 → 취합 → 분석 → 보고서 작성까지 자동으로 처리합니다.

[![CI](https://github.com/catherinejo/travel-plan-adk/actions/workflows/ci.yml/badge.svg)](https://github.com/catherinejo/travel-plan-adk/actions/workflows/ci.yml)

---

## 파이프라인 구조

```
사용자 입력
    │
    ▼
intent_router          ← REPORT / GENERAL 분류
    │
    ├─ GENERAL ──→ general_response_agent   (일반 질문 응답)
    │
    └─ REPORT ──→ WeeklyReportWorkflow
                      │
                      ▼
                  parser_agent           ← 엑셀 파싱 + TaskItem 검증
                      │  NEXT
                      ▼
                  aggregator_agent       ← 프로젝트별 취합 + ProjectAggregate 검증
                      │  NEXT
                      ▼
                  analyzer_agent         ← 인사이트 분석 + AnalysisResult 검증
                      │  NEXT
                      ▼
                  writer_agent           ← 마크다운 보고서 작성
                      │  NEXT
                      ▼
                  final_report_agent     ← 최종 응답 반환
                      │
                  (오류 발생 시 → error_agent)
```

각 단계는 오류 발생 시 **1회 자동 재시도** 후 `error_agent`로 분기됩니다.

---

## 빠른 시작

### 요구 사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 패키지 매니저
- Google Gemini API Key

### 설치

```bash
git clone https://github.com/catherinejo/travel-plan-adk.git
cd travel-plan-adk

cp .env.example .env
# .env에 GOOGLE_API_KEY 입력

uv sync
```

### 실행

```bash
uv run adk web
```

브라우저에서 `http://localhost:8000` 접속 후 엑셀 파일을 첨부하고 요청합니다.

```
예시: "이번 주 보고서 작성해줘"
```

---

## 엑셀 파일 형식

두 가지 형식을 자동 인식합니다.

### 형식 A — 헤더 기반 (권장)

| 프로젝트명 | 상태 | 업무내용 | 담당자 | 센터 | 시작일 | 종료일 |
|---|---|---|---|---|---|---|
| ERP 구축 | 진행 | API 연동 작업 | 홍길동 | 개발센터 | 04.21 | 04.25 |

- 지원 헤더 별칭: `프로젝트명`, `프로젝트`, `업무구분` / `상태`, `진행상태` / `업무내용`, `내용`, `요약` 등

### 형식 B — 계층 구조 (비정형)

```
개발센터 개인업무
홍길동
  - ERP 구축
    > API 연동     진행   04.21 ~ 04.25
    > 단위 테스트  완료
```

- 파일명에 `*센터*` 패턴이 있으면 센터명 자동 추출 (예: `개발센터_홍길동_0421.xlsx`)
- 빨간색 셀 → 지연 자동 감지

---

## 프로젝트 구조

```
travel-plan-adk/
├── src/travel_plan/
│   ├── core/
│   │   ├── _utils.py          # 공통 JSON 파싱 유틸리티
│   │   ├── agent.py           # 에이전트 & 워크플로 정의
│   │   ├── parse_tool.py      # 엑셀 파싱 도구
│   │   ├── aggregate_tool.py  # 프로젝트별 취합 도구
│   │   ├── analyze_tool.py    # 분석 도구
│   │   ├── report_tool.py     # 보고서 작성 + PDF 변환 도구
│   │   ├── prompts.py         # 에이전트 프롬프트
│   │   ├── model_config.py    # 모델 설정
│   │   └── guardrails.py      # 콜백 안전장치
│   └── schemas/
│       └── model.py           # Pydantic 데이터 스키마
├── tests/                     # 단위 테스트 (89개)
├── .github/workflows/ci.yml   # GitHub Actions CI
├── pyproject.toml
└── uv.lock
```

---

## 데이터 스키마

파이프라인 단계 간 데이터는 Pydantic 모델로 검증됩니다.

| 스키마 | 설명 | 사용 단계 |
|---|---|---|
| `TaskItem` | 개별 업무 레코드 | parser → aggregator |
| `ProjectAggregate` | 프로젝트별 취합 결과 | aggregator → analyzer |
| `AnalysisResult` | 분석 결과 + 인사이트 | analyzer → writer |

---

## 환경 변수

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `GOOGLE_API_KEY` | ✅ | — | Google Gemini API Key |
| `TRAVEL_PLAN_AGENT_MODEL` | — | `gemini-2.5-flash` | 사용할 모델 |

---

## 개발

```bash
# 개발 의존성 포함 설치
uv sync --extra dev

# 테스트
uv run pytest tests/ -v

# 린트
uv run ruff check src/ tests/

# 자동 수정
uv run ruff check --fix src/ tests/
```

### CI

`main` 브랜치 push 및 PR 시 GitHub Actions가 자동으로 실행됩니다.

- **lint**: `ruff check`
- **test**: `pytest tests/` (89개 단위 테스트)

---

## 라이선스

MIT
