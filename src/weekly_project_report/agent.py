# ADK web/run 진입점 — root_agent를 이 파일에서 노출한다.
from .core.agent import root_agent

__all__ = ["root_agent"]
