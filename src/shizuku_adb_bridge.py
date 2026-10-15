"""Shizuku (rish) and Local Wireless ADB Self-Modding Bridge for Samsung Galaxy S23 Ultra.

Provides structured, safety-validated execution over Shizuku (`rish`) and
localhost Wireless ADB (`127.0.0.1:<port>`, UID 2000 shell privileges) on an
unrooted Samsung Galaxy S23 Ultra:
- Transport detection (`rish` vs `adb` vs `offline`)
- Package listing and split APK path resolution (`pm list packages -f`, `pm path`)
- APK extraction (`apks/in/`), decompilation (`apktool` / `jadx` -> `apks/decompiled/`),
  rebuilding (`apks/patched/`), and V2/V3/V4 signing (`uber-apk-signer` -> `apks/signed/`)
- Signed APK installation and version downgrade (`pm install -r -d -g`)
- Carrier and One UI bloatware disabling (`pm disable-user --user 0`) with a strict
  `CRITICAL_BOOT_PACKAGES` blocklist to prevent soft-bricking
- One UI system settings tweaking (`settings put system|secure|global`)
- UI hierarchy XML dump (`uiautomator dump`) and `logcat` snapshot capture
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PACKAGE_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$"
)
SETTING_KEY_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_.\-]+$")
VALID_SETTING_NAMESPACES: frozenset[str] = frozenset({"system", "secure", "global"})

CRITICAL_BOOT_PACKAGES: frozenset[str] = frozenset(
    {
        "android",
        "com.android.systemui",
        "com.android.settings",
        "com.android.phone",
        "com.android.providers.settings",
        "com.android.providers.telephony",
        "com.samsung.android.incallui",
        "com.sec.imsservice",
        "moe.shizuku.privileged.api",
        "com.termux",
    }
)


@dataclass(frozen=True)
class CommandResult:
    """Result of executing a shell or host command through the modding bridge."""

    transport: str
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        """Return True if the command exited with code 0."""
        return self.returncode == 0


@dataclass(frozen=True)
class PackageRecord:
    """Metadata for an installed Android package on the S23 Ultra."""

    package_name: str
    apk_paths: tuple[str, ...]
    is_system: bool
    enabled: bool = True


@dataclass(frozen=True)
class ModdingPipelineArtifact:
    """Paths produced during a complete APK decompile -> rebuild -> sign cycle."""

    package_name: str
    decompiled_dir: str
    unsigned_patched_apk: str
    signed_apk: str
    signer_stdout: str


@dataclass(frozen=True)
class SystemSettingTweak:
    """Specification for an Android `settings put` modification."""

    namespace: str
    key: str
    value: str


RunnerCallable = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_subprocess_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute a subprocess command and return CompletedProcess[str]."""
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )


def validate_package_name(package_name: str) -> str:
    """Validate an Android package identifier against command-injection characters."""
    cleaned = package_name.strip()
    if not PACKAGE_NAME_PATTERN.match(cleaned):
        raise ValueError(f"Invalid Android package identifier: '{package_name}'")
    return cleaned


def validate_setting_tweak(tweak: SystemSettingTweak) -> SystemSettingTweak:
    """Validate namespace, key, and value for an Android system setting tweak."""
    ns = tweak.namespace.strip().lower()
    if ns not in VALID_SETTING_NAMESPACES:
        raise ValueError(
            f"Invalid settings namespace '{tweak.namespace}'. "
            f"Allowed: {sorted(VALID_SETTING_NAMESPACES)}"
        )
    key = tweak.key.strip()
    if not SETTING_KEY_PATTERN.match(key):
        raise ValueError(f"Invalid settings key identifier: '{tweak.key}'")
    val = tweak.value.strip()
    if not val or any(ch in val for ch in (";", "&", "|", "`", "$", "\n", "\r")):
        raise ValueError(f"Unsafe or empty settings value: '{tweak.value}'")
    return SystemSettingTweak(namespace=ns, key=key, value=val)


