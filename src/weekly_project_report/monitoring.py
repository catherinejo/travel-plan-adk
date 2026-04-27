"""

Architecture:
    ┌─────────────────────────────────────────────────┐
    │  ADK Agent                                      │
    │  ┌──────────────────────────────────────────┐   │
    │  │ OpenTelemetry SDK                        │   │
    │  │  ┌──────┐  ┌───────┐  ┌──────────────┐  │   │
    │  │  │Traces│  │Metrics│  │    Logs      │  │   │
    │  │  └──┬───┘  └───┬───┘  └──────┬───────┘  │   │
    │  └─────┼──────────┼─────────────┼───────────┘   │
    │        │          │             │               │
    │   OTLP Exporter ──┴─────────────┘               │
    │        │                                        │
    └────────┼────────────────────────────────────────┘
             │
    ┌────────▼────────────────────────────────────────┐
    │  Collector (Jaeger / Prometheus / Cloud Trace)   │
    └─────────────────────────────────────────────────┘

OTel 구성:
  - TracerProvider: 분산 추적 (Jaeger/Cloud Trace)
  - MeterProvider:  메트릭 (Prometheus/Cloud Monitoring)
  - LoggerProvider: 로그 (Cloud Logging)
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from threading import Lock

logger = logging.getLogger("adk2_labs.monitoring")
_DB_LOCK = Lock()
_MONITOR_DB_PATH = Path(os.getenv("WEEKLY_PROJECT_REPORT_MONITOR_DB", "src/.adk/monitoring.db"))


def _init_monitor_db() -> None:
    _MONITOR_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        conn = sqlite3.connect(_MONITOR_DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS monitor_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    session_id TEXT,
                    agent_name TEXT,
                    tool_name TEXT,
                    success INTEGER,
                    error_type TEXT,
                    payload_json TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def record_monitor_event(
    event_type: str,
    *,
    session_id: str | None = None,
    agent_name: str | None = None,
    tool_name: str | None = None,
    success: bool | None = None,
    error_type: str | None = None,
    payload: dict | None = None,
) -> None:
    """Persist a monitoring event into SQLite."""
    _init_monitor_db()
    with _DB_LOCK:
        conn = sqlite3.connect(_MONITOR_DB_PATH)
        try:
            conn.execute(
                """
                INSERT INTO monitor_events
                (ts, event_type, session_id, agent_name, tool_name, success, error_type, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    event_type,
                    session_id,
                    agent_name,
                    tool_name,
                    None if success is None else int(success),
                    error_type,
                    json.dumps(payload, ensure_ascii=False) if payload else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def get_monitoring_summary(window_minutes: int = 60) -> dict:
    """Return practical monitoring summary for recent activity."""
    _init_monitor_db()
    window_start = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    rows: list[tuple] = []

    with _DB_LOCK:
        conn = sqlite3.connect(_MONITOR_DB_PATH)
        try:
            cursor = conn.execute(
                """
                SELECT event_type, success, error_type, tool_name, agent_name, ts
                FROM monitor_events
                WHERE ts >= ?
                ORDER BY id DESC
                """,
                (window_start.isoformat(),),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

    total = len(rows)
    blocked = sum(1 for r in rows if r[0] == "guardrail_block")
    tool_calls = [r for r in rows if r[0] == "tool_call"]
    tool_errors = sum(1 for r in tool_calls if r[1] == 0)
    agent_calls = sum(1 for r in rows if r[0] == "agent_call")
    rate_limited = sum(1 for r in rows if r[0] == "rate_limited")

    return {
        "window_minutes": window_minutes,
        "total_events": total,
        "agent_calls": agent_calls,
        "tool_calls": len(tool_calls),
        "tool_errors": tool_errors,
        "guardrail_blocks": blocked,
        "rate_limited": rate_limited,
        "db_path": str(_MONITOR_DB_PATH),
        "latest_event_ts": rows[0][5] if rows else None,
    }


def setup_telemetry(
    service_name: str = "adk2-labs",
    otlp_endpoint: str = "http://localhost:4317",
    enable_traces: bool = True,
    enable_metrics: bool = True,
) -> dict:
    """Initialize OpenTelemetry instrumentation.

    Args:
        service_name: OTLP 서비스 이름.
        otlp_endpoint: OTLP Collector 엔드포인트.
        enable_traces: 분산 추적 활성화.
        enable_metrics: 메트릭 수집 활성화.

    Returns:
        초기화된 provider 딕셔너리.

    Note:
        opentelemetry 패키지가 설치되어 있어야 합니다:
        uv pip install opentelemetry-api opentelemetry-sdk \\
            opentelemetry-exporter-otlp
    """
    providers = {}

    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})

        # ── Traces ──
        if enable_traces:
            tracer_provider = TracerProvider(resource=resource)
            span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            try:
                trace.set_tracer_provider(tracer_provider)
            except Exception:
                # 개발 중 reload 환경에서는 provider 중복 설정이 발생할 수 있다.
                logger.debug("TracerProvider already set; reusing existing provider.")
            providers["tracer"] = trace.get_tracer(service_name)
            logger.info("OpenTelemetry tracing initialized → %s", otlp_endpoint)

        # ── Metrics ──
        if enable_metrics:
            metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
            metric_reader = PeriodicExportingMetricReader(
                metric_exporter, export_interval_millis=10000
            )
            meter_provider = MeterProvider(
                resource=resource, metric_readers=[metric_reader]
            )
            try:
                metrics.set_meter_provider(meter_provider)
            except Exception:
                logger.debug("MeterProvider already set; reusing existing provider.")
            providers["meter"] = metrics.get_meter(service_name)
            logger.info("OpenTelemetry metrics initialized → %s", otlp_endpoint)

        providers["enabled"] = True

    except ImportError:
        logger.warning(
            "OpenTelemetry not installed. Run: "
            "uv pip install 'adk2-labs[monitoring]' for OTel support."
        )
        providers["enabled"] = False

    return providers


