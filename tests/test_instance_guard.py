"""One MAK4I server per installation (mak4i.instance_guard), and the
cross-platform OS file lock under it (mak4i._filelock), with real
processes: a second server is refused before its start-up recovery can
touch a live server's in-flight work; the lock is released after a normal
stop and after an abrupt kill; the artifact store's compare-and-set holds
across processes. Runs on macOS, Linux and Windows."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest
from sqlalchemy import select

from mak4i import instance_guard
from mak4i._filelock import FileLock
from mak4i.changes.feed import SqlChangeFeed
from mak4i.identity import ControlPlane, schema
from mak4i.identity.sql_store import SqlControlPlaneStore

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
_ON_WINDOWS = sys.platform == "win32"


def _env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_python(code: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env=_env(**env), capture_output=True, text=True, timeout=60,
    )


# -- the OS file lock --------------------------------------------------------


def test_file_lock_excludes_another_process_until_released(tmp_path):
    path = tmp_path / "x.lock"
    probe = f"""
        from mak4i._filelock import FileLock
        lock = FileLock({str(path)!r})
        print("got" if lock.acquire(blocking=False) else "busy")
    """
    lock = FileLock(path)
    lock.acquire()
    try:
        assert _run_python(probe).stdout.strip() == "busy"
    finally:
        lock.release()
    assert _run_python(probe).stdout.strip() == "got"


def test_file_lock_excludes_within_one_process(tmp_path):
    path = tmp_path / "x.lock"
    first, second = FileLock(path), FileLock(path)
    assert first.acquire(blocking=False)
    assert not second.acquire(blocking=False)
    first.release()
    assert second.acquire(blocking=False)
    second.release()


def test_file_lock_is_released_when_its_process_is_killed(tmp_path):
    path = tmp_path / "x.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            from mak4i._filelock import FileLock
            FileLock({str(path)!r}).acquire()
            print("held", flush=True)
            time.sleep(120)
        """)],
        env=_env(), stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        assert not FileLock(path).acquire(blocking=False)
        holder.kill()  # SIGKILL / TerminateProcess: no clean-up runs
        holder.wait(timeout=10)
        lock = FileLock(path)
        deadline = time.time() + 10
        while not lock.acquire(blocking=False):
            assert time.time() < deadline, "lock still held after its process was killed"
            time.sleep(0.05)
        lock.release()
    finally:
        if holder.poll() is None:
            holder.kill()


def test_guard_lock_path_follows_the_local_store():
    assert instance_guard.lock_path_from_env({"MAK4I_LOCAL_STORE_DIR": "/data/artifacts"}) == Path(
        "/data/artifacts", instance_guard.LOCK_FILE_NAME
    )
    assert instance_guard.lock_path_from_env({}) == Path(
        instance_guard.DEFAULT_LOCAL_STORE_DIR, instance_guard.LOCK_FILE_NAME
    )
    assert instance_guard.lock_path_from_env({"MAK4I_STORE": "gcs"}) is None


# -- real server processes ---------------------------------------------------


@pytest.fixture
def installation(tmp_path):
    """A control-plane database and an artifact store, as one installation."""
    db_url = f"sqlite:///{(tmp_path / 'control-plane.db').as_posix()}"
    control_plane = ControlPlane(SqlControlPlaneStore(db_url, create_tables=True))
    org, owner = control_plane.onboard_organization(organization_name="Org", owner_display_name="Owner")
    project = control_plane.create_project(actor=owner, organization_id=org.organization_id, name="P")
    return {
        "db_url": db_url,
        "store_dir": tmp_path / "artifacts",
        "engine": control_plane._store.engine,  # noqa: SLF001
        "org": org.organization_id,
        "project": project.project_id,
    }


def _start_server(inst) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "mak4i.mcp_server"],
        env=_env(
            MAK4I_TRANSPORT="http", MAK4I_HOST="127.0.0.1", MAK4I_PORT=str(port),
            MAK4I_STORE="local", MAK4I_LOCAL_STORE_DIR=str(inst["store_dir"]),
            MAK4I_CONTROL_PLANE_DB=inst["db_url"],
        ),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    return proc, port


def _wait_ready(proc: subprocess.Popen, port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited early ({proc.returncode}): {proc.stdout.read()}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=0.5)
            return
        except Exception:  # noqa: BLE001 - readiness poll
            time.sleep(0.1)
    raise TimeoutError("server never became ready")


def _stop(proc: subprocess.Popen, *, abrupt: bool) -> None:
    if proc.poll() is not None:
        return
    if abrupt or _ON_WINDOWS:
        proc.kill()  # Windows has no SIGTERM: terminate() is TerminateProcess too
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _event_state(inst, sequence: int) -> str:
    e = schema.change_events
    with inst["engine"].connect() as conn:
        return conn.execute(
            select(e.c.state).where(
                (e.c.organization_id == inst["org"]) & (e.c.project == inst["project"]) & (e.c.sequence == sequence)
            )
        ).scalar_one()


