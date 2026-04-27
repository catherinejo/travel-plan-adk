# ADK web/run 진입점.
import os

from google.adk.apps import App
from google.adk.plugins.save_files_as_artifacts_plugin import SaveFilesAsArtifactsPlugin

from .core.agent import root_agent
from .monitoring import get_monitoring_summary
from .monitoring import setup_telemetry

_ENABLE_TELEMETRY = os.getenv("TRAVEL_PLAN_ENABLE_TELEMETRY", "false").lower() == "true"
_TELEMETRY_ENDPOINT = os.getenv("TRAVEL_PLAN_OTLP_ENDPOINT", "http://localhost:4317")
telemetry = setup_telemetry(
    service_name="weekly_project_report",
    otlp_endpoint=_TELEMETRY_ENDPOINT,
    enable_traces=_ENABLE_TELEMETRY,
    enable_metrics=_ENABLE_TELEMETRY,
)

app = App(
    name="weekly_project_report",
    root_agent=root_agent,
    plugins=[
        # 업로드 파일을 artifact로 저장하고, user message에는 파일 참조 텍스트만 남긴다.
        SaveFilesAsArtifactsPlugin(attach_file_reference=False),
    ],
)


def monitoring_summary(window_minutes: int = 60) -> dict:
    """Runtime monitoring summary for operations/debugging."""
    return get_monitoring_summary(window_minutes=window_minutes)

__all__ = ["root_agent", "app", "monitoring_summary"]
