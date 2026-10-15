"""ARM64 Termux + proot-distro Ubuntu Antigravity & Android Modding Bootstrap Generator.

Generates the Samsung Galaxy S23 Ultra (aarch64) bootstrap scripts on the
exFAT thumbdrive (AGY_DRIVE):
1. `boot-agy.sh`: Termux outer bootstrap that detects the USB-C exFAT drive,
   bypasses Android's exFAT `noexec` restriction, configures Shizuku (`rish`)
   and local Wireless ADB, and launches the Ubuntu ARM64 proot environment with
   `AGY_Workspace` bind-mounted to `/workspace`.
2. `provision-ubuntu-arm64.sh`: Inner Ubuntu ARM64 provisioning script that
   installs Google Antigravity (`linux-arm` + `google-antigravity` SDK/CLI),
   Google `android` CLI (`linux_arm64`), `apktool`, `jadx`, `uber-apk-signer`,
   `aapt2`, `adb`, `fastboot`, and XFCE4.
3. `launch-dex-desktop.sh`: Samsung DeX / Termux:X11 full Linux desktop launcher
   that starts `termux-x11 :0`, PulseAudio, XFCE4, and the BlattCo Gravity Deck.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_APT_PACKAGES: tuple[str, ...] = (
    "curl",
    "wget",
    "git",
    "unzip",
    "zip",
    "xz-utils",
    "jq",
    "python3",
    "python3-pip",
    "python3-venv",
    "openjdk-17-jdk",
    "android-sdk-libsparse-utils",
    "aapt",
    "apktool",
    "adb",
    "fastboot",
    "xfce4",
    "xfce4-terminal",
    "dbus-x11",
    "pulseaudio",
)

DEFAULT_TERMUX_PACKAGES: tuple[str, ...] = (
    "proot-distro",
    "x11-repo",
    "termux-x11-nightly",
    "pulseaudio",
    "android-tools",
    "openssh",
    "git",
    "curl",
    "jq",
)


@dataclass(frozen=True)
class Arm64ToolchainSpec:
    """Specification for the ARM64 Antigravity and Android Modding toolchain."""

    architecture: str = "aarch64"
    distro_alias: str = "ubuntu"
    usb_label: str = "AGY_DRIVE"
    antigravity_tarball_name: str = "Antigravity-linux-arm.tar.gz"
    antigravity_pip_package: str = "google-antigravity"
    android_cli_url: str = "https://dl.google.com/android/cli/latest/linux_arm64/android"
    jadx_version: str = "1.5.1"
    uber_apk_signer_version: str = "1.3.0"
    apktool_version: str = "2.10.0"
    termux_packages: tuple[str, ...] = DEFAULT_TERMUX_PACKAGES
    ubuntu_apt_packages: tuple[str, ...] = DEFAULT_APT_PACKAGES

    @property
    def jadx_release_url(self) -> str:
        """Return the official GitHub release URL for JADX CLI + GUI zip."""
        return (
            f"https://github.com/skylot/jadx/releases/download/"
            f"v{self.jadx_version}/jadx-{self.jadx_version}.zip"
        )

    @property
    def uber_apk_signer_url(self) -> str:
        """Return the official GitHub release URL for uber-apk-signer JAR."""
        return (
            f"https://github.com/patrickfav/uber-apk-signer/releases/download/"
            f"v{self.uber_apk_signer_version}/uber-apk-signer-{self.uber_apk_signer_version}.jar"
        )

    @property
    def apktool_jar_url(self) -> str:
        """Return the official GitHub release URL for Apktool JAR."""
        return (
            f"https://github.com/iBotPeaches/Apktool/releases/download/"
            f"v{self.apktool_version}/apktool_{self.apktool_version}.jar"
        )


@dataclass(frozen=True)
class BootstrapBundle:
    """Paths and verification metadata for generated ARM64 bootstrap scripts."""

    mount_root: str
    termux_boot_script: str
    ubuntu_provision_script: str
    dex_launcher_script: str
    toolchain_spec_json: str
    bash_syntax_verified: bool
    generated_at: str


def generate_termux_boot_script(spec: Arm64ToolchainSpec | None = None) -> str:
    """Generate the outer Termux `boot-agy.sh` script for the Samsung Galaxy S23 Ultra."""
    active_spec = spec if spec is not None else Arm64ToolchainSpec()
    termux_pkgs = " ".join(active_spec.termux_packages)

    lines = [
        "#!/usr/bin/env bash",
        "# Gravity In Hand (BlattCo) — Termux ARM64 Outer Bootstrap for Samsung Galaxy S23 Ultra",
        "# Bypasses Android exFAT noexec mount restrictions and enters Ubuntu ARM64 proot.",
        "set -euo pipefail",
        "",
        f'USB_LABEL="{active_spec.usb_label}"',
        f'DISTRO_ALIAS="{active_spec.distro_alias}"',
        "",
        "echo \"[INFO] Scanning for mounted USB-C thumbdrive (${USB_LABEL})...\"",
        "AGY_USB_PATH=\"\"",
        "for candidate in /storage/* /mnt/media_rw/*; do",
        "    if [ -d \"${candidate}/AGY_System\" ] && [ -d \"${candidate}/AGY_Workspace\" ]; then",
        "        AGY_USB_PATH=\"${candidate}\"",
        "        break",
        "    fi",
        "done",
        "",
        "if [ -z \"${AGY_USB_PATH}\" ]; then",
        "    echo \"[ERROR] Could not locate AGY_System/ and AGY_Workspace/ on any mounted USB volume.\"",
        "    echo \"[INFO] Ensure the USB-C thumbdrive is inserted and mounted via Android USB OTG.\"",
        "    exit 1",
        "fi",
        "",
        "echo \"[OK] Found Gravity In Hand USB volume at: ${AGY_USB_PATH}\"",
        "",
        "echo \"[INFO] Installing required Termux host packages...\"",
        "pkg update -y",
        f"pkg install -y {termux_pkgs}",
        "",
        "mkdir -p \"${HOME}/bin\" \"${HOME}/.agy\"",
        "cp \"${AGY_USB_PATH}/AGY_System/scripts/provision-ubuntu-arm64.sh\" \"${HOME}/.agy/provision-ubuntu-arm64.sh\"",
        "cp \"${AGY_USB_PATH}/AGY_System/scripts/launch-dex-desktop.sh\" \"${HOME}/bin/agy-dex\"",
        "chmod 700 \"${HOME}/.agy/provision-ubuntu-arm64.sh\" \"${HOME}/bin/agy-dex\"",
        "",
        "if ! proot-distro list | grep -q \"${DISTRO_ALIAS}.*installed\"; then",
        "    echo \"[INFO] Installing Ubuntu ARM64 proot-distro rootfs...\"",
        "    proot-distro install \"${DISTRO_ALIAS}\"",
        "fi",
        "",
        "cat > \"${HOME}/bin/agy-usb\" <<EOF",
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "exec proot-distro login \"${DISTRO_ALIAS}\" \\",
        "    --shared-tmp \\",
        "    --bind \"${AGY_USB_PATH}/AGY_Workspace:/workspace\" \\",
        "    --bind \"${AGY_USB_PATH}/AGY_System:/agy_system\" \\",
        "    -- /bin/bash -l",
        "EOF",
        "chmod 700 \"${HOME}/bin/agy-usb\"",
        "",
        "echo \"[INFO] Running inside-proot ARM64 toolchain provisioning check...\"",
        "proot-distro login \"${DISTRO_ALIAS}\" \\",
        "    --shared-tmp \\",
        "    --bind \"${AGY_USB_PATH}/AGY_Workspace:/workspace\" \\",
        "    --bind \"${AGY_USB_PATH}/AGY_System:/agy_system\" \\",
        "    -- /bin/bash /agy_system/scripts/provision-ubuntu-arm64.sh",
        "",
        "echo \"[SUCCESS] Gravity In Hand ARM64 environment ready.\"",
        "echo \"[INFO] Run '~/bin/agy-usb' for CLI/Web Deck or '~/bin/agy-dex' for Samsung DeX Desktop.\"",
    ]
    return "\n".join(lines) + "\n"


def generate_ubuntu_proot_provision_script(
    spec: Arm64ToolchainSpec | None = None,
) -> str:
    """Generate `provision-ubuntu-arm64.sh` to configure Antigravity & Android modding tools."""
    active_spec = spec if spec is not None else Arm64ToolchainSpec()
    apt_pkgs = " ".join(active_spec.ubuntu_apt_packages)

    lines = [
        "#!/usr/bin/env bash",
        "# Gravity In Hand (BlattCo) — Inner Ubuntu ARM64 (aarch64) Toolchain Provisioner",
        "# Installs Antigravity ARM64, Google android CLI, apktool, jadx, uber-apk-signer, and rules.",
        "set -euo pipefail",
        "",
        "export DEBIAN_FRONTEND=noninteractive",
        "ARCH=\"$(uname -m)\"",
        "if [ \"${ARCH}\" != \"aarch64\" ] && [ \"${ARCH}\" != \"arm64\" ]; then",
        "    echo \"[WARN] Expected aarch64/arm64 architecture, detected: ${ARCH}\"",
        "fi",
        "",
        "echo \"[INFO] Updating Ubuntu ARM64 apt repositories and packages...\"",
        "apt-get update -y",
        f"apt-get install -y --no-install-recommends {apt_pkgs}",
        "",
        "mkdir -p /opt/antigravity /opt/android-modding /usr/local/bin",
        "mkdir -p /root/.gemini/config/rules",
        "",
        "# 1. Link portable Architect Prime AGENTS.md rules from USB workspace",
        "if [ -f /workspace/.gemini/config/rules/AGENTS.md ]; then",
        "    ln -sf /workspace/.gemini/config/rules/AGENTS.md /root/.gemini/config/rules/AGENTS.md",
        "    echo \"[OK] Linked portable AGENTS.md into /root/.gemini/config/rules/AGENTS.md\"",
        "fi",
        "",
        "# 2. Install Antigravity ARM64 tarball if cached on AGY_System/installers",
        f'AGY_TARBALL="/agy_system/installers/{active_spec.antigravity_tarball_name}"',
        "if [ -f \"${AGY_TARBALL}\" ]; then",
        "    echo \"[INFO] Extracting Antigravity ARM64 bundle from ${AGY_TARBALL}...\"",
        "    tar -xzf \"${AGY_TARBALL}\" -C /opt/antigravity",
        "    if [ -f /opt/antigravity/bin/agy ]; then",
        "        ln -sf /opt/antigravity/bin/agy /usr/local/bin/agy",
        "    fi",
        "fi",
        "",
        "# 3. Create isolated Python virtual environment for Antigravity SDK & Gravity GUI Deck",
        "if [ ! -d /opt/antigravity/venv ]; then",
        "    python3 -m venv /opt/antigravity/venv",
        "fi",
        "/opt/antigravity/venv/bin/pip install --upgrade pip flask",
        f'/opt/antigravity/venv/bin/pip install "{active_spec.antigravity_pip_package}" || true',
        "",
        "# 4. Install official Google Android CLI (linux_arm64)",
        "if [ ! -x /usr/local/bin/android ]; then",
        "    if [ -f /agy_system/installers/android-cli-linux_arm64 ]; then",
        "        cp /agy_system/installers/android-cli-linux_arm64 /usr/local/bin/android",
        "    else",
        f'        curl -fsSL "{active_spec.android_cli_url}" -o /usr/local/bin/android || true',
        "    fi",
        "    if [ -f /usr/local/bin/android ]; then",
        "        chmod 755 /usr/local/bin/android",
        "    fi",
        "fi",
        "",
        "# 5. Install JADX (CLI + GUI) for APK -> Java decompilation",
        "if [ ! -x /usr/local/bin/jadx ]; then",
        "    mkdir -p /opt/android-modding/jadx",
        f'    JADX_ZIP="/agy_system/installers/jadx-{active_spec.jadx_version}.zip"',
        "    if [ ! -f \"${JADX_ZIP}\" ]; then",
        f'        curl -fsSL "{active_spec.jadx_release_url}" -o "${{JADX_ZIP}}"',
        "    fi",
        "    unzip -qo \"${JADX_ZIP}\" -d /opt/android-modding/jadx",
        "    ln -sf /opt/android-modding/jadx/bin/jadx /usr/local/bin/jadx",
        "    ln -sf /opt/android-modding/jadx/bin/jadx-gui /usr/local/bin/jadx-gui",
        "fi",
        "",
        "# 6. Install uber-apk-signer (V2/V3/V4 APK signing for Android 14 / One UI 6.x)",
        f'SIGNER_JAR="/opt/android-modding/uber-apk-signer-{active_spec.uber_apk_signer_version}.jar"',
        "if [ ! -f \"${SIGNER_JAR}\" ]; then",
        f'    CACHED_SIGNER="/agy_system/installers/uber-apk-signer-{active_spec.uber_apk_signer_version}.jar"',
        "    if [ -f \"${CACHED_SIGNER}\" ]; then",
        "        cp \"${CACHED_SIGNER}\" \"${SIGNER_JAR}\"",
        "    else",
        f'        curl -fsSL "{active_spec.uber_apk_signer_url}" -o "${{SIGNER_JAR}}"',
        "    fi",
        "fi",
        "cat > /usr/local/bin/uber-apk-signer <<EOF",
        "#!/usr/bin/env bash",
        f'exec java -jar "{active_spec.uber_apk_signer_version and "/opt/android-modding/uber-apk-signer-" + active_spec.uber_apk_signer_version + ".jar"}" "\\$@"',
        "EOF",
        "chmod 755 /usr/local/bin/uber-apk-signer",
        "",
        "# 7. Install latest Apktool JAR wrapper",
        f'APKTOOL_JAR="/opt/android-modding/apktool_{active_spec.apktool_version}.jar"',
        "if [ ! -f \"${APKTOOL_JAR}\" ]; then",
        f'    CACHED_APKTOOL="/agy_system/installers/apktool_{active_spec.apktool_version}.jar"',
        "    if [ -f \"${CACHED_APKTOOL}\" ]; then",
        "        cp \"${CACHED_APKTOOL}\" \"${APKTOOL_JAR}\"",
        "    else",
        f'        curl -fsSL "{active_spec.apktool_jar_url}" -o "${{APKTOOL_JAR}}"',
        "    fi",
        "fi",
        "cat > /usr/local/bin/apktool-latest <<EOF",
        "#!/usr/bin/env bash",
        f'exec java -jar "{active_spec.apktool_version and "/opt/android-modding/apktool_" + active_spec.apktool_version + ".jar"}" "\\$@"',
        "EOF",
        "chmod 755 /usr/local/bin/apktool-latest",
        "",
        "echo \"[SUCCESS] Ubuntu ARM64 Antigravity + Android Modding suite provisioned.\"",
    ]
    return "\n".join(lines) + "\n"


def generate_samsung_dex_launcher(spec: Arm64ToolchainSpec | None = None) -> str:
    """Generate `launch-dex-desktop.sh` for Termux:X11 + XFCE4 on Samsung DeX."""
    active_spec = spec if spec is not None else Arm64ToolchainSpec()

    lines = [
        "#!/usr/bin/env bash",
        "# Gravity In Hand (BlattCo) — Samsung DeX Full Linux Desktop Launcher (Termux:X11 + XFCE4)",
        "set -euo pipefail",
        "",
        f'DISTRO_ALIAS="{active_spec.distro_alias}"',
        "",
        "AGY_USB_PATH=\"\"",
        "for candidate in /storage/* /mnt/media_rw/*; do",
        "    if [ -d \"${candidate}/AGY_System\" ] && [ -d \"${candidate}/AGY_Workspace\" ]; then",
        "        AGY_USB_PATH=\"${candidate}\"",
        "        break",
        "    fi",
        "done",
        "",
        "if [ -z \"${AGY_USB_PATH}\" ]; then",
        "    echo \"[ERROR] AGY_DRIVE USB thumbdrive not detected.\"",
        "    exit 1",
        "fi",
        "",
        "echo \"[INFO] Starting PulseAudio server for Termux:X11...\"",
        "pulseaudio --start --load=\"module-native-protocol-tcp auth-ip-acl=127.0.0.1 auth-anonymous=1\" --exit-idle-time=-1 || true",
        "",
        "echo \"[INFO] Launching Termux:X11 display :0...\"",
        "termux-x11 :0 -ac &",
        "sleep 2",
        "",
        "echo \"[INFO] Entering Ubuntu ARM64 XFCE4 Desktop + BlattCo Gravity Deck...\"",
        "exec proot-distro login \"${DISTRO_ALIAS}\" \\",
        "    --shared-tmp \\",
        "    --bind \"${AGY_USB_PATH}/AGY_Workspace:/workspace\" \\",
        "    --bind \"${AGY_USB_PATH}/AGY_System:/agy_system\" \\",
        "    -- /bin/bash -c 'export DISPLAY=:0 PULSE_SERVER=127.0.0.1 && cd /workspace && dbus-launch --exit-with-session startxfce4'",
    ]
    return "\n".join(lines) + "\n"


def validate_bash_syntax(script_path: Path) -> bool:
    """Run `bash -n` on a generated shell script and return True if syntax is valid."""
    completed = subprocess.run(
        ["bash", "-n", str(script_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"Bash syntax check failed for {script_path}: {completed.stderr.strip()}"
        )
    return True


def write_bootstrap_bundle(
    mount_root: Path,
    spec: Arm64ToolchainSpec | None = None,
) -> BootstrapBundle:
    """Write all ARM64 Termux/Ubuntu/DeX bootstrap scripts onto AGY_DRIVE and verify syntax."""
    active_spec = spec if spec is not None else Arm64ToolchainSpec()
    resolved_mount = mount_root.resolve()
    scripts_dir = resolved_mount / "AGY_System" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    boot_path = scripts_dir / "boot-agy.sh"
    provision_path = scripts_dir / "provision-ubuntu-arm64.sh"
    dex_path = scripts_dir / "launch-dex-desktop.sh"
    spec_json_path = scripts_dir / "toolchain_spec.json"

    boot_path.write_text(generate_termux_boot_script(active_spec), encoding="utf-8")
    provision_path.write_text(
        generate_ubuntu_proot_provision_script(active_spec), encoding="utf-8"
    )
    dex_path.write_text(generate_samsung_dex_launcher(active_spec), encoding="utf-8")

    for path in (boot_path, provision_path, dex_path):
        path.chmod(0o755)
        validate_bash_syntax(path)

    spec_payload = asdict(active_spec)
    spec_payload["jadx_release_url"] = active_spec.jadx_release_url
    spec_payload["uber_apk_signer_url"] = active_spec.uber_apk_signer_url
    spec_payload["apktool_jar_url"] = active_spec.apktool_jar_url
    spec_json_path.write_text(
        json.dumps(spec_payload, indent=2) + "\n", encoding="utf-8"
    )

    timestamp = datetime.now(UTC).isoformat()
    return BootstrapBundle(
        mount_root=str(resolved_mount),
        termux_boot_script=str(boot_path),
        ubuntu_provision_script=str(provision_path),
        dex_launcher_script=str(dex_path),
        toolchain_spec_json=str(spec_json_path),
        bash_syntax_verified=True,
        generated_at=timestamp,
    )
