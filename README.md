# weekly_review_report_adk — 주간 업무 보고서 자동화 파이프라인

Google ADK 2.0 기반 멀티 에이전트 파이프라인.  
센터별 엑셀 파일을 업로드하면 **병렬 파싱 → 취합 → 병렬 분석 → 보고서 작성**까지 자동으로 처리합니다.

[![CI](https://github.com/catherinejo/weekly-review-report-adk/actions/workflows/ci.yml/badge.svg)](https://github.com/catherinejo/weekly-review-report-adk/actions/workflows/ci.yml)

---

## 파이프라인 구조

```
사용자 입력
    │
    ▼
intent_router                  ← REPORT / GENERAL 분류
    │
    ├─ GENERAL ──→ general_response_agent   (일반 질문 응답)
    │
    └─ REPORT ──→ WeeklyReportWorkflow
                      │
                      ▼
              ┌─ MultiFileParseWorkflow ─────────────────────────┐
              │  list_excel_artifacts_node                        │  ← 업로드 파일 목록 수집
              │      │                                            │
              │      ▼  (Fan-out: 파일마다 병렬)                  │
              │  parse_single_artifact_node × N                   │  ← 각 파일 동시 파싱
              │      │                                            │
              │      ▼  (Fan-in)                                  │
              │  merge_parsed_results_node                        │  ← 레코드 병합
              └──────────────────────────────────────────────────┘
                      │  NEXT
                      ▼
              aggregator_agent                                    ← 프로젝트별 취합
                      │  NEXT
                      ▼
              ┌─ ParallelAnalysisWorkflow ───────────────────────┐
              │  ┌─────────────────┐  ┌────────────────────────┐ │
              │  │ risk_analyzer   │  │ achievement_analyzer   │ │  ← 동시 실행
              │  │ _agent          │  │ _agent                 │ │
              │  │ (리스크·이슈)   │  │ (성과·진척·계획)       │ │
              │  └────────┬────────┘  └──────────┬─────────────┘ │
              │           └──────────┬────────────┘               │
              │                      ▼  (JoinNode)                │
              │           merge_analysis_results_node             │  ← AnalysisResult 병합
              └──────────────────────────────────────────────────┘
                      │  NEXT
                      ▼
              writer_agent                                        ← 마크다운 보고서 작성
                      │  NEXT
                      ▼
              final_report_agent                                  ← 최종 응답 반환
                      │
              (오류 발생 시 → error_agent, 각 단계 1회 자동 재시도)
```

### ADK Workflow 패턴

| 패턴 | 구현 위치 | 설명 |
|---|---|---|
| **Fan-out / Fan-in** | `MultiFileParseWorkflow` | `_ParallelWorker`로 N개 파일을 동시 파싱 후 병합 |
| **Parallel Flow** | `ParallelAnalysisWorkflow` | Workflow 팬아웃으로 두 분석 에이전트를 동시 실행, `JoinNode`로 합산 |

---

## 빠른 시작

### 요구 사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 패키지 매니저
- Google Gemini API Key

### 설치

```bash
git clone https://github.com/catherinejo/weekly-review-report-adk.git
cd weekly-review-report-adk

cp .env.example .env
# .env에 GOOGLE_API_KEY 입력

uv sync
```

### 실행

```bash
uv run adk web
```

브라우저에서 `http://localhost:8000` 접속 후 엑셀 파일(단일 또는 여러 개)을 첨부하고 요청합니다.

```
예시: "이번 주 보고서 작성해줘"
```

---

## 엑셀 파일 형식

두 가지 형식을 자동 인식합니다. **파일을 여러 개 동시에 업로드**하면 센터별로 병렬 파싱 후 통합 보고서를 생성합니다.

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
weekly-review-report-adk/
├── src/weekly_project_report/
│   ├── core/
│   │   ├── agent.py               # 에이전트 & 워크플로 정의 (최상위 배선)
│   │   ├── fanout.py              # Fan-out/Fan-in: 멀티파일 병렬 파싱 워크플로
│   │   ├── parallel_analysis.py   # Parallel Flow: 병렬 분석 워크플로
│   │   ├── parse_tool.py          # 엑셀 파싱 도구 (_parse_excel_file 포함)
│   │   ├── aggregate_tool.py      # 프로젝트별 취합 도구
│   │   ├── analyze_tool.py        # 분석 공통 함수
│   │   ├── report_tool.py         # 보고서 작성 + PDF 변환 도구
│   │   ├── prompts.py             # 에이전트 프롬프트
│   │   ├── model_config.py        # 모델 설정
│   │   └── _utils.py              # 공통 JSON 파싱 유틸리티
│   ├── guardrails.py              # 콜백 안전장치 (콘텐츠 필터, 속도 제한, 감사 로그)
│   └── schemas/
│       └── model.py               # Pydantic 데이터 스키마
├── tests/                         # 단위 테스트 (89개)
├── .github/workflows/ci.yml       # GitHub Actions CI
├── pyproject.toml
└── uv.lock
```

---

## 데이터 스키마

파이프라인 단계 간 데이터는 Pydantic 모델로 검증됩니다.

| 스키마 | 설명 | 사용 단계 |
|---|---|---|
| `TaskItem` | 개별 업무 레코드 | MultiFileParseWorkflow → aggregator |
| `ProjectAggregate` | 프로젝트별 취합 결과 | aggregator → ParallelAnalysisWorkflow |
| `AnalysisResult` | 분석 결과 + 인사이트 | ParallelAnalysisWorkflow → writer |

---

## 환경 변수

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `GOOGLE_API_KEY` | ✅ | — | Google Gemini API Key |
| `WEEKLY_PROJECT_REPORT_AGENT_MODEL` | — | `gemini-2.5-flash` | 사용할 모델 |

---

## 개발

```bash
# 개발 의존성 포함 설치
uv sync --extra dev

# 테스트
uv run --extra dev pytest tests/ -v

# 린트
uv run --extra dev ruff check src/ tests/

# 자동 수정
uv run --extra dev ruff check --fix src/ tests/
```

### CI

`main` 브랜치 push 및 PR 시 GitHub Actions가 자동으로 실행됩니다.

- **lint**: `ruff check`
- **test**: `pytest tests/` (89개 단위 테스트)

---

## 라이선스

MIT
