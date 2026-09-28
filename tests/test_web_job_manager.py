from pathlib import Path

from webapp.job_manager import JobManager


def test_event_sequence_is_persisted(tmp_path: Path) -> None:
    manager = JobManager(db_path=tmp_path / "jobs.db", workspace_dir=tmp_path / "workspace")
    first = manager.publish("run-test", {"type": "queued", "message": "queued"})
    second = manager.publish("run-test", {"type": "stage", "stage": "eda_done"})

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert [event["type"] for event in manager.get_events("run-test")] == ["queued", "stage"]
    assert manager.get_events("run-test", after=1)[0]["stage"] == "eda_done"


def test_artifact_resolution_rejects_path_escape(tmp_path: Path) -> None:
    manager = JobManager(db_path=tmp_path / "jobs.db", workspace_dir=tmp_path / "workspace")
    run_id = "run-safe-path"
    manager.submit(
        run_id=run_id,
        input_path=str(tmp_path / "input.csv"),
        description="sample",
        user_request="analyze",
    )

    assert manager.resolve_artifact(run_id, "../../outside.txt") is None