def _in_flight_event(inst) -> int:
    """A change event as a live server leaves it between `begin` and the
    store write: pending, for an artifact that doesn't exist yet. A
    start-up reconciliation would abandon it."""
    pending = SqlChangeFeed(inst["engine"]).begin(
        inst["org"], inst["project"], "record.created",
        verify={"type": "artifact_exists", "artifact_id": "not-written-yet"},
        artifact_id="not-written-yet",
    )
    return pending.sequence


@pytest.mark.parametrize("abrupt", [False, True], ids=["normal-stop", "killed"])
def test_second_server_is_refused_before_recovery_and_restart_recovers(installation, abrupt):
    first, first_port = _start_server(installation)
    try:
        _wait_ready(first, first_port)
        sequence = _in_flight_event(installation)

        second, _ = _start_server(installation)
        second.wait(timeout=30)
        output = second.stdout.read()
        assert second.returncode == 2, output
        assert "another MAK4I server is already running for this installation" in output
        # Refused before its start-up recovery: the live server's in-flight
        # event is untouched, and the first server still answers.
        assert _event_state(installation, sequence) == "pending"
        urllib.request.urlopen(f"http://127.0.0.1:{first_port}/ready", timeout=5)
    finally:
        _stop(first, abrupt=abrupt)

    # The lock went with the first process (cleanly or not); the next
    # server starts and its start-up recovery decides the leftover event.
    restarted, port = _start_server(installation)
    try:
        _wait_ready(restarted, port)
        assert _event_state(installation, sequence) == "abandoned"
    finally:
        _stop(restarted, abrupt=False)


def test_cli_commands_still_run_alongside_the_server(installation):
    server, port = _start_server(installation)
    try:
        _wait_ready(server, port)
        result = subprocess.run(
            [sys.executable, "-m", "mak4i.cli", "org", "list"],
            env=_env(
                MAK4I_STORE="local", MAK4I_LOCAL_STORE_DIR=str(installation["store_dir"]),
                MAK4I_CONTROL_PLANE_DB=installation["db_url"], MAK4I_AUDIT_LOG=os.devnull,
            ),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert installation["org"] in result.stdout
    finally:
        _stop(server, abrupt=False)


# -- the artifact store across processes ---------------------------------------


def test_store_compare_and_set_admits_exactly_one_writer_across_processes(tmp_path):
    store_dir = tmp_path / "store"
    setup = f"""
        from datetime import datetime, timezone
        from mak4i.models import Artifact
        from mak4i.store.local_json import LocalJSONStore
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        LocalJSONStore({str(store_dir)!r}).put_new(Artifact(
            artifact_id="a", artifact_type="t", organization_id="org_x", project="p", title="t",
            content="v0", status="active", version="1.0", created_by="x", created_at=now,
            updated_at=now, lineage_id="a",
        ))
    """
    assert _run_python(setup).returncode == 0
    writer = f"""
        import sys, time
        from mak4i.store.base import ConcurrentModificationError
        from mak4i.store.local_json import LocalJSONStore
        store = LocalJSONStore({str(store_dir)!r})
        artifact, token = store.get("org_x", "p", "a")
        while time.time() < float(sys.argv[1]):
            time.sleep(0.001)
        try:
            store.put_if_match(artifact.model_copy(update={{"content": sys.argv[2]}}), token)
            print("won")
        except ConcurrentModificationError:
            print("lost")
    """
    start_at = str(time.time() + 3)
    writers = [
        subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(writer), start_at, f"writer-{i}"],
            env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for i in range(6)
    ]
    results = []
    for proc in writers:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
        results.append(out.strip())
    assert sorted(results) == ["lost"] * 5 + ["won"]

    from mak4i.store.local_json import LocalJSONStore

    artifact, token = LocalJSONStore(store_dir).get("org_x", "p", "a")
    assert token == "2" and artifact.content.startswith("writer-")


def test_store_records_are_utf8_on_every_platform(tmp_path):
    from datetime import datetime, timezone

    from mak4i.models import Artifact
    from mak4i.store.local_json import LocalJSONStore

    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    text = "Cache → Redis; café; 漢字; emoji 🚀"
    store = LocalJSONStore(tmp_path / "store")
    store.put_new(Artifact(
        artifact_id="u", artifact_type="t", organization_id="org_x", project="p", title=text,
        content=text, status="active", version="1.0", created_by="x", created_at=now,
        updated_at=now, lineage_id="u",
    ))
    artifact, _ = store.get("org_x", "p", "u")
    assert artifact.content == text and artifact.title == text
    raw = (tmp_path / "store" / "org_x" / "p" / "u.json").read_bytes()
    assert text.encode("utf-8") in raw or "\\u2192" in raw.decode("utf-8")
