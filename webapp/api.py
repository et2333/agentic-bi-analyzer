from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from webapp.job_manager import SERVICE_DIR, job_manager


STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOAD_DIR = SERVICE_DIR / "uploads"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _configure_console_encoding() -> None:
    """Prevent background jobs from failing on Unicode diagnostic output.

    Windows terminals commonly default to GBK, while the pipeline logs symbols
    such as check marks and arrows. Logging must never terminate a pipeline run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _configure_console_encoding()
    job_manager.start()
    yield
    job_manager.stop()


app = FastAPI(
    title="MADS-P Agentic Analytics",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "worker": "single"}


@app.post("/api/runs", status_code=202)
async def create_run(
    data_file: UploadFile = File(...),
    description: str = Form(...),
    user_request: str = Form(...),
    target_column: str = Form(""),
    task_type: str = Form(""),
) -> dict:
    filename = Path(data_file.filename or "dataset.csv").name
    if Path(filename).suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="Only CSV files are supported in this MVP")
    description = description.strip()
    user_request = user_request.strip()
    if not description or not user_request:
        raise HTTPException(status_code=400, detail="Description and analysis objective are required")
    task_type = task_type.strip().lower()
    target_column = target_column.strip()
    if task_type and task_type not in {"classification", "regression"}:
        raise HTTPException(status_code=400, detail="task_type must be classification or regression")
    if bool(task_type) != bool(target_column):
        raise HTTPException(status_code=400, detail="target_column and task_type must be provided together")

    run_id = f"run-{uuid4().hex[:12]}"
    upload_dir = UPLOAD_DIR / run_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    input_path = upload_dir / filename
    total = 0
    with input_path.open("wb") as output:
        while chunk := await data_file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                output.close()
                input_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="CSV exceeds the 50 MB MVP limit")
            output.write(chunk)
    if total == 0:
        input_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded CSV is empty")

    job = job_manager.submit(
        run_id=run_id,
        input_path=str(input_path.resolve()),
        description=description,
        user_request=user_request,
        target_column=target_column,
        task_type=task_type,
    )
    return {"run_id": run_id, "status": job["status"], "queue_position": job["queue_position"]}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    job = job_manager.get_job(run_id)
    if not job:
        raise HTTPException(status_code=404, detail="Run not found")
    return job


@app.get("/api/runs/{run_id}/events")
def stream_events(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
    if not job_manager.get_job(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    header_id = request.headers.get("last-event-id", "")
    cursor = int(header_id) if header_id.isdigit() else max(after, 0)

    def generate() -> Iterator[str]:
        nonlocal cursor
        while True:
            events = job_manager.get_events(run_id, cursor)
            if events:
                for event in events:
                    cursor = event["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if events[-1].get("type") in {"succeeded", "failed"}:
                    return
            job = job_manager.get_job(run_id)
            if not job or job["status"] in {"succeeded", "failed"}:
                return
            job_manager.wait_for_events(timeout=10)
            if not job_manager.get_events(run_id, cursor):
                yield ": keep-alive\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}/result")
def get_result(run_id: str) -> dict:
    result = job_manager.get_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return result


@app.get("/api/runs/{run_id}/artifacts/{artifact_path:path}")
def download_artifact(run_id: str, artifact_path: str) -> FileResponse:
    path = job_manager.resolve_artifact(run_id, artifact_path)
    if path is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path, filename=path.name)