# ══════════════════════════════════════════════════════════════
#  Custom Metrics for ADK Agents
# ══════════════════════════════════════════════════════════════
class AgentMetrics:
    """ADK Agent 전용 메트릭 수집기.

    수집 항목:
      - agent_request_count: 에이전트 호출 횟수
      - agent_request_duration: 응답 시간 히스토그램
      - agent_token_usage: 토큰 사용량
      - agent_error_count: 에러 횟수
    """

    def __init__(self, meter=None):
        self._meter = meter
        self._enabled = meter is not None

        if self._enabled:
            self.request_counter = meter.create_counter(
                "agent_request_count",
                description="Total agent requests",
                unit="1",
            )
            self.duration_histogram = meter.create_histogram(
                "agent_request_duration",
                description="Agent request duration",
                unit="ms",
            )
            self.token_counter = meter.create_counter(
                "agent_token_usage",
                description="Token consumption",
                unit="tokens",
            )
            self.error_counter = meter.create_counter(
                "agent_error_count",
                description="Agent errors",
                unit="1",
            )

    def record_request(self, agent_name: str, duration_ms: float, tokens: int = 0):
        """Record a completed agent request."""
        if not self._enabled:
            return
        attrs = {"agent.name": agent_name}
        self.request_counter.add(1, attrs)
        self.duration_histogram.record(duration_ms, attrs)
        if tokens > 0:
            self.token_counter.add(tokens, attrs)

    def record_error(self, agent_name: str, error_type: str):
        """Record an agent error."""
        if not self._enabled:
            return
        self.error_counter.add(
            1, {"agent.name": agent_name, "error.type": error_type}
        )


# ══════════════════════════════════════════════════════════════
#  Tracing Context Manager
# ══════════════════════════════════════════════════════════════
@contextmanager
def trace_agent_call(tracer, agent_name: str, user_input: str = ""):
    """Wrap an agent call with an OpenTelemetry span.

    Usage:
        with trace_agent_call(tracer, "my_agent", "hello"):
            result = await runner.run_async(...)
    """
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(
        f"agent.{agent_name}",
        attributes={
            "agent.name": agent_name,
            "agent.input_preview": user_input[:100],
        },
    ) as span:
        try:
            yield span
        except Exception as exc:
            from opentelemetry.trace import Status
            from opentelemetry.trace import StatusCode

            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