class S23UltraModdingBridge:
    """Privileged UID 2000 modding controller over Shizuku (rish) or local Wireless ADB."""

    def __init__(
        self,
        runner: RunnerCallable | None = None,
        preferred_transport: str = "rish",
        adb_serial: str = "127.0.0.1:5555",
    ) -> None:
        self._runner: RunnerCallable = (
            runner if runner is not None else _default_subprocess_runner
        )
        self.preferred_transport: str = preferred_transport
        self.adb_serial: str = adb_serial

    def detect_active_transport(self) -> str:
        """Probe whether `rish` (Shizuku) or local `adb` is currently responsive."""
        probes = (
            [self.preferred_transport, "adb"]
            if self.preferred_transport == "rish"
            else ["adb", "rish"]
        )
        for candidate in probes:
            if candidate == "rish":
                res = self._runner(["rish", "-c", "id -u"])
                if res.returncode == 0 and res.stdout.strip() in ("2000", "0"):
                    return "rish"
            elif candidate == "adb":
                res = self._runner(
                    ["adb", "-s", self.adb_serial, "shell", "id", "-u"]
                )
                if res.returncode == 0 and res.stdout.strip() in ("2000", "0"):
                    return "adb"
        return "offline"

    def run_device_shell(self, shell_command: str) -> CommandResult:
        """Execute a privileged shell command on the S23 Ultra via rish or adb."""
        transport = self.detect_active_transport()
        if transport == "offline":
            transport = self.preferred_transport

        if transport == "rish":
            cmd = ["rish", "-c", shell_command]
        else:
            cmd = ["adb", "-s", self.adb_serial, "shell", shell_command]

        proc = self._runner(cmd)
        return CommandResult(
            transport=transport,
            command=tuple(cmd),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def run_host_tool(self, cmd: list[str]) -> CommandResult:
        """Execute an ARM64 Linux host tool (apktool, jadx, uber-apk-signer) in Ubuntu proot."""
        proc = self._runner(cmd)
        return CommandResult(
            transport="host",
            command=tuple(cmd),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def list_packages(
        self,
        include_disabled: bool = False,
        third_party_only: bool = False,
    ) -> list[PackageRecord]:
        """List installed packages on the S23 Ultra using `pm list packages -f`."""
        flags = ["-f"]
        if not include_disabled:
            flags.append("-e")
        if third_party_only:
            flags.append("-3")

        shell_cmd = f"pm list packages {' '.join(flags)}"
        res = self.run_device_shell(shell_cmd)
        if not res.succeeded:
            raise RuntimeError(
                f"Failed to list packages ({res.transport}): {res.stderr.strip()}"
            )

        records: list[PackageRecord] = []
        for raw_line in res.stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("package:"):
                continue
            body = line[len("package:") :]
            if "=" not in body:
                continue
            apk_path, pkg_name = body.rsplit("=", 1)
            pkg_name = pkg_name.strip()
            apk_path = apk_path.strip()
            is_system = apk_path.startswith(
                ("/system/", "/product/", "/vendor/", "/system_ext/")
            )
            records.append(
                PackageRecord(
                    package_name=pkg_name,
                    apk_paths=(apk_path,),
                    is_system=is_system,
                    enabled=True,
                )
            )
        return sorted(records, key=lambda r: r.package_name)

    def get_package_apk_paths(self, package_name: str) -> list[str]:
        """Resolve all base and split APK paths on the device via `pm path <pkg>`."""
        pkg = validate_package_name(package_name)
        res = self.run_device_shell(f"pm path {pkg}")
        if not res.succeeded:
            raise RuntimeError(
                f"Failed to resolve APK paths for {pkg}: {res.stderr.strip()}"
            )

        paths: list[str] = []
        for raw_line in res.stdout.splitlines():
            line = raw_line.strip()
            if line.startswith("package:"):
                apk_path = line[len("package:") :].strip()
                if apk_path:
                    paths.append(apk_path)
        if not paths:
            raise FileNotFoundError(f"No APK paths returned by pm path for '{pkg}'.")
        return paths

    def pull_package_apks(
        self,
        package_name: str,
        workspace_root: Path,
    ) -> list[Path]:
        """Pull base and split APKs for `package_name` into `AGY_Workspace/apks/in/<pkg>/`."""
        pkg = validate_package_name(package_name)
        remote_paths = self.get_package_apk_paths(pkg)
        dest_dir = workspace_root.resolve() / "apks" / "in" / pkg
        dest_dir.mkdir(parents=True, exist_ok=True)

        pulled_files: list[Path] = []
        for idx, remote_path in enumerate(remote_paths):
            basename = Path(remote_path).name or f"split_{idx}.apk"
            local_target = dest_dir / basename
            copy_cmd = f"cp '{remote_path}' '{local_target}'"
            res = self.run_device_shell(copy_cmd)
            if not res.succeeded:
                raise RuntimeError(
                    f"Failed to pull '{remote_path}' to '{local_target}': {res.stderr.strip()}"
                )
            pulled_files.append(local_target)

        return pulled_files

    def decompile_apk(
        self,
        apk_path: Path,
        workspace_root: Path,
        use_jadx: bool = False,
    ) -> Path:
        """Decompile an APK into `AGY_Workspace/apks/decompiled/<stem>` via apktool or jadx."""
        resolved_apk = apk_path.resolve()
        if not resolved_apk.is_file():
            raise FileNotFoundError(f"APK file not found: {resolved_apk}")

        suffix = "jadx" if use_jadx else "apktool"
        out_dir = (
            workspace_root.resolve()
            / "apks"
            / "decompiled"
            / f"{resolved_apk.stem}_{suffix}"
        )
        out_dir.parent.mkdir(parents=True, exist_ok=True)

        if use_jadx:
            cmd = ["jadx", "-d", str(out_dir), str(resolved_apk)]
        else:
            cmd = ["apktool", "d", "-f", "-o", str(out_dir), str(resolved_apk)]

        res = self.run_host_tool(cmd)
        if not res.succeeded:
            raise RuntimeError(
                f"APK decompilation failed ({' '.join(cmd)}): {res.stderr.strip()}"
            )
        return out_dir

    def build_and_sign_apk(
        self,
        decompiled_dir: Path,
        workspace_root: Path,
        package_name: str = "custom.modded.app",
    ) -> ModdingPipelineArtifact:
        """Rebuild a decompiled APK directory with `apktool b` and V2/V3/V4 sign it."""
        pkg = validate_package_name(package_name)
        resolved_decompiled = decompiled_dir.resolve()
        if not resolved_decompiled.is_dir():
            raise FileNotFoundError(
                f"Decompiled directory not found: {resolved_decompiled}"
            )

        patched_dir = workspace_root.resolve() / "apks" / "patched"
        signed_dir = workspace_root.resolve() / "apks" / "signed"
        patched_dir.mkdir(parents=True, exist_ok=True)
        signed_dir.mkdir(parents=True, exist_ok=True)

        unsigned_apk = patched_dir / f"{pkg}-unsigned.apk"
        build_cmd = [
            "apktool",
            "b",
            str(resolved_decompiled),
            "-o",
            str(unsigned_apk),
        ]
        build_res = self.run_host_tool(build_cmd)
        if not build_res.succeeded:
            raise RuntimeError(
                f"apktool rebuild failed for {resolved_decompiled}: {build_res.stderr.strip()}"
            )

        sign_cmd = [
            "uber-apk-signer",
            "--apks",
            str(unsigned_apk),
            "--out",
            str(signed_dir),
        ]
        sign_res = self.run_host_tool(sign_cmd)
        if not sign_res.succeeded:
            raise RuntimeError(
                f"uber-apk-signer failed for {unsigned_apk}: {sign_res.stderr.strip()}"
            )

        signed_apk = signed_dir / f"{pkg}-aligned-debugSigned.apk"
        return ModdingPipelineArtifact(
            package_name=pkg,
            decompiled_dir=str(resolved_decompiled),
            unsigned_patched_apk=str(unsigned_apk),
            signed_apk=str(signed_apk),
            signer_stdout=sign_res.stdout.strip(),
        )

    def install_signed_apk(
        self,
        signed_apk_path: Path,
        allow_downgrade: bool = True,
        grant_permissions: bool = True,
    ) -> CommandResult:
        """Install a signed APK onto the S23 Ultra using `pm install`."""
        resolved_apk = signed_apk_path.resolve()
        if not resolved_apk.is_file():
            raise FileNotFoundError(f"Signed APK not found: {resolved_apk}")

        flags = ["-r"]
        if allow_downgrade:
            flags.append("-d")
        if grant_permissions:
            flags.append("-g")

        shell_cmd = f"pm install {' '.join(flags)} '{resolved_apk}'"
        res = self.run_device_shell(shell_cmd)
        if not res.succeeded or "Success" not in res.stdout:
            raise RuntimeError(
                f"pm install failed for {resolved_apk}: {res.stdout.strip()} {res.stderr.strip()}"
            )
        return res

    def set_package_state(
        self,
        package_name: str,
        enabled: bool,
    ) -> CommandResult:
        """Enable or disable a package for `--user 0` while protecting critical boot packages."""
        pkg = validate_package_name(package_name)
        if not enabled and pkg in CRITICAL_BOOT_PACKAGES:
            raise PermissionError(
                f"Refusing to disable critical boot/system package '{pkg}'."
            )

        if enabled:
            shell_cmd = f"pm enable {pkg}"
        else:
            shell_cmd = f"pm disable-user --user 0 {pkg}"

        res = self.run_device_shell(shell_cmd)
        if not res.succeeded:
            raise RuntimeError(
                f"Failed to change state for package '{pkg}': {res.stderr.strip()}"
            )
        return res

    def apply_system_tweak(self, tweak: SystemSettingTweak) -> CommandResult:
        """Apply an Android `settings put <namespace> <key> <value>` tweak."""
        validated = validate_setting_tweak(tweak)
        shell_cmd = (
            f"settings put {validated.namespace} {validated.key} {validated.value}"
        )
        res = self.run_device_shell(shell_cmd)
        if not res.succeeded:
            raise RuntimeError(
                f"Failed to apply setting tweak ({shell_cmd}): {res.stderr.strip()}"
            )
        return res

    def dump_ui_hierarchy(self, workspace_root: Path) -> Path:
        """Capture the current S23 Ultra UI XML hierarchy into `AGY_Workspace/logs/ui_dump.xml`."""
        logs_dir = workspace_root.resolve() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        local_xml = logs_dir / "ui_dump.xml"
        remote_tmp = "/data/local/tmp/agy_ui_dump.xml"

        dump_res = self.run_device_shell(
            f"uiautomator dump {remote_tmp} && cp {remote_tmp} '{local_xml}'"
        )
        if not dump_res.succeeded:
            raise RuntimeError(
                f"uiautomator dump failed: {dump_res.stderr.strip()}"
            )
        return local_xml

    def capture_logcat_snapshot(
        self,
        lines: int = 200,
        filter_spec: str = "*:I",
    ) -> str:
        """Capture a non-blocking logcat snapshot (`logcat -d -t <lines>`)."""
        if lines <= 0 or lines > 10000:
            raise ValueError("lines must be between 1 and 10000.")
        if any(ch in filter_spec for ch in (";", "&", "|", "`", "$", "\n", "\r")):
            raise ValueError(f"Unsafe logcat filter_spec: '{filter_spec}'")

        res = self.run_device_shell(f"logcat -d -t {lines} {filter_spec}")
        if not res.succeeded:
            raise RuntimeError(f"logcat capture failed: {res.stderr.strip()}")
        return res.stdout
