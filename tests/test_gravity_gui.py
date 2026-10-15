"""Unit and integration tests for src/gravity_gui.py and run.sh."""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.arm64_bootstrap import validate_bash_syntax
from src.gravity_gui import (
    HeartbeatMonitor,
    create_gravity_app,
)
from src.shizuku_adb_bridge import S23UltraModdingBridge


def _make_gui_bridge() -> S23UltraModdingBridge:
    """Create a deterministic S23UltraModdingBridge for GUI endpoint testing."""

    def _runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        joined = " ".join(cmd)
        if "rish -c id -u" in joined:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="2000\n", stderr=""
            )
        if "pm list packages" in joined:
            out = "package:/data/app/com.example.app/base.apk=com.example.app\n"
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=out, stderr=""
            )
        if "logcat -d" in joined:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="I/GravityInHand: Ready\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="Success\n", stderr=""
        )

    return S23UltraModdingBridge(runner=_runner)


def test_heartbeat_monitor_expiration_and_reaper_callback() -> None:
    """Verify HeartbeatMonitor tracks pings and invokes shutdown callback when > 60s."""
    fake_now = [1000.0]
    reaped_codes: list[int] = []

    monitor = HeartbeatMonitor(
        timeout_seconds=60.0,
        shutdown_callback=lambda code: reaped_codes.append(code),
        clock=lambda: fake_now[0],
    )
    assert monitor.is_expired() is False
    assert monitor.check_and_reap_once() is False

    # Advance 30 seconds -> still alive
    fake_now[0] = 1030.0
    assert monitor.is_expired() is False

    # Record a ping at t=1030.0
    monitor.record_ping()
    fake_now[0] = 1085.0
    assert monitor.is_expired() is False

    # Advance past 60s threshold (1030 + 61 = 1091)
    fake_now[0] = 1091.0
    assert monitor.is_expired() is True
    assert monitor.check_and_reap_once() is True
    assert reaped_codes == [0]


def test_gui_index_status_and_project_state_endpoints() -> None:
    """Verify GET /, GET /api/status, and GET /api/project-state return valid data."""
    repo_root = Path(__file__).resolve().parent.parent
    bridge = _make_gui_bridge()
    monitor = HeartbeatMonitor(timeout_seconds=60.0)
    app = create_gravity_app(
        workspace_root=repo_root,
        bridge=bridge,
        heartbeat_monitor=monitor,
        start_reaper=False,
    )
    client = app.test_client()

    index_res = client.get("/")
    assert index_res.status_code == 200
    html = index_res.get_data(as_text=True)
    assert "BLATTCO // GRAVITY IN HAND" in html
    assert "pollHeartbeatAndState" in html

    status_res = client.get("/api/status")
    assert status_res.status_code == 200
    status_json = status_res.get_json()
    assert status_json["status"] == "[OK]"
    assert status_json["transport"] == "rish"
    assert status_json["project_name"] == "Gravity_In_Hand"

    state_res = client.get("/api/project-state")
    assert state_res.status_code == 200
    state_json = state_res.get_json()
    assert state_json["architecture_target"] == "Antigravity-Universal-Agent-Runtime"


def test_gui_terminal_and_modding_endpoints(tmp_path: Path) -> None:
    """Verify terminal execution, package state toggling, critical package block, and logcat."""
    bridge = _make_gui_bridge()
    app = create_gravity_app(
        workspace_root=tmp_path,
        bridge=bridge,
        start_reaper=False,
    )
    client = app.test_client()

    term_res = client.post(
        "/api/terminal/exec", json={"command": "echo 'GRAVITY_DECK_OK'"}
    )
    assert term_res.status_code == 200
    term_data = term_res.get_json()
    assert term_data["returncode"] == 0
    assert "GRAVITY_DECK_OK" in term_data["stdout"]

    pkgs_res = client.get("/api/modding/packages")
    assert pkgs_res.status_code == 200
    assert len(pkgs_res.get_json()["packages"]) == 1

    # Disabling a critical boot package must return HTTP 403
    forbidden_res = client.post(
        "/api/modding/package-state",
        json={"package_name": "com.android.systemui", "enabled": False},
    )
    assert forbidden_res.status_code == 403

    allowed_res = client.post(
        "/api/modding/package-state",
        json={"package_name": "com.samsung.android.bixby.agent", "enabled": False},
    )
    assert allowed_res.status_code == 200
    assert allowed_res.get_json()["status"] == "[OK]"

    tweak_res = client.post(
        "/api/modding/tweak",
        json={
            "namespace": "global",
            "key": "window_animation_scale",
            "value": "0.5",
        },
    )
    assert tweak_res.status_code == 200
    assert tweak_res.get_json()["status"] == "[OK]"

    log_res = client.get("/api/modding/logcat?lines=25")
    assert log_res.status_code == 200
    assert "GravityInHand: Ready" in log_res.get_json()["logcat"]


def test_run_sh_follows_process_naming_protocol() -> None:
    """Verify run.sh passes `bash -n` and implements Directive 1 GRAV_DECK_${TIMESTAMP}.py."""
    repo_root = Path(__file__).resolve().parent.parent
    run_sh = repo_root / "run.sh"
    assert run_sh.is_file()
    assert validate_bash_syntax(run_sh) is True

    content = run_sh.read_text(encoding="utf-8")
    assert 'PROC_NAME="GRAV_DECK_${TIMESTAMP}.py"' in content
    assert 'ln -sf "${SCRIPT_DIR}/src/gravity_gui.py" "/tmp/${PROC_NAME}"' in content
    assert 'python3 "/tmp/${PROC_NAME}" &' in content
    assert "/home/blatt" not in content
