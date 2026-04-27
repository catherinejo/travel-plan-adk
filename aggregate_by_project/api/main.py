from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from fastapi import File
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse

from weekly_project_report.core.report_tool import render_pdf_function

Stage = Literal["queued", "parsing", "mapping", "reporting", "rendering", "done", "failed"]


@dataclass
class JobState:
    job_id: str
    filename: str
    uploaded_path: str
    stage: Stage = "queued"
    progress: int = 0
    pdf_path: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def touch(self, stage: Stage, progress: int) -> None:
        self.stage = stage
        self.progress = max(0, min(progress, 100))
        self.updated_at = datetime.utcnow().isoformat()


app = FastAPI(title="Travel Plan Report API")
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
JOBS: dict[str, JobState] = {}


async def _run_pipeline(job_id: str) -> None:
    job = JOBS[job_id]
    try:
        job.touch("parsing", 15)
        await asyncio.sleep(0.2)

        job.touch("mapping", 45)
        await asyncio.sleep(0.2)

        job.touch("reporting", 75)
        markdown_report = (
            "# 주간 프로젝트 보고서\n\n"
            f"- 원본 파일: {job.filename}\n"
            f"- 생성 시각: {datetime.utcnow().isoformat()}\n"
            "- 상태: 자동 생성 완료\n"
        )

        job.touch("rendering", 90)
        rendered = await render_pdf_function(markdown=markdown_report)
        pdf_path = rendered.get("pdf_path")
        if not isinstance(pdf_path, str) or not pdf_path.strip():
            raise RuntimeError(rendered.get("error") or "PDF 생성 결과가 비어 있습니다.")

        job.pdf_path = pdf_path
        job.touch("done", 100)
    except Exception as exc:
        job.stage = "failed"
        job.error = str(exc)
        job.updated_at = datetime.utcnow().isoformat()


@app.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, str]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일명이 없습니다.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".xlsx", ".xls", ".xlsm", ".csv"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 파일 형식입니다.")

    job_id = str(uuid4())
    save_path = UPLOAD_DIR / f"{job_id}_{file.filename}"
    content = await file.read()
    save_path.write_bytes(content)

    JOBS[job_id] = JobState(job_id=job_id, filename=file.filename, uploaded_path=str(save_path))
    asyncio.create_task(_run_pipeline(job_id))
    return {"job_id": job_id}


@app.get("/status/{job_id}")
async def status(job_id: str) -> dict[str, object]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_id를 찾을 수 없습니다.")

    response: dict[str, object] = {
        "job_id": job.job_id,
        "stage": job.stage,
        "progress": job.progress,
    }
    if job.error:
        response["error"] = job.error
    if job.pdf_path:
        response["pdf_path"] = job.pdf_path
    return response


@app.get("/report/{job_id}/pdf", response_model=None)
async def report_pdf(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_id를 찾을 수 없습니다.")

    if job.stage != "done" or not job.pdf_path:
        return JSONResponse(
            status_code=202,
            content={"job_id": job_id, "stage": job.stage, "progress": job.progress},
        )

    pdf_path = Path(job.pdf_path)
    if not pdf_path.exists():
        raise HTTPException(status_code=500, detail="PDF 파일을 찾을 수 없습니다.")

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=pdf_path.name,
    )


@app.post("/report")
async def report_alias(request: Request, file: UploadFile = File(...)) -> dict[str, str]:
    """Frontend 호환용 alias endpoint."""
    result = await upload(file=file)
    job_id = result["job_id"]
    base_url = str(request.base_url).rstrip("/")
    return {
        "job_id": job_id,
        "download_url": f"{base_url}/report/{job_id}/pdf",
    }
