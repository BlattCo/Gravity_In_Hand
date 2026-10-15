"""BlattCo Gravity In Hand — Dual-Mode GUI & Terminal Deck + Auto-Shutdown Heartbeat.

Provides the BlattCo Dark-Blueprint Web/Desktop GUI on top of an interactive
workspace terminal, Antigravity agent orchestration panel, live PROJECT_STATE.json
HUD, Shizuku (`rish`) & Local Wireless ADB Android Modding Deck, Samsung DeX mode
switcher, and the mandatory 60-second Auto-Shutdown Heartbeat Reaper (Directive 2).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request

from src.shizuku_adb_bridge import (
    S23UltraModdingBridge,
    SystemSettingTweak,
)
from src.usb_provisioner import (
    build_exfat_partition_plan,
    discover_usb_devices,
)

DEFAULT_HEARTBEAT_TIMEOUT_SECONDS: float = 60.0
DEFAULT_REAPER_INTERVAL_SECONDS: float = 5.0
MAX_TERMINAL_HISTORY: int = 100


@dataclass(frozen=True)
class TerminalHistoryEntry:
    """Record of a command executed in the Gravity Deck workspace terminal."""

    command: str
    returncode: int
    stdout: str
    stderr: str
    timestamp: str


class HeartbeatMonitor:
    """Enforces Directive 2: Background Daemon Auto-Shutdown Heartbeat Protocol."""

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        shutdown_callback: Callable[[int], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.timeout_seconds: float = timeout_seconds
        self._clock: Callable[[], float] = clock
        self._shutdown_callback: Callable[[int], None] = (
            shutdown_callback if shutdown_callback is not None else os._exit
        )
        self._lock = threading.Lock()
        self._last_ping_time: float = self._clock()
        self._reaper_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def last_ping_time(self) -> float:
        """Return the timestamp of the most recent frontend heartbeat ping."""
        with self._lock:
            return self._last_ping_time

    def record_ping(self) -> float:
        """Update the last received ping timestamp to the current clock time."""
        now = self._clock()
        with self._lock:
            self._last_ping_time = now
        return now

    def seconds_since_last_ping(self) -> float:
        """Return elapsed seconds since the last recorded frontend ping."""
        now = self._clock()
        with self._lock:
            return max(0.0, now - self._last_ping_time)

    def is_expired(self) -> bool:
        """Return True if elapsed time since the last ping exceeds timeout_seconds."""
        return self.seconds_since_last_ping() > self.timeout_seconds

    def check_and_reap_once(self) -> bool:
        """Check expiration once and invoke shutdown_callback(0) if expired."""
        if self.is_expired():
            print("[Daemon] Frontend disconnected. Auto-shutting down.")
            self._shutdown_callback(0)
            return True
        return False

    def start_background_reaper(
        self,
        poll_interval_seconds: float = DEFAULT_REAPER_INTERVAL_SECONDS,
    ) -> threading.Thread:
        """Start the daemon reaper thread that checks heartbeat expiration every 5s."""
        if self._reaper_thread is not None and self._reaper_thread.is_alive():
            return self._reaper_thread

        self._stop_event.clear()

        def _reaper_loop() -> None:
            while not self._stop_event.is_set():
                if self.check_and_reap_once():
                    break
                self._stop_event.wait(poll_interval_seconds)

        thread = threading.Thread(
            target=_reaper_loop,
            name="GravityDeckHeartbeatReaper",
            daemon=True,
        )
        thread.start()
        self._reaper_thread = thread
        return thread

    def stop_background_reaper(self) -> None:
        """Signal the background reaper thread to stop cleanly (used in test teardown)."""
        self._stop_event.set()


class TerminalSessionManager:
    """Executes shell commands inside the active portable workspace with bounded history."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir: Path = workspace_dir.resolve()
        self._history: list[TerminalHistoryEntry] = []
        self._lock = threading.Lock()

    @property
    def history(self) -> tuple[TerminalHistoryEntry, ...]:
        """Return a snapshot of executed terminal commands."""
        with self._lock:
            return tuple(self._history)

    def execute_command(
        self,
        command_str: str,
        timeout_seconds: float = 30.0,
    ) -> TerminalHistoryEntry:
        """Execute a command string inside `workspace_dir` and record its output."""
        cleaned = command_str.strip()
        if not cleaned:
            raise ValueError("Command string cannot be empty.")

        proc = subprocess.run(
            ["/bin/bash", "-c", cleaned],
            cwd=str(self.workspace_dir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        entry = TerminalHistoryEntry(
            command=cleaned,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timestamp=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._history.append(entry)
            if len(self._history) > MAX_TERMINAL_HISTORY:
                self._history = self._history[-MAX_TERMINAL_HISTORY:]
        return entry


BLATTCO_DECK_HTML: str = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GRAVITY IN HAND // BLATTCO ARM64 DECK</title>
  <style>
    :root {
      --bg-deep: #070b12;
      --bg-panel: #0d1421;
      --bg-elevated: #131d30;
      --border-blueprint: #1f3354;
      --accent-cyan: #00d2ff;
      --accent-amber: #ffb000;
      --accent-green: #00e676;
      --accent-red: #ff5252;
      --text-primary: #e6f0ff;
      --text-muted: #8aa0c0;
      --font-mono: "JetBrains Mono", "Fira Code", monospace;
      --font-sans: "Inter", system-ui, -apple-system, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-deep);
      background-image:
        linear-gradient(rgba(31, 51, 84, 0.22) 1px, transparent 1px),
        linear-gradient(90deg, rgba(31, 51, 84, 0.22) 1px, transparent 1px);
      background-size: 24px 24px;
      color: var(--text-primary);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: var(--bg-panel);
      border-bottom: 1px solid var(--border-blueprint);
      padding: 12px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }
    .brand-title {
      font-family: var(--font-mono);
      font-size: 1.05rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      color: var(--accent-cyan);
    }
    .status-strip {
      display: flex;
      gap: 14px;
      align-items: center;
      font-family: var(--font-mono);
      font-size: 0.82rem;
    }
    .badge {
      padding: 4px 10px;
      border: 1px solid var(--border-blueprint);
      background: var(--bg-elevated);
      border-radius: 3px;
    }
    .layout-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      padding: 16px;
      flex: 1;
    }
    body.mobile-mode .layout-grid {
      grid-template-columns: 1fr;
    }
    .panel {
      background: rgba(13, 20, 33, 0.94);
      border: 1px solid var(--border-blueprint);
      border-radius: 4px;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .panel-header {
      background: var(--bg-elevated);
      border-bottom: 1px solid var(--border-blueprint);
      padding: 10px 14px;
      font-family: var(--font-mono);
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--accent-cyan);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .panel-body {
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      flex: 1;
    }
    .terminal-output {
      background: #04070c;
      border: 1px solid var(--border-blueprint);
      color: var(--accent-green);
      font-family: var(--font-mono);
      font-size: 0.82rem;
      padding: 12px;
      height: 280px;
      overflow-y: auto;
      white-space: pre-wrap;
    }
    .input-row {
      display: flex;
      gap: 8px;
    }
    input[type="text"], select {
      flex: 1;
      background: #050911;
      border: 1px solid var(--border-blueprint);
      color: var(--text-primary);
      font-family: var(--font-mono);
      font-size: 0.85rem;
      padding: 8px 10px;
      border-radius: 3px;
    }
    button {
      background: var(--bg-elevated);
      border: 1px solid var(--accent-cyan);
      color: var(--accent-cyan);
      font-family: var(--font-mono);
      font-size: 0.8rem;
      font-weight: 600;
      padding: 8px 14px;
      border-radius: 3px;
      cursor: pointer;
    }
    button:hover {
      background: var(--accent-cyan);
      color: var(--bg-deep);
    }
    .btn-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 8px;
    }
    .task-list {
      font-family: var(--font-mono);
      font-size: 0.8rem;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .task-item {
      padding: 8px;
      border: 1px solid var(--border-blueprint);
      background: #060b14;
      display: flex;
      justify-content: space-between;
    }
  </style>
</head>
<body class="dex-mode">
  <header>
    <div class="brand-title">BLATTCO // GRAVITY IN HAND [S23 ULTRA ARM64]</div>
    <div class="status-strip">
      <span class="badge" id="bridge-badge">BRIDGE: CHECKING</span>
      <span class="badge" id="heartbeat-badge">HEARTBEAT: [OK]</span>
      <button type="button" id="mode-toggle-btn" onclick="toggleDisplayMode()">MODE: SAMSUNG DEX</button>
    </div>
  </header>

  <main class="layout-grid">
    <section class="panel">
      <div class="panel-header">
        <span>[01] LIVE WORKSPACE TERMINAL &amp; ANTIGRAVITY CLI</span>
        <span>/workspace</span>
      </div>
      <div class="panel-body">
        <div class="btn-grid">
          <button type="button" onclick="runQuickCmd('agy --version || echo [INFO] agy CLI ready')">AGY VERSION</button>
          <button type="button" onclick="runQuickCmd('android --help || echo [INFO] android CLI ready')">ANDROID CLI</button>
          <button type="button" onclick="runQuickCmd('apktool -version')">APKTOOL CHECK</button>
          <button type="button" onclick="runQuickCmd('git status -s')">GIT STATUS</button>
        </div>
        <div id="terminal-screen" class="terminal-output">[READY] Gravity In Hand ARM64 Terminal initialized.</div>
        <div class="input-row">
          <input type="text" id="cmd-input" placeholder="Enter bash / agy / apktool / rish command..." />
          <button type="button" onclick="submitTerminalCommand()">EXECUTE</button>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <span>[02] S23 ULTRA SHIZUKU (RISH) &amp; ADB SELF-MODDING DECK</span>
        <span>UID 2000</span>
      </div>
      <div class="panel-body">
        <div class="input-row">
          <input type="text" id="pkg-input" placeholder="com.samsung.android.bixby.agent" />
          <button type="button" onclick="togglePackage(false)">DISABLE PKG</button>
          <button type="button" onclick="togglePackage(true)">ENABLE PKG</button>
        </div>
        <div class="btn-grid">
          <button type="button" onclick="applyTweak('global', 'window_animation_scale', '0.5')">ANIM SCALE 0.5x</button>
          <button type="button" onclick="applyTweak('global', 'transition_animation_scale', '0.5')">TRANSITION 0.5x</button>
          <button type="button" onclick="applyTweak('system', 'peak_refresh_rate', '120.0')">PEAK 120HZ</button>
          <button type="button" onclick="fetchLogcat()">CAPTURE LOGCAT</button>
        </div>
        <div class="panel-header" style="margin-top:6px;">
          <span>[03] ARCHITECT PRIME // PROJECT_STATE.JSON HUD</span>
          <span id="phase-label">PHASE: BUILDING</span>
        </div>
        <div id="state-hud" class="task-list"></div>
      </div>
    </section>
  </main>

  <script>
    function toggleDisplayMode() {
      const body = document.body;
      const btn = document.getElementById('mode-toggle-btn');
      if (body.classList.contains('dex-mode')) {
        body.classList.remove('dex-mode');
        body.classList.add('mobile-mode');
        btn.textContent = 'MODE: S23 TOUCH DECK';
      } else {
        body.classList.remove('mobile-mode');
        body.classList.add('dex-mode');
        btn.textContent = 'MODE: SAMSUNG DEX';
      }
    }

    function appendOutput(text) {
      const screen = document.getElementById('terminal-screen');
      screen.textContent += "\\n" + text;
      screen.scrollTop = screen.scrollHeight;
    }

    async function pollHeartbeatAndState() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        document.getElementById('bridge-badge').textContent = 'BRIDGE: ' + data.transport.toUpperCase();
        document.getElementById('heartbeat-badge').textContent = 'HEARTBEAT: [OK]';
        document.getElementById('phase-label').textContent = 'PHASE: ' + data.phase;
        const hud = document.getElementById('state-hud');
        hud.innerHTML = '';
        (data.tasks || []).forEach(t => {
          const row = document.createElement('div');
          row.className = 'task-item';
          row.innerHTML = '<span>' + t.task_id + ': ' + t.name + '</span><span>[' + t.status + ']</span>';
          hud.appendChild(row);
        });
      } catch (err) {
        document.getElementById('heartbeat-badge').textContent = 'HEARTBEAT: [ERROR]';
      }
    }

    async function submitTerminalCommand() {
      const input = document.getElementById('cmd-input');
      const cmd = input.value.trim();
      if (!cmd) return;
      input.value = '';
      await runQuickCmd(cmd);
    }

    async function runQuickCmd(cmd) {
      appendOutput('$ ' + cmd);
      const res = await fetch('/api/terminal/exec', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmd })
      });
      const data = await res.json();
      if (data.stdout) appendOutput(data.stdout.trim());
      if (data.stderr) appendOutput('[STDERR] ' + data.stderr.trim());
    }

    async function togglePackage(enabled) {
      const pkg = document.getElementById('pkg-input').value.trim();
      if (!pkg) return;
      const res = await fetch('/api/modding/package-state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package_name: pkg, enabled: enabled })
      });
      const data = await res.json();
      appendOutput('[MODDING] ' + JSON.stringify(data));
    }

    async function applyTweak(namespace, key, value) {
      const res = await fetch('/api/modding/tweak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ namespace: namespace, key: key, value: value })
      });
      const data = await res.json();
      appendOutput('[TWEAK] ' + JSON.stringify(data));
    }

    async function fetchLogcat() {
      const res = await fetch('/api/modding/logcat?lines=40');
      const data = await res.json();
      appendOutput('[LOGCAT]\\n' + (data.logcat || ''));
    }

    setInterval(pollHeartbeatAndState, 5000);
    pollHeartbeatAndState();
  </script>
