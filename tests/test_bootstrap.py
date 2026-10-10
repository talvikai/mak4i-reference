"""deploy/bootstrap/mak4i-enterprise, exercised as a real bash program.

Docker and the host tools the bootstrap reads (uname, getent, dig, curl,
ss, getconf, git) are replaced by stubs on PATH (tests/bootstrap_stubs/
docker, plus small generated scripts), so these tests cover argument
validation, preflight decisions, idempotency, secret-file handling, safe
uninstall/restore and failure paths without a Docker daemon. The real
end-to-end lifecycle on a clean Linux host is covered by the RC4 release
acceptance run (see the release report), not here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUBS = Path(__file__).resolve().parent / "bootstrap_stubs"
PUBLIC_IP = "203.0.113.10"


def _script(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def host(tmp_path):
    """A throwaway 'repository' + fake host. Returns a helper namespace."""
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copytree(REPO / "deploy" / "bootstrap", repo / "deploy" / "bootstrap")
    shutil.copytree(REPO / "deploy" / "compose", repo / "deploy" / "compose")
    shutil.copy(REPO / "Dockerfile", repo / "Dockerfile")
    for leftover in (repo / "deploy" / "compose").glob(".env"):
        leftover.unlink()

    stub = tmp_path / "stub"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stub.mkdir()
    shutil.copy(STUBS / "docker", bin_ / "docker")
    (bin_ / "docker").chmod(0o755)
    _script(bin_ / "uname", 'case "$1" in -m) echo "${STUB_ARCH:-x86_64}";; *) echo Linux;; esac\n')
    _script(bin_ / "getconf", 'echo "${STUB_CPUS:-4}"\n')
    _script(bin_ / "ss", 'printf "%s\\n" ${STUB_LISTEN:-} | while read -r p; do [ -n "$p" ] && echo "LISTEN 0 4096 0.0.0.0:$p 0.0.0.0:*"; done; exit 0\n')
    # Like the real tools, getent exits 2 for an unknown name.
    _script(bin_ / "getent", '[ -n "${STUB_DNS_LOCAL:-}" ] || exit 2\nfor ip in $STUB_DNS_LOCAL; do echo "$ip STREAM $3"; done\n')
    _script(bin_ / "dig", 'for ip in ${STUB_DNS_PUBLIC:-}; do echo "$ip"; done\n')
    # Real psql fails when PostgreSQL is down; the docker stub does the
    # same for exec into a stopped service.
    # curl: instance metadata is unavailable; reachability probes answer 200;
    # the local HTTPS check answers per STUB_TLS_READY.
    _script(
        bin_ / "curl",
        'for a in "$@"; do case "$a" in *169.254.169.254*) exit 22;; esac; done\n'
        'for a in "$@"; do case "$a" in https://*/ready) [ "${STUB_TLS_READY:-1}" = 1 ] && printf ready && exit 0; exit 7;; esac; done\n'
        'for a in "$@"; do [ "$a" = "%{http_code}" ] && printf 200 && exit 0; done\n'
        "exit 0\n",
    )
    _script(bin_ / "git", 'echo "${STUB_GIT_TAG:-v0.1.0-rc.5}"\n')

    os_release = tmp_path / "os-release"
    os_release.write_text('ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n')
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        8123456 kB\n")
    home = tmp_path / "home"
    home.mkdir()

    class Host:
        root = repo
        compose_dir = repo / "deploy" / "compose"
        env_file = repo / "deploy" / "compose" / ".env"
        state_dir = tmp_path / "state"
        backups = tmp_path / "backups"

        def __init__(self):
            self.env = {
                "PATH": f"{bin_}:{os.environ['PATH']}",
                "HOME": str(home),
                "STUB_DIR": str(stub),
                "MAK4I_STATE_DIR": str(self.state_dir),
                "MAK4I_BOOTSTRAP_OS_RELEASE": str(os_release),
                "MAK4I_BOOTSTRAP_MEMINFO": str(meminfo),
                "STUB_DNS_LOCAL": PUBLIC_IP,
                "STUB_DNS_PUBLIC": PUBLIC_IP,
                "NO_COLOR": "1",
            }
            self.set_state()

        def set_state(self, **values):
            state = {"volumes": [], "running": [], "images_present": True}
            state.update(values)
            (stub / "state.json").write_text(json.dumps(state))

        def state(self):
            return json.loads((stub / "state.json").read_text())

        def calls(self):
            path = stub / "calls.jsonl"
            if not path.exists():
                return []
            return [json.loads(line) for line in path.read_text().splitlines()]

        def compose_calls(self, verb):
            out = []
            for call in self.calls():
                if call[:1] != ["compose"]:
                    continue
                rest = [a for a in call[1:]]
                # drop global flags
                cleaned, skip = [], False
                for a in rest:
                    if skip:
                        skip = False
                        continue
                    if a in ("--project-directory", "--file", "--env-file", "--profile"):
                        skip = True
                        continue
                    cleaned.append(a)
                if cleaned[:1] == [verb]:
                    out.append(cleaned)
            return out

        def run(self, *args, env=None, stdin=None):
            proc = subprocess.run(
                ["bash", str(repo / "deploy" / "bootstrap" / "mak4i-enterprise"), *args],
                env={**self.env, **(env or {})},
                capture_output=True,
                text=True,
                input=stdin,
                timeout=120,
            )
            proc.output = proc.stdout + proc.stderr
            return proc

        def logs(self):
            logs_dir = self.state_dir / "logs"
            return "".join(p.read_text() for p in sorted(logs_dir.glob("*.log"))) if logs_dir.exists() else ""

    return Host()


MUTATING = {"up", "pull", "build", "down", "restart", "stop", "start", "run", "cp"}


def _mutations(host):
    return [c for verb in MUTATING for c in host.compose_calls(verb)]


def _install_private(host, *extra):
    return host.run("install", "--profile", "private-http", "--non-interactive", *extra)


# -- arguments and help --------------------------------------------------------


def test_help_lists_commands_and_exit_codes(host):
    r = host.run("--help")
    assert r.returncode == 0
    for word in ("preflight", "install", "status", "restart", "upgrade", "backup", "restore", "uninstall"):
        assert word in r.stdout
    assert "3 preflight failed" in r.stdout
    assert "--dry-run" in r.stdout and "--non-interactive" in r.stdout


def test_exit_codes_in_help_match_the_constants(host):
    common = (REPO / "deploy/bootstrap/lib/common.sh").read_text()
    codes = dict(re.findall(r"readonly (EX_[A-Z]+)=(\d)", common))
    assert codes == {
        "EX_OK": "0", "EX_FAILURE": "1", "EX_USAGE": "2", "EX_PREFLIGHT": "3", "EX_EXISTING": "4",
        "EX_UNHEALTHY": "5", "EX_ABORTED": "6", "EX_UNSUPPORTED": "7", "EX_BACKUP": "8",
    }


@pytest.mark.parametrize(
    "args",
    [
        ["frobnicate"],
        ["install", "--profile", "https"],
        ["install", "--profile", "tls", "--domain", "not a domain"],
        ["install", "--profile", "tls", "--domain", "203.0.113.10"],
        ["install", "--profile", "private-http", "--http-port", "70000"],
        ["install", "--profile", "tls", "--domain", "mak4i.example.com", "--bind", "10.0.0.5"],
        ["install", "--profile", "private-http", "--instance-name", "Acme $(id)"],
        ["status", "--profile", "tls"],
        ["uninstall", "--yes-destroy-data"],
        ["uninstall", "--remove-images"],
        ["uninstall", "--preserve-data", "--destroy-data"],
        ["backup", "--backup-dir"],
        ["upgrade", "--from-version", "latest"],
    ],
)
def test_invalid_command_lines_exit_2_without_touching_docker(host, args):
    r = host.run(*args)
    assert r.returncode == 2, r.output
    assert _mutations(host) == []


def test_secrets_are_not_accepted_as_options(host):
    r = host.run("install", "--profile", "private-http", "--postgres-password", "hunter2")
    assert r.returncode == 2
    assert "not an option" in r.output


# -- preflight -------------------------------------------------------------------


def test_preflight_passes_on_a_ready_host_and_changes_nothing(host):
    r = host.run("preflight", "--profile", "tls", "--domain", "mak4i.example.com", "--public-ip", PUBLIC_IP)
    assert r.returncode == 0, r.output
    for check in ("Linux distribution", "Architecture", "Docker Engine", "Docker Compose plugin",
                  "DNS A record", "Local port 80", "Local port 443", "Port 8080 exposure",
                  "Outbound HTTPS: Let's Encrypt", "Compose configuration"):
        assert check in r.output
    assert "[FAIL]" not in r.output
    assert _mutations(host) == []
    assert not host.env_file.exists()


def test_preflight_fails_clearly_when_docker_is_not_usable(host):
    host.set_state(docker_error="permission denied while trying to connect to the Docker daemon socket")
    r = host.run("preflight", "--profile", "private-http")
    assert r.returncode == 3
    assert "usermod -aG docker" in r.output


def test_preflight_fails_when_dns_points_elsewhere(host):
    r = host.run("preflight", "--profile", "tls", "--domain", "mak4i.example.com", "--public-ip", "198.51.100.7")
    assert r.returncode == 3
    assert "Point the A record at 198.51.100.7" in r.output


def test_preflight_nxdomain_is_a_failure_with_the_propagation_hint(host):
    r = host.run(
        "preflight", "--profile", "tls", "--domain", "mak4i.example.com", "--public-ip", PUBLIC_IP,
        env={"STUB_DNS_LOCAL": "", "STUB_DNS_PUBLIC": ""},
    )
    assert r.returncode == 3
    assert "NXDOMAIN" in r.output


def test_preflight_detects_a_cached_negative_dns_answer(host):
    # Public DNS answers, this VM's resolver doesn't yet (verified on GCP).
    r = host.run(
        "preflight", "--profile", "tls", "--domain", "mak4i.example.com", "--public-ip", PUBLIC_IP,
        env={"STUB_DNS_LOCAL": ""},
    )
    assert r.returncode == 0, r.output
    assert "cached negative answer" in r.output and "--resolve" in r.output


def test_preflight_fails_when_ports_80_443_are_taken(host):
    r = host.run(
        "preflight", "--profile", "tls", "--domain", "mak4i.example.com", "--public-ip", PUBLIC_IP,
        env={"STUB_LISTEN": "80 443"},
    )
    assert r.returncode == 3
    assert "Local port 80" in r.output and "already in use" in r.output


@pytest.mark.parametrize(
    ("bind", "extra", "code", "text"),
    [
        ("127.0.0.1", [], 0, "[PASS] Port 8080 exposure"),
        ("10.1.2.3", [], 0, "[WARN] Port 8080 exposure"),
        ("0.0.0.0", [], 3, "[FAIL] Port 8080 exposure"),
        ("203.0.113.9", [], 3, "[FAIL] Port 8080 exposure"),
        ("203.0.113.9", ["--allow-public-bind"], 0, "[WARN] Port 8080 exposure"),
    ],
)
def test_private_http_bind_safety(host, bind, extra, code, text):
    r = host.run("preflight", "--profile", "private-http", "--bind", bind, *extra)
    assert r.returncode == code, r.output
    assert text in r.output


def test_preflight_warns_on_untested_distributions_and_low_memory(host, tmp_path):
    (tmp_path / "os-release").write_text('ID=arch\nPRETTY_NAME="Arch Linux"\n')
    (tmp_path / "meminfo").write_text("MemTotal:        2097152 kB\n")
    r = host.run("preflight", "--profile", "private-http")
    assert r.returncode == 0
    assert "not a tested distribution" in r.output
    assert "[WARN] Memory" in r.output


# -- install ---------------------------------------------------------------------


def _password(host):
    return re.search(r"^POSTGRES_PASSWORD=(.*)$", host.env_file.read_text(), re.M).group(1)


def test_install_private_http_creates_a_private_env_and_verifies(host):
    r = _install_private(host)
    assert r.returncode == 0, r.output
    mode = stat.S_IMODE(host.env_file.stat().st_mode)
    assert mode == 0o600
    password = _password(host)
    assert re.fullmatch(r"[0-9a-f]{64}", password)
    env = host.env_file.read_text()
    assert "MAK4I_INSTALL_STATE=complete" in env
    assert "MAK4I_INSTALLED_RELEASE=v0.1.0-rc.5" in env
    assert "MAK4I_BOOTSTRAP_PROFILE=private-http" in env
    assert "MAK4I_HTTP_BIND=127.0.0.1" in env
    # The secret is never shown or logged.
    assert password not in r.output
    assert password not in host.logs()
    assert password not in json.dumps(host.calls())
    up = host.compose_calls("up")
    assert up and "--wait" in up[0]
    assert host.compose_calls("build")
    assert "installed and healthy" in r.output


def test_repeated_install_keeps_secrets_and_changes_nothing_it_owns(host):
    assert _install_private(host).returncode == 0
    first = host.env_file.read_text()
    r = _install_private(host)
    assert r.returncode == 0, r.output
    assert host.env_file.read_text() == first  # same password, same settings
    assert "no secrets are regenerated" in r.output


def test_install_refuses_orphaned_data_volumes(host):
    host.set_state(volumes=["mak4i_pgdata", "mak4i_artifacts"])
    r = _install_private(host)
    assert r.returncode == 4
    assert "data volumes already exist" in r.output
    assert not host.env_file.exists()
    assert _mutations(host) == []


def test_install_refuses_a_manual_installation(host):
    host.env_file.write_text("POSTGRES_PASSWORD=abc\n")
    r = _install_private(host)
    assert r.returncode == 4
    assert "upgrade" in r.output


def test_install_refuses_to_change_the_profile(host):
    assert _install_private(host).returncode == 0
    r = host.run("install", "--profile", "tls", "--domain", "mak4i.example.com", "--public-ip", PUBLIC_IP)
    assert r.returncode == 4
    assert "won't change it" in r.output


def test_install_dry_run_changes_nothing(host):
    r = _install_private(host, "--dry-run")
    assert r.returncode == 0, r.output
    assert not host.env_file.exists()
    assert _mutations(host) == []
    assert "[dry-run]" in r.output


def test_failed_start_leaves_a_resumable_pending_installation(host):
    host.set_state(up_fails=True)
    r = _install_private(host)
    assert r.returncode == 5
    assert "INCOMPLETE (state: pending)" in r.output
    assert "re-run the same install command" in r.output
    assert "MAK4I_INSTALL_STATE=pending" in host.env_file.read_text()
    host.set_state()
    r = _install_private(host)
    assert r.returncode == 0, r.output
    assert "MAK4I_INSTALL_STATE=complete" in host.env_file.read_text()


def test_tls_install_waits_for_the_certificate_and_explains_a_timeout(host):
    r = host.run(
        "install", "--profile", "tls", "--domain", "mak4i.example.com", "--public-ip", PUBLIC_IP,
        "--cert-timeout", "1", "--non-interactive", env={"STUB_TLS_READY": "0"},
    )
    assert r.returncode == 5, r.output
    assert "no valid certificate" in r.output
    assert "NXDOMAIN" in r.output and "80 and 443" in r.output
    assert "MAK4I_INSTALL_STATE=complete" in host.env_file.read_text()


def test_tls_install_binds_mak4i_to_loopback_only(host):
    r = host.run("install", "--profile", "tls", "--domain", "mak4i.example.com", "--public-ip", PUBLIC_IP, "--non-interactive")
    assert r.returncode == 0, r.output
    env = host.env_file.read_text()
    assert "MAK4I_HTTP_BIND=127.0.0.1" in env
    assert "MAK4I_DOMAIN=mak4i.example.com" in env
    assert "MAK4I_PUBLIC_ENDPOINT=https://mak4i.example.com/mcp" in env


def test_env_file_is_parsed_not_executed(host, tmp_path):
    assert _install_private(host).returncode == 0
    marker = tmp_path / "pwned"
    with host.env_file.open("a") as f:
        f.write(f"MAK4I_INSTANCE_NAME=$(touch {marker})\nMAK4I_ENVIRONMENT=`touch {marker}`\n")
    r = host.run("status")
    assert not marker.exists()
    assert f"$(touch {marker})" in r.output  # shown literally


# -- status / restart --------------------------------------------------------------


def test_status_reports_healthy(host):
    assert _install_private(host).returncode == 0
    r = host.run("status")
    assert r.returncode == 0, r.output
    for text in ("/health: ok", "/ready: ready", "at the latest migration", "1 organization(s)", "Healthy."):
        assert text in r.output


def test_admin_info_prints_the_non_secret_inventory(host):  # issue #21
    assert _install_private(host).returncode == 0
    r = host.run("admin-info")
    assert r.returncode == 0, r.output
    assert '"owners": ["prn_owner"]' in r.output
    assert ["exec", "-T", "mak4i", "mak4i", "admin", "inventory"] in host.compose_calls("exec")
    host.set_state(running=["postgres"], volumes=["mak4i_pgdata", "mak4i_artifacts"])
    assert host.run("admin-info").returncode == 5


def test_status_exits_5_when_mak4i_is_down(host):
    assert _install_private(host).returncode == 0
    host.set_state(running=["postgres"], volumes=["mak4i_pgdata", "mak4i_artifacts"])
    r = host.run("status")
    assert r.returncode == 5
    assert "restart" in r.output


def test_restart_verifies_data_is_preserved(host):
    assert _install_private(host).returncode == 0
    r = host.run("restart")
    assert r.returncode == 0, r.output
    assert "data preserved" in r.output


# -- backup / restore ----------------------------------------------------------------


def test_backup_refuses_the_repository_and_system_directories(host):
    assert _install_private(host).returncode == 0
    for bad in (str(host.root / "backups"), "/", "relative/dir"):
        r = host.run("backup", "--backup-dir", bad)
        assert r.returncode == 2, (bad, r.output)


def test_backup_is_private_verified_and_complete(host):
    assert _install_private(host).returncode == 0
    r = host.run("backup", "--backup-dir", str(host.backups))
    assert r.returncode == 0, r.output
    (dest,) = list(host.backups.glob("mak4i-backup-*"))
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700
    for name in ("control-plane.dump", "env", "manifest.env", "artifacts.sha256"):
        assert (dest / name).is_file()
        assert stat.S_IMODE((dest / name).stat().st_mode) & 0o077 == 0
    manifest = (dest / "manifest.env").read_text()
    assert "MAK4I_BACKUP_RELEASE=v0.1.0-rc.5" in manifest
    assert "MAK4I_BACKUP_ARTIFACT_COUNT=5" in manifest
    # mak4i was stopped for consistency and started again.
    assert host.compose_calls("stop") and host.compose_calls("start")
    assert "mak4i" in host.state()["running"]
    assert _password(host) not in r.output


def test_restore_requires_confirmation(host):
    assert _install_private(host).returncode == 0
    assert host.run("backup", "--backup-dir", str(host.backups)).returncode == 0
    (dest,) = list(host.backups.glob("mak4i-backup-*"))
    r = host.run("restore", "--from", str(dest), "--non-interactive")
    assert r.returncode == 6
    assert host.compose_calls("stop") == [["stop", "mak4i"]]  # only the backup's


def test_restore_rejects_a_tampered_backup(host):
    assert _install_private(host).returncode == 0
    assert host.run("backup", "--backup-dir", str(host.backups)).returncode == 0
    (dest,) = list(host.backups.glob("mak4i-backup-*"))
    (dest / "artifacts" / "org_x" / "prj_y" / "a0.json").write_text("tampered")
    r = host.run("restore", "--from", str(dest), "--yes")
    assert r.returncode == 8
    assert "checksum" in r.output


def _relabel_backup(dest, release):
    manifest = dest / "manifest.env"
    manifest.write_text(re.sub(r"(?m)^MAK4I_BACKUP_RELEASE=.*$", f"MAK4I_BACKUP_RELEASE={release}", manifest.read_text()))


def test_restore_accepts_a_backup_from_the_previous_release(host):
    # A backup taken just before upgrading (or by the previous release's
    # bootstrap) must stay restorable; migrations bring its schema up to date.
    assert _install_private(host).returncode == 0
    assert host.run("backup", "--backup-dir", str(host.backups)).returncode == 0
    (dest,) = list(host.backups.glob("mak4i-backup-*"))
    _relabel_backup(dest, "v0.1.0-rc.4")
    r = host.run("restore", "--from", str(dest), "--yes")
    assert r.returncode == 0, r.output
    assert "Restore complete and verified" in r.output


def test_restore_refuses_a_backup_from_two_releases_back(host):
    assert _install_private(host).returncode == 0
    assert host.run("backup", "--backup-dir", str(host.backups)).returncode == 0
    (dest,) = list(host.backups.glob("mak4i-backup-*"))
    _relabel_backup(dest, "v0.1.0-rc.3")
    r = host.run("restore", "--from", str(dest), "--yes")
    assert r.returncode == 7
    assert "backup was made by v0.1.0-rc.3" in r.output


def test_restore_with_yes_restores_and_verifies_counts(host):
    assert _install_private(host).returncode == 0
    assert host.run("backup", "--backup-dir", str(host.backups)).returncode == 0
    (dest,) = list(host.backups.glob("mak4i-backup-*"))
    r = host.run("restore", "--from", str(dest), "--yes")
    assert r.returncode == 0, r.output
    assert "Restore complete and verified" in r.output
    # Private backups (mode 700 directories) need DAC_READ_SEARCH for the
    # one-off root chown to descend into them (found on a real host).
    chowns = [c for c in host.compose_calls("run") if "chown" in c]
    assert chowns and "DAC_READ_SEARCH" in chowns[0]


# -- upgrade -------------------------------------------------------------------------


def test_upgrade_refuses_unsupported_versions(host):
    host.env_file.write_text("POSTGRES_PASSWORD=abc\n")
    # Two releases back is refused: RC3 must go through RC4 first.
    host.set_state(running=["postgres", "mak4i"], version="0.1.0rc3")
    r = host.run("upgrade", "--backup-dir", str(host.backups))
    assert r.returncode == 7
    assert "v0.1.0-rc.4 -> v0.1.0-rc.5" in r.output
    assert _mutations(host) == []


def test_upgrade_from_rc4_backs_up_first_and_adopts_the_installation(host):
    host.env_file.write_text("POSTGRES_PASSWORD=abc\nMAK4I_HTTP_BIND=127.0.0.1\n")
    host.env_file.chmod(0o600)
    host.set_state(running=["postgres", "mak4i"], volumes=["mak4i_pgdata", "mak4i_artifacts"], version="0.1.0rc4")

    # The running version flips to RC5 when the stack is recreated.
    import json as _json
    stub_state = Path(host.env["STUB_DIR"]) / "state.json"

    r = host.run("upgrade", "--backup-dir", str(host.backups), env={})
    # First run sees rc4 during the backup; the stub keeps reporting rc4
    # after `up`, so verification must catch the version mismatch.
    assert r.returncode == 1, r.output
    assert "expected v0.1.0-rc.5" in r.output
    assert list(host.backups.glob("mak4i-backup-*")), "backup must be taken before upgrading"

    state = _json.loads(stub_state.read_text())
    state["version"] = "0.1.0rc5"
    stub_state.write_text(_json.dumps(state))
    r = host.run("upgrade", "--backup-dir", str(host.backups))
    assert r.returncode == 0, r.output
    env = host.env_file.read_text()
    assert "MAK4I_INSTALLED_RELEASE=v0.1.0-rc.5" in env
    assert "MAK4I_INSTALL_STATE=complete" in env
    assert "Rollback to" in r.output


# -- uninstall -----------------------------------------------------------------------


def test_uninstall_defaults_to_preserving_data_and_reinstall_restores_it(host):
    assert _install_private(host).returncode == 0
    password = _password(host)
    r = host.run("uninstall")
    assert r.returncode == 0, r.output
    down = host.compose_calls("down")
    assert down and "--volumes" not in down[-1]
    assert host.state()["volumes"]  # data kept
    assert "MAK4I_INSTALL_STATE=preserved" in host.env_file.read_text()
    assert "Reinstall with the same data" in r.output

    r = _install_private(host)
    assert r.returncode == 0, r.output
    assert _password(host) == password  # same database credentials, same data


def test_destroy_data_needs_an_explicit_confirmation(host):
    assert _install_private(host).returncode == 0
    r = host.run("uninstall", "--destroy-data", "--non-interactive")
    assert r.returncode == 6
    assert "cannot be undone" in r.output
    assert all("--volumes" not in c for c in host.compose_calls("down"))
    assert host.env_file.exists()


def test_destroy_data_with_the_wrong_phrase_is_refused(host):
    assert _install_private(host).returncode == 0
    # stdin isn't a terminal in tests, so the prompt path refuses as well.
    r = host.run("uninstall", "--destroy-data", stdin="yes\n")
    assert r.returncode == 6
    assert host.state()["volumes"]


def test_destroy_data_removes_exactly_the_project_volumes_and_env(host):
    assert _install_private(host).returncode == 0
    r = host.run("uninstall", "--destroy-data", "--yes-destroy-data", "--non-interactive")
    assert r.returncode == 0, r.output
    assert host.compose_calls("down")[-1][-2:] == ["--volumes", "--remove-orphans"]
    assert host.state()["volumes"] == []
    assert not host.env_file.exists()
    assert "Backups (if any) were not touched" in r.output
    # Nothing else in the compose directory was removed.
    assert (host.compose_dir / "compose.yaml").exists()
    assert (host.compose_dir / ".env.example").exists()


def test_uninstall_with_nothing_installed_is_a_no_op(host):
    r = host.run("uninstall")
    assert r.returncode == 0
    assert "Nothing to uninstall" in r.output
    assert _mutations(host) == []


def test_logs_are_private_and_outside_the_repository(host):
    assert _install_private(host).returncode == 0
    logs = list((host.state_dir / "logs").glob("*.log"))
    assert logs
    for log in logs:
        assert stat.S_IMODE(log.stat().st_mode) == 0o600
        assert host.root not in log.parents


def test_a_refused_backup_location_is_not_created(host):
    assert _install_private(host).returncode == 0
    inside = host.root / "deploy" / "compose" / "backups"
    r = host.run("backup", "--backup-dir", str(inside))
    assert r.returncode == 2
    assert not inside.exists()
