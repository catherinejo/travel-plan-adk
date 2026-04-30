"""Fan-out / Fan-in workflow for parallel multi-file Excel parsing.

멀티 파일 업로드 시 각 Excel 파일을 병렬로 파싱한 뒤
결과를 하나의 parsed_records로 합산한다.

Workflow 구조:
    START
     └─ list_excel_artifacts_node   (업로드된 Excel 아티팩트 목록 조회)
         └─ parse_single_artifact_node  (_ParallelWorker: 파일마다 병렬 파싱)
             └─ merge_parsed_results_node  (결과 병합 → state["parsed_records"])
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from google.adk.agents.context import Context
from google.adk.workflow import FunctionNode, Workflow, node

from .parse_tool import (
    _SUPPORTED_EXCEL_EXTENSIONS,
    _extract_inline_bytes,
    _parse_excel_file,
)


# ── Step 1: 아티팩트 목록 조회 ─────────────────────────────────────────────

async def list_excel_artifacts(ctx: Context) -> list[str]:
    """Session에 업로드된 Excel 아티팩트 이름 목록을 반환한다."""
    try:
        names = await ctx.list_artifacts()
    except Exception:
        return []
    return [
        n
        for n in names
        if Path(n.split(":", 1)[-1]).suffix.lower() in _SUPPORTED_EXCEL_EXTENSIONS
    ]


list_excel_artifacts_node = FunctionNode(
    func=list_excel_artifacts,
    name="list_excel_artifacts",
)


# ── Step 2: 단일 파일 파싱 (_ParallelWorker로 감싸져 병렬 실행됨) ──────────

@node(parallel_worker=True)
async def parse_single_artifact_node(ctx: Context, node_input: str) -> dict:
    """단일 Excel 아티팩트를 파싱한다.

    ``parallel_worker=True`` 로 감싸져 있으므로
    list_excel_artifacts_node가 반환한 이름 목록의 각 항목에 대해
    병렬로 실행된다.

    Args:
        node_input: 아티팩트 이름 (예: ``"report_A센터.xlsx"``)

    Returns:
        ``{"records": [...], "total_count": int, "anomalies": [...]}``
        또는 오류 시 ``{"error": "...", "records": [], ...}``
    """
    artifact_name = node_input

    artifact = await ctx.load_artifact(artifact_name)
    if artifact is None and not artifact_name.startswith("user:"):
        artifact = await ctx.load_artifact(f"user:{artifact_name}")

    if artifact is None or artifact.inline_data is None:
        return {
            "error": f"아티팩트를 불러올 수 없습니다: {artifact_name}",
            "records": [],
            "total_count": 0,
            "anomalies": [],
        }

    data = _extract_inline_bytes(artifact.inline_data.data)
    if not data:
        return {
            "error": f"아티팩트 데이터가 비어 있습니다: {artifact_name}",
            "records": [],
            "total_count": 0,
            "anomalies": [],
        }

    source_name = Path(artifact_name.split(":", 1)[-1]).name
    upload_dir = Path("uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    temp_path = (
        upload_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{source_name}"
    )
    temp_path.write_bytes(data)

    return _parse_excel_file(str(temp_path))


# ── Step 3: 결과 병합 ─────────────────────────────────────────────────────

def merge_parsed_results(ctx: Context, node_input: list[dict]) -> dict:
    """여러 파일의 파싱 결과를 병합하고 ``state["parsed_records"]`` 에 저장한다.

    ``node_input`` 은 ``parse_single_artifact_node`` (_ParallelWorker) 가
    반환하는 파일별 파싱 결과 목록이다.
    """
    all_records: list[Any] = []
    all_anomalies: list[Any] = []

    for result in node_input:
        if not isinstance(result, dict) or result.get("error"):
            continue
        all_records.extend(result.get("records", []))
        all_anomalies.extend(result.get("anomalies", []))

    if not all_records:
        return {
            "error": (
                "분석할 파일이 없습니다. "
                "UI에서 엑셀 파일을 첨부한 뒤 다시 요청하세요."
            ),
        }

    merged: dict[str, Any] = {
        "records": all_records,
        "total_count": len(all_records),
        "anomalies": all_anomalies,
    }
    ctx.state["parsed_records"] = merged
    return merged


merge_parsed_results_node = FunctionNode(
    func=merge_parsed_results,
    name="merge_parsed_results",
)


# ── Workflow 정의 ─────────────────────────────────────────────────────────

multi_file_parse_workflow = Workflow(
    name="MultiFileParseWorkflow",
    edges=[
        ("START", list_excel_artifacts_node),
        (list_excel_artifacts_node, parse_single_artifact_node),
        (parse_single_artifact_node, merge_parsed_results_node),
    ],
)
