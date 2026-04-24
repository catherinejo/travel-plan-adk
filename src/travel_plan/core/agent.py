from google.adk import Agent
from google.adk.agents.context import Context
from google.adk.workflow import Workflow
from google.genai import types

from ..guardrails import GUARDRAIL_CALLBACKS
from .model_config import AGENT_MODEL
from .prompts import PARSER_AGENT_PROMPT   
from .prompts import aggregator_agent_prompt
from .prompts import analyzer_agent_prompt  
from .prompts import writer_agent_prompt
from .tools import aggregate_tool
from .tools import analyze_tool
from .tools import parse_and_analyze_tool
from .tools import render_pdf_function
from .tools import write_report_tool

AGENT_GUARDRAIL_KWARGS = {
    "before_model_callback": GUARDRAIL_CALLBACKS["before_model_call"],
    "after_model_callback": GUARDRAIL_CALLBACKS["after_model_call"],
    "before_agent_callback": GUARDRAIL_CALLBACKS["before_agent_call"],
    "after_tool_callback": GUARDRAIL_CALLBACKS["after_tool_call"],
}

MAX_RETRY_COUNT = 1

# ══════════════════════════════════════════════════════════════
#  router 함수 (라우팅 함수)
# ══════════════════════════════════════════════════════════════
def intent_router(ctx: Context, node_input: object) -> None:
    """Emit REPORT or GENERAL route."""
    normalized = _normalize_text(node_input)
    report_hints = (
        "report",
        "리포트",
        "보고서",
        "주간",
        "생성",
        "작성",
        "pdf",
    )
    ctx.route = "REPORT" if any(hint in normalized for hint in report_hints) else "GENERAL"


