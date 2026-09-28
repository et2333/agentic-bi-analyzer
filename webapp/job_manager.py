from __future__ import annotations

import json
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = PROJECT_ROOT / "artifacts" / "service"
WORKSPACE_DIR = PROJECT_ROOT / "artifacts" / "workspace"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    """SQLite-backed job registry with one serial pipeline worker."""

    def __init__(
        self,
        db_path: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        SERVICE_DIR.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path or SERVICE_DIR / "jobs.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_dir = workspace_dir or WORKSPACE_DIR
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._condition = threading.Condition()
        self._db_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    input_path TEXT NOT NULL,
                    description TEXT NOT NULL,
                    user_request TEXT NOT NULL,
                    target_column TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT '',
                    run_dir TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq)
                );
                """
            )

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='failed', finished_at=?, error=? WHERE status='running'",
                (_utc_now(), "Service restarted while the task was running"),
            )
            queued = conn.execute(
                "SELECT run_id FROM jobs WHERE status='queued' ORDER BY created_at"
            ).fetchall()
        for row in queued:
            self._queue.put(row["run_id"])
        self._worker = threading.Thread(target=self._worker_loop, name="pipeline-worker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=5)

    def submit(
        self,
        *,
        run_id: str,
        input_path: str,
        description: str,
        user_request: str,
        target_column: str = "",
        task_type: str = "",
    ) -> dict[str, Any]:
        run_dir = self.workspace_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        created_at = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    run_id, status, created_at, input_path, description,
                    user_request, target_column, task_type, run_dir
                ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    created_at,
                    input_path,
                    description,
                    user_request,
                    target_column,
                    task_type,
                    str(run_dir),
                ),
            )
        self.publish(run_id, {"type": "queued", "stage": "queued", "message": "Task queued"})
        self._queue.put(run_id)
        return self.get_job(run_id) or {}

    def get_job(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["result"] = json.loads(result.pop("result_json") or "null")
            if result["status"] == "queued":
                position = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status='queued' AND created_at <= ?",
                    (result["created_at"],),
                ).fetchone()[0]
                result["queue_position"] = position
            else:
                result["queue_position"] = None
            return result

    def publish(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._db_lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id=?", (run_id,)
            ).fetchone()
            seq = int(row[0])
            event = {**payload, "run_id": run_id, "seq": seq, "created_at": _utc_now()}
            conn.execute(
                "INSERT INTO events (run_id, seq, created_at, payload_json) VALUES (?, ?, ?, ?)",
                (run_id, seq, event["created_at"], json.dumps(event, ensure_ascii=False, default=str)),
            )
        with self._condition:
            self._condition.notify_all()
        return event

    def get_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM events WHERE run_id=? AND seq>? ORDER BY seq",
                (run_id, after),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def wait_for_events(self, timeout: float = 10.0) -> None:
        with self._condition:
            self._condition.wait(timeout=timeout)

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        job = self.get_job(run_id)
        if not job:
            return []
        root = Path(job["run_dir"]).resolve()
        if not root.exists():
            return []
        items = []
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            items.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "kind": path.suffix.lower().lstrip(".") or "file",
                }
            )
        return items

    def resolve_artifact(self, run_id: str, relative_path: str) -> Path | None:
        job = self.get_job(run_id)
        if not job:
            return None
        root = Path(job["run_dir"]).resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def get_result(self, run_id: str) -> dict[str, Any] | None:
        job = self.get_job(run_id)
        if not job:
            return None
        result = dict(job.get("result") or {})
        report_path = Path(job["run_dir"]) / "reports" / "business_insight_report.md"
        result["business_report"] = (
            report_path.read_text(encoding="utf-8", errors="replace") if report_path.is_file() else ""
        )
        artifacts = self.list_artifacts(run_id)
        result["artifacts"] = artifacts
        result["charts"] = [item for item in artifacts if item["kind"] in {"png", "jpg", "jpeg", "webp"}]
        return result

    def _update_job(self, run_id: str, **fields: Any) -> None:
        allowed = {"status", "started_at", "finished_at", "result_json", "error"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE run_id=?",
                (*updates.values(), run_id),
            )

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            run_id = self._queue.get()
            if run_id is None:
                break
            try:
                self._run_job(run_id)
            finally:
                self._queue.task_done()

    def _run_job(self, run_id: str) -> None:
        from main import run_pipeline

        job = self.get_job(run_id)
        if not job or job["status"] != "queued":
            return
        self._update_job(run_id, status="running", started_at=_utc_now(), error=None)
        self.publish(run_id, {"type": "running", "stage": "initializing", "message": "Worker started the task"})
        try:
            final_state = run_pipeline(
                data_path=job["input_path"],
                description=job["description"],
                user_request=job["user_request"],
                target_column=job["target_column"],
                task_type=job["task_type"],
                run_id=run_id,
                run_dir=job["run_dir"],
                event_callback=lambda event: self.publish(run_id, event),
            )
            terminal_stage = str(final_state.get("current_stage") or "")
            best_model = final_state.get("best_model") or {}
            if terminal_stage.endswith("_failed"):
                raise RuntimeError(f"Pipeline stopped at failed stage: {terminal_stage}")
            if not best_model.get("final_test_metrics"):
                raise RuntimeError("Pipeline ended without final test metrics")
            token_path = Path(job["run_dir"]) / "reports" / "token_usage.json"
            token_usage = (
                json.loads(token_path.read_text(encoding="utf-8")) if token_path.is_file() else {}
            )
            summary = {
                "current_stage": final_state.get("current_stage", ""),
                "best_model": best_model,
                "timing": final_state.get("timing") or {},
                "token_usage": token_usage,
            }
            self._update_job(
                run_id,
                status="succeeded",
                finished_at=_utc_now(),
                result_json=json.dumps(summary, ensure_ascii=False, default=str),
            )
            self.publish(run_id, {"type": "succeeded", "stage": "completed", "message": "Analysis completed"})
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"
            self._update_job(run_id, status="failed", finished_at=_utc_now(), error=message)
            self.publish(run_id, {"type": "failed", "stage": "failed", "message": message})


job_manager = JobManager()
