#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  주간 보고서 ADK 로컬 실행 스크립트
#  사용법:
#    ./run.sh          # Web UI 모드 (기본)
#    ./run.sh cli      # CLI 대화 모드
#    ./run.sh api      # REST API 서버 모드
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# .env 로드
if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
else
  echo "[경고] .env 파일이 없습니다. .env.example을 복사해 설정하세요."
  echo "  cp .env.example .env"
fi

# GOOGLE_API_KEY 확인
if [ -z "${GOOGLE_API_KEY:-}" ]; then
  echo "[오류] GOOGLE_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
  exit 1
fi

PORT="${ADK_PORT:-8080}"
AGENTS_DIR="src"
MODE="${1:-web}"

case "$MODE" in
  web)
    echo "────────────────────────────────────────"
    echo " ADK Web UI 시작"
    echo " URL: http://localhost:${PORT}"
    echo " 에이전트: weekly_project_report"
    echo " 종료: Ctrl+C"
    echo "────────────────────────────────────────"
    uv run adk web "$AGENTS_DIR" --port "$PORT"
    ;;
  cli)
    echo "────────────────────────────────────────"
    echo " ADK CLI 대화 모드"
    echo " 종료: exit 또는 Ctrl+C"
    echo "────────────────────────────────────────"
    uv run adk run "$AGENTS_DIR/weekly_project_report"
    ;;
  api)
    echo "────────────────────────────────────────"
    echo " ADK REST API 서버 시작"
    echo " URL: http://localhost:${PORT}"
    echo " 종료: Ctrl+C"
    echo "────────────────────────────────────────"
    uv run adk api_server "$AGENTS_DIR" --port "$PORT"
    ;;
  *)
    echo "알 수 없는 모드: $MODE"
    echo "사용법: ./run.sh [web|cli|api]"
    exit 1
    ;;
esac