</body>
</html>
"""


def create_gravity_app(
    workspace_root: Path | None = None,
    bridge: S23UltraModdingBridge | None = None,
    heartbeat_monitor: HeartbeatMonitor | None = None,
    start_reaper: bool = False,
) -> Flask:
    """Create and configure the BlattCo Gravity In Hand Flask application."""
    resolved_workspace = (
        workspace_root.resolve()
        if workspace_root is not None
        else Path(__file__).resolve().parent.parent
    )
    active_bridge = bridge if bridge is not None else S23UltraModdingBridge()
    active_heartbeat = (
        heartbeat_monitor if heartbeat_monitor is not None else HeartbeatMonitor()
    )
    terminal_mgr = TerminalSessionManager(resolved_workspace)

    if start_reaper:
        active_heartbeat.start_background_reaper()

    app = Flask(__name__)
    app.config["WORKSPACE_ROOT"] = resolved_workspace
    app.config["MODDING_BRIDGE"] = active_bridge
    app.config["HEARTBEAT_MONITOR"] = active_heartbeat
    app.config["TERMINAL_MANAGER"] = terminal_mgr

    @app.before_request
    def _update_heartbeat_on_every_request() -> None:
        """Directive 2.1: Update LAST_PING_TIME on every incoming frontend request."""
        active_heartbeat.record_ping()

    def _load_project_state() -> dict[str, Any]:
        state_file = resolved_workspace / "PROJECT_STATE.json"
        if not state_file.is_file():
            return {
                "project_name": "Gravity_In_Hand",
                "phase": "UNKNOWN",
                "tasks": [],
            }
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"project_name": "Gravity_In_Hand", "phase": "UNKNOWN", "tasks": []}
        return data

    @app.get("/")
    def index() -> Response:
        return Response(BLATTCO_DECK_HTML, mimetype="text/html")

    @app.get("/api/status")
    def api_status() -> Response:
        state_data = _load_project_state()
        transport = active_bridge.detect_active_transport()
        return jsonify(
            {
                "status": "[OK]",
                "project_name": state_data.get("project_name", "Gravity_In_Hand"),
                "phase": state_data.get("phase", "BUILDING"),
                "transport": transport,
                "last_ping_time": active_heartbeat.last_ping_time,
                "timeout_seconds": active_heartbeat.timeout_seconds,
                "tasks": state_data.get("tasks", []),
            }
        )

    @app.get("/api/project-state")
    def api_project_state() -> Response:
        return jsonify(_load_project_state())

    @app.post("/api/terminal/exec")
    def api_terminal_exec() -> tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        cmd = str(payload.get("command") or "").strip()
        if not cmd:
            return jsonify({"error": "Missing 'command' field."}), 400
        try:
            entry = terminal_mgr.execute_command(cmd)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify(asdict(entry)), 200

    @app.get("/api/modding/packages")
    def api_modding_packages() -> tuple[Response, int]:
        third_party = request.args.get("third_party", "false").lower() == "true"
        try:
            pkgs = active_bridge.list_packages(third_party_only=third_party)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"packages": [asdict(p) for p in pkgs]}), 200

    @app.post("/api/modding/package-state")
    def api_modding_package_state() -> tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        pkg = str(payload.get("package_name") or "").strip()
        enabled = bool(payload.get("enabled", True))
        try:
            res = active_bridge.set_package_state(pkg, enabled=enabled)
        except PermissionError as exc:
            return jsonify({"status": "[ERROR]", "error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"status": "[ERROR]", "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"status": "[ERROR]", "error": str(exc)}), 500
        return (
            jsonify(
                {
                    "status": "[OK]",
                    "package_name": pkg,
                    "enabled": enabled,
                    "stdout": res.stdout.strip(),
                }
            ),
            200,
        )

    @app.post("/api/modding/tweak")
    def api_modding_tweak() -> tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        tweak = SystemSettingTweak(
            namespace=str(payload.get("namespace") or ""),
            key=str(payload.get("key") or ""),
            value=str(payload.get("value") or ""),
        )
        try:
            res = active_bridge.apply_system_tweak(tweak)
        except ValueError as exc:
            return jsonify({"status": "[ERROR]", "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"status": "[ERROR]", "error": str(exc)}), 500
        return (
            jsonify(
                {
                    "status": "[OK]",
                    "namespace": tweak.namespace,
                    "key": tweak.key,
                    "value": tweak.value,
                    "transport": res.transport,
                }
            ),
            200,
        )

    @app.get("/api/modding/logcat")
    def api_modding_logcat() -> tuple[Response, int]:
        lines = int(request.args.get("lines", "100"))
        try:
            log_text = active_bridge.capture_logcat_snapshot(lines=lines)
        except Exception as exc:
            return jsonify({"status": "[ERROR]", "error": str(exc)}), 400
        return jsonify({"status": "[OK]", "logcat": log_text}), 200

    @app.post("/api/usb/inspect")
    def api_usb_inspect() -> tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        target_gb = int(payload.get("target_size_gb", 50))
        try:
            devices = discover_usb_devices()
            plans = [
                asdict(build_exfat_partition_plan(dev, target_size_gb=target_gb))
                for dev in devices
            ]
        except Exception as exc:
            return jsonify({"status": "[ERROR]", "error": str(exc)}), 400
        return (
            jsonify(
                {
                    "status": "[OK]",
                    "devices": [asdict(d) for d in devices],
                    "plans": plans,
                }
            ),
            200,
        )

    return app


def main() -> None:
    """Entrypoint for the BlattCo Gravity In Hand GUI daemon."""
    host = os.environ.get("GRAVITY_HOST", "127.0.0.1")
    port = int(os.environ.get("GRAVITY_PORT", "8765"))
    app = create_gravity_app(start_reaper=True)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
