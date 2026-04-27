"""Model configuration loaded from environment."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# 우선순위: DEFAULT_AGENT_MODEL > WEEKLY_PROJECT_REPORT_AGENT_MODEL > 기본값
AGENT_MODEL = (
    os.getenv("DEFAULT_AGENT_MODEL")
    or os.getenv("WEEKLY_PROJECT_REPORT_AGENT_MODEL")
    or "gemini-2.5-flash"
).strip() or "gemini-2.5-flash"