def _normalize_text(value: object) -> str:
    """User input/model output을 라우팅용 텍스트로 정규화한다."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, types.Content):
        texts = [part.text for part in (value.parts or []) if part.text]
        return " ".join(texts).strip().lower()
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values()).strip().lower()
    return str(value).strip().lower()

def critic_router(ctx: Context, node_input: object) -> None:
    """Emit PASS or RETRY route."""
    normalized = _normalize_text(node_input)
    ctx.route = "PASS" if "pass" in normalized else "RETRY"


def route_on_error(ctx: Context, node_input: object) -> None:
    """Emit ERROR when upstream output contains an error field."""
    if isinstance(node_input, dict) and node_input.get("error"):
        ctx.route = "ERROR"
    else:
        ctx.route = "NEXT"


def _extract_error_message(node_input: object) -> str | None:
    """dict/text/content 형태 입력에서 오류 메시지를 추출한다."""
    if isinstance(node_input, dict):
        error_value = node_input.get("error")
        if isinstance(error_value, str) and error_value.strip():
            return error_value.strip()
    normalized = _normalize_text(node_input)
    error_hints = (
        "error",
        "오류",
        "실패",
        "누락",
        "찾을 수 없습니다",
        "지원하지 않는 파일 형식",
    )
    if any(hint in normalized for hint in error_hints):
        return normalized
    return None


def _is_fatal_error(error_message: str) -> bool:
    """재시도해도 의미 없는 입력/스키마 오류를 판별한다."""
    fatal_hints = (
        "필수 컬럼",
        "누락",
        "헤더",
        "지원하지 않는 파일 형식",
        "찾을 수 없습니다",
        "분석할 파일이 없습니다",
    )
    lowered = error_message.lower()
    return any(hint.lower() in lowered for hint in fatal_hints)


def _allow_retry(ctx: Context, state_key: str) -> bool:
    """단계별 재시도를 MAX_RETRY_COUNT 이내에서 허용한다."""
    current = int(ctx.state.get(state_key, 0) or 0)
    if current < MAX_RETRY_COUNT:
        ctx.state[state_key] = current + 1
        return True
    return False


def _route_with_single_retry(ctx: Context, node_input: object, stage_key: str) -> None:
    """오류 발생 시 단계별 MAX_RETRY_COUNT 회만 RETRY 후 ERROR로 전환한다."""
    retry_state_key = f"{stage_key}_retry_count"
    error_message = _extract_error_message(node_input)

    if not error_message:
        ctx.state[retry_state_key] = 0
        ctx.state["last_error_message"] = ""
        ctx.route = "NEXT"
        return

    ctx.state["last_error_message"] = error_message
    if _is_fatal_error(error_message):
        ctx.route = "ERROR"
        return

    ctx.route = "RETRY" if _allow_retry(ctx, retry_state_key) else "ERROR"


def parser_guard(ctx: Context, node_input: object) -> None:
    """Parser 단계 오류 분기 + 1회 재시도."""
    _route_with_single_retry(ctx, node_input, "parser")


def aggregator_guard(ctx: Context, node_input: object) -> None:
    """Aggregator 단계 오류 분기 + 1회 재시도."""
    _route_with_single_retry(ctx, node_input, "aggregator")


def analyzer_guard(ctx: Context, node_input: object) -> None:
    """Analyzer 단계 오류 분기 + 1회 재시도."""
    _route_with_single_retry(ctx, node_input, "analyzer")


def writer_guard(ctx: Context, node_input: object) -> None:
    """Writer 단계 오류 분기. 자체 재시도 없이 즉시 ERROR 처리."""
    route_on_error(ctx, node_input)


def critic_guard(ctx: Context, node_input: object) -> None:
    """Critic 단계 PASS/RETRY 분기 + MAX_RETRY_COUNT 재시도 제한."""
    normalized = _normalize_text(node_input)
    if "retry" not in normalized:
        ctx.state["critic_retry_count"] = 0
        ctx.route = "PASS"
        return

    ctx.route = "RETRY" if _allow_retry(ctx, "critic_retry_count") else "ERROR"

# ══════════════════════════════════════════════════════════════
#  intent router 에이전트 (의도파악 에이전트)
# ══════════════════════════════════════════════════════════════
intent_router_agent = Agent(
    name="intent_router_agent",
    model=AGENT_MODEL,
    description=(
        "사용자 메시지가 주간 리포트 생성(REPORT)인지 일반 대화(GENERAL)인지 분류한다."
        "intent_route에 라우팅 결과를 내보낸다. 워크플로에서 분기 결정의 첫 단계로 사용한다."
    ),
    instruction=(
        "사용자 쿼리를 보고 리포트 생성 요청인지 분류하라.\n"
        "- 리포트/보고서 생성, 작성, 출력, PDF 생성 요청이면 REPORT\n"
        "- 그 외 일반 질의/대화면 GENERAL\n"
        "출력은 반드시 한 단어로만 하라: REPORT 또는 GENERAL"
    ),
    output_key="intent_route",
    **AGENT_GUARDRAIL_KWARGS,
)

# ══════════════════════════════════════════════════════════════
#  general_response_agent 에이전트 (일반 질문 응답 에이전트)
# ══════════════════════════════════════════════════════════════
general_response_agent = Agent(
    name="general_response_agent",
    model=AGENT_MODEL,
     description="""
        리포트 생성과 무관한 일반 질문·잡담을 처리하는 종단 에이전트.
        intent_router가 GENERAL로 분류한 요청에 대해 한국어로 간결하게 응답한다.
    """,
    instruction="""
        아래 순서대로 응답을 생성하라.
        【Step 1 — 질문 의도 파악】
        사용자가 무엇을 알고 싶어하는지, 어떤 도움이 필요한지 파악한다.
        【Step 2 — 답변 구성】
        핵심 정보만 추려 간결하게 구성한다. 불필요한 서론·부연은 생략한다.
        【Step 3 — 출력】
        한국어로 명확하고 친절하게 응답한다
    """,
    **AGENT_GUARDRAIL_KWARGS,
)


# ══════════════════════════════════════════════════════════════
#  parser_agent 에이전트 (엑셀 파싱 에이전트)
# ══════════════════════════════════════════════════════════════
parser_agent = Agent(
    name="parser_agent",
    model=AGENT_MODEL,
    description=(
        "파이프라인 1단계. 업로드된 엑셀 파일을 파싱하고 데이터를 정규화한다. "
        "파일 형식을 검증하고 날짜·프로젝트명 등 핵심 필드를 추출해 "
        "이후 분석 단계에서 사용할 구조화된 데이터를 생성한다."
    ),
    instruction=PARSER_AGENT_PROMPT,
    tools=[parse_and_analyze_tool],
    **AGENT_GUARDRAIL_KWARGS,
)

# ══════════════════════════════════════════════════════════════
#  aggregator_agent 에이전트 (데이터 취합 에이전트)
# ══════════════════════════════════════════════════════════════
aggregator_agent = Agent(
    name="aggregator_agent",
    model=AGENT_MODEL,
    description="""
        너는 임원 보고용 프로젝트 통합 리포트 생성기 에이전트입니다. 입력으로 여러 센터의 개인 주간 보고 텍스트가 주어진다.
        이 텍스트를 기반으로 프로젝트 단위로 통합하고, 임원 의사결정에 필요한 정보 중심으로 재구성하라.
        파이프라인 2단계. parser_agent가 생성한 정규화 데이터를 프로젝트별로 취합한다. 이후 analyzer_agent가 분석을 수행한다.
    """,
    instruction=aggregator_agent_prompt,
    tools=[aggregate_tool],
    **AGENT_GUARDRAIL_KWARGS,
)

# ══════════════════════════════════════════════════════════════
#  analyzer_agent 에이전트 (데이터 분석 에이전트)
# ══════════════════════════════════════════════════════════════
analyzer_agent = Agent(
    name="analyzer_agent",
    model=AGENT_MODEL,
   description="""
        "파이프라인 3단계. aggregator_agent의 취합 결과를 바탕으로 핵심 인사이트를 도출한다. "
        "작업 집중도, 이슈 프로젝트, 리소스 편중 등 리포트에 담을 주요 발견사항을 분석한다."
    """,
    instruction=analyzer_agent_prompt,
    tools=[analyze_tool],
    **AGENT_GUARDRAIL_KWARGS,
)
# ══════════════════════════════════════════════════════════════
#  writer_agent 에이전트 (포트 작성 에이전트)
# ══════════════════════════════════════════════════════════════
writer_agent = Agent(
    name="writer_agent",
    model=AGENT_MODEL,
    description=(
        "파이프라인 4단계. analyzer_agent의 분석 결과를 바탕으로 주간 업무 보고서 초안을 작성한다. "
        "경영진이 바로 읽을 수 있는 수준의 마크다운 리포트를 생성하며, critic_agent의 검토 후 재작성될 수 있다."
    ),
    instruction=writer_agent_prompt,
    tools=[write_report_tool],
    **AGENT_GUARDRAIL_KWARGS,
)

# ══════════════════════════════════════════════════════════════
#  critic_agent 에이전트 ( 리포트 검토 에이전트)
# ══════════════════════════════════════════════════════════════
critic_agent = Agent(
    name="critic_agent",
    model=AGENT_MODEL,
    description="""
        파이프라인 5단계. writer_agent가 생성한 리포트 초안의 품질을 검토한다.
        사실 일치성·완성도·논조를 기준으로 평가하고, 기준 통과 시 PASS, 재작성 필요 시 RETRY를 반환한다.
        RETRY 판정 시 writer_agent가 재실행된다.
    """,
    instruction="""
       아래 순서대로 리포트를 검토하고 판정 결과만 출력하라.

        【Step 1 — 사실 일치성 검토】
        리포트 서술이 분석 결과와 논리적으로 어긋나거나, 근거 없는 단정이 있으면 RETRY.

        【Step 2 — 완성도 검토】
        필수 섹션(요약, 프로젝트별 실적 및 작업 내역, 이슈, 다음 주 계획)이 모두 포함되어 있는지 확인한다.
        섹션 누락 또는 내용이 현저히 부족하면 RETRY.

        【Step 3 — 논조·가독성 검토】
        경영진 보고서에 적합한 간결하고 객관적인 논조인지 확인한다.
        과도한 구어체, 불명확한 표현, 오탈자가 있으면 RETRY.
  
        【Step 4 — 판정 출력】
        판정 이유를 한 줄로 함께 출력하라.
    """,
    tools=[critic_router],
    **AGENT_GUARDRAIL_KWARGS,
)

# ══════════════════════════════════════════════════════════════
#  final_report_agent 에이전트 ( 리포트 PDF 생성 및 최종응답 에이전트)
# ══════════════════════════════════════════════════════════════
final_report_agent = Agent(
    name="final_report_agent",
    model=AGENT_MODEL,
    description="마크다운 형식의 보고서를 PDF 파일로 변환하는 에이전트.",
    instruction=(
        "사용자에게 마크다운 내용을 다시 요청하지 말고 즉시 render_pdf_function을 호출하라.\n"
        "markdown 인자가 비어 있으면 tool_context.state의 markdown_report/final_report를 사용한다.\n"
        "성공 시 pdf_path를 포함한 결과만 간결하게 반환하라."
    ),
    tools=[render_pdf_function],
    **AGENT_GUARDRAIL_KWARGS,
)

error_agent = Agent(
    name="error_agent",
    model=AGENT_MODEL,
    description="파이프라인 오류를 사용자에게 전달하고 종료한다.",
    instruction=(
        "입력에서 error 정보를 읽어 한국어로 간결하게 실패 원인을 설명하라.\n"
        "형식: '<단계> 단계에서 실패: <오류 메시지>'\n"
        "단계를 알 수 없으면 '처리 단계'로 표시하라."
    ),
    **AGENT_GUARDRAIL_KWARGS,
)

# ══════════════════════════════════════════════════════════════
#  Workflow 정의 (구조 유지용)
# ══════════════════════════════════════════════════════════════
weekly_report_workflow = Workflow(
    name="WeeklyReportWorkflow",
    edges=[
        (
            "START",
            parser_agent,
            parser_guard,
            {"NEXT": aggregator_agent, "RETRY": parser_agent, "ERROR": error_agent},
        ),
        (
            aggregator_agent,
            aggregator_guard,
            {"NEXT": analyzer_agent, "RETRY": aggregator_agent, "ERROR": error_agent},
        ),
        (
            analyzer_agent,
            analyzer_guard,
            {"NEXT": writer_agent, "RETRY": analyzer_agent, "ERROR": error_agent},
        ),
        (
            writer_agent,
            writer_guard,
            {"NEXT": critic_agent, "RETRY": writer_agent, "ERROR": error_agent},
        ),
        (
            critic_agent,
            critic_guard,
            {"PASS": final_report_agent, "RETRY": writer_agent, "ERROR": error_agent},
        ),
    ],
)

root_agent = Workflow(
    name="ReportReviewWorkflow",
    edges=[
        (
            "START",
            intent_router,
            {
                "REPORT": weekly_report_workflow,
                "GENERAL": general_response_agent,
            },
        ),
    ],
)
