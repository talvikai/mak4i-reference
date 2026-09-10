import pytest

from mak4i.config import build_control_plane_from_env as _build_control_plane
from mak4i.config import build_store_from_env as _build_store
from mak4i.identity import ControlPlane
from mak4i.identity.sql_store import SqlControlPlaneStore
from mak4i.store.local_json import LocalJSONStore


def test_default_backend_is_local(tmp_path, monkeypatch):
    monkeypatch.delenv("MAK4I_STORE", raising=False)
    monkeypatch.chdir(tmp_path)
    store = _build_store()
    assert isinstance(store, LocalJSONStore)


def test_local_backend_honors_store_dir_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MAK4I_STORE", "local")
    monkeypatch.setenv("MAK4I_LOCAL_STORE_DIR", str(tmp_path / "custom"))
    store = _build_store()
    assert isinstance(store, LocalJSONStore)
    assert store._root == tmp_path / "custom"


def test_gcs_backend_is_selected_and_configured(monkeypatch):
    monkeypatch.setenv("MAK4I_STORE", "gcs")
    monkeypatch.setenv("MAK4I_GCS_BUCKET", "example-mak4i-artifacts")
    monkeypatch.setenv("MAK4I_GCP_PROJECT", "example-mak4i-project")

    captured = {}

    class _FakeGCSArtifactStore:
        def __init__(self, bucket_name, *, project=None, client=None):
            captured["bucket_name"] = bucket_name
            captured["project"] = project

    monkeypatch.setattr("mak4i.store.gcs.GCSArtifactStore", _FakeGCSArtifactStore)

    store = _build_store()

    assert isinstance(store, _FakeGCSArtifactStore)
    assert captured == {
        "bucket_name": "example-mak4i-artifacts",
        "project": "example-mak4i-project",
    }


def test_gcs_backend_requires_bucket_env_var(monkeypatch):
    monkeypatch.setenv("MAK4I_STORE", "gcs")
    monkeypatch.delenv("MAK4I_GCS_BUCKET", raising=False)
    with pytest.raises(KeyError):
        _build_store()


def test_unknown_backend_rejected(monkeypatch):
    monkeypatch.setenv("MAK4I_STORE", "not-a-real-backend")
    with pytest.raises(ValueError, match="MAK4I_STORE"):
        _build_store()


def test_control_plane_default_db_is_local_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("MAK4I_CONTROL_PLANE_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    control_plane = _build_control_plane()
    assert isinstance(control_plane, ControlPlane)
    assert isinstance(control_plane._store, SqlControlPlaneStore)
    assert str(control_plane._store.engine.url).startswith("sqlite:///")


def test_control_plane_honors_db_env_var(tmp_path, monkeypatch):
    db_path = tmp_path / "custom-control-plane.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{db_path}")
    control_plane = _build_control_plane()
    assert str(db_path) in str(control_plane._store.engine.url)


def test_control_plane_create_tables_flag_builds_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh-control-plane.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{db_path}")
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", "1")
    control_plane = _build_control_plane()

    # onboard_organization writes to the organizations/principals tables —
    # this only succeeds if create_tables actually built the schema.
    organization, owner = control_plane.onboard_organization(
        organization_name="Test Org", owner_display_name="Owner"
    )
    assert control_plane.get_principal(owner.principal_id) == owner
    assert organization.name == "Test Org"
