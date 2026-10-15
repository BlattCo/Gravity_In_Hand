"""Unit and integration tests for src/shizuku_adb_bridge.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.shizuku_adb_bridge import (
    S23UltraModdingBridge,
    SystemSettingTweak,
    validate_package_name,
    validate_setting_tweak,
)


def _make_fake_runner(
    responses: dict[str, tuple[int, str, str]],
):
    """Create a deterministic command runner that matches command substrings."""

    def _runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        joined = " ".join(cmd)
        for pattern, (rc, out, err) in responses.items():
            if pattern in joined:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=rc, stdout=out, stderr=err
                )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="Success\n", stderr=""
        )

    return _runner


def test_detect_active_transport_prefers_rish_then_falls_back_to_adb() -> None:
    """Verify detect_active_transport detects Shizuku rish UID 2000 or falls back to adb."""
    rish_runner = _make_fake_runner({"rish -c id -u": (0, "2000\n", "")})
    bridge = S23UltraModdingBridge(runner=rish_runner, preferred_transport="rish")
    assert bridge.detect_active_transport() == "rish"

    adb_fallback_runner = _make_fake_runner(
        {
            "rish -c id -u": (1, "", "rish: shizuku not running"),
            "adb -s 127.0.0.1:5555 shell id -u": (0, "2000\n", ""),
        }
    )
    bridge_adb = S23UltraModdingBridge(
        runner=adb_fallback_runner, preferred_transport="rish"
    )
    assert bridge_adb.detect_active_transport() == "adb"


def test_list_packages_and_pull_split_apks(tmp_path: Path) -> None:
    """Verify listing packages and pulling base + split APKs into AGY_Workspace/apks/in/."""
    pm_list_output = (
        "package:/system/priv-app/SystemUI/SystemUI.apk=com.android.systemui\n"
        "package:/data/app/~~abc==/com.example.demo-xyz==/base.apk=com.example.demo\n"
    )
    pm_path_output = (
        "package:/data/app/~~abc==/com.example.demo-xyz==/base.apk\n"
        "package:/data/app/~~abc==/com.example.demo-xyz==/split_config.arm64_v8a.apk\n"
    )
    runner = _make_fake_runner(
        {
            "rish -c id -u": (0, "2000\n", ""),
            "pm list packages": (0, pm_list_output, ""),
            "pm path com.example.demo": (0, pm_path_output, ""),
            "cp '/data/app/": (0, "", ""),
        }
    )
    bridge = S23UltraModdingBridge(runner=runner)

    pkgs = bridge.list_packages()
    assert len(pkgs) == 2
    assert pkgs[0].package_name == "com.android.systemui"
    assert pkgs[0].is_system is True
    assert pkgs[1].package_name == "com.example.demo"
    assert pkgs[1].is_system is False

    pulled = bridge.pull_package_apks("com.example.demo", tmp_path)
    assert len(pulled) == 2
    assert pulled[0].name == "base.apk"
    assert pulled[1].name == "split_config.arm64_v8a.apk"


def test_decompile_rebuild_sign_and_install_pipeline(tmp_path: Path) -> None:
    """Verify full APK decompile -> rebuild -> uber-apk-signer -> pm install -r -d -g pipeline."""
    fake_apk = tmp_path / "apks" / "in" / "com.example.demo" / "base.apk"
    fake_apk.parent.mkdir(parents=True, exist_ok=True)
    fake_apk.write_bytes(b"PK\x03\x04FAKE_APK_BYTES")

    runner = _make_fake_runner(
        {
            "rish -c id -u": (0, "2000\n", ""),
            "apktool d": (0, "I: Decoding AndroidManifest.xml\n", ""),
            "apktool b": (0, "I: Built apk into patched\n", ""),
            "uber-apk-signer": (0, "Successfully processed 1 APKs\n", ""),
            "pm install -r -d -g": (0, "Success\n", ""),
        }
    )
    bridge = S23UltraModdingBridge(runner=runner)

    decompiled_dir = bridge.decompile_apk(fake_apk, tmp_path, use_jadx=False)
    decompiled_dir.mkdir(parents=True, exist_ok=True)
    assert decompiled_dir.name == "base_apktool"

    artifact = bridge.build_and_sign_apk(
        decompiled_dir, tmp_path, package_name="com.example.demo"
    )
    assert "Successfully processed 1 APKs" in artifact.signer_stdout

    signed_apk_path = Path(artifact.signed_apk)
    signed_apk_path.write_bytes(b"PK\x03\x04SIGNED_APK")

    install_res = bridge.install_signed_apk(signed_apk_path)
    assert install_res.succeeded is True
    assert "Success" in install_res.stdout


def test_critical_boot_packages_cannot_be_disabled() -> None:
    """Verify set_package_state blocks disabling critical One UI / Android system packages."""
    runner = _make_fake_runner({"rish -c id -u": (0, "2000\n", "")})
    bridge = S23UltraModdingBridge(runner=runner)

    with pytest.raises(PermissionError, match="Refusing to disable critical"):
        bridge.set_package_state("com.android.systemui", enabled=False)

    with pytest.raises(PermissionError, match="Refusing to disable critical"):
        bridge.set_package_state("moe.shizuku.privileged.api", enabled=False)

    res = bridge.set_package_state("com.samsung.android.bixby.agent", enabled=False)
    assert res.succeeded is True


def test_system_tweaks_ui_dump_and_logcat_validation(tmp_path: Path) -> None:
    """Verify settings put tweaks, uiautomator dump, logcat capture, and injection rejection."""
    runner = _make_fake_runner(
        {
            "rish -c id -u": (0, "2000\n", ""),
            "settings put global window_animation_scale 0.5": (0, "", ""),
            "uiautomator dump": (0, "UI hierchary dumped to: /data/local/tmp/agy_ui_dump.xml\n", ""),
            "logcat -d -t 50 *:W": (0, "W/ActivityManager: Slow operation\n", ""),
        }
    )
    bridge = S23UltraModdingBridge(runner=runner)

    tweak_res = bridge.apply_system_tweak(
        SystemSettingTweak(
            namespace="global", key="window_animation_scale", value="0.5"
        )
    )
    assert tweak_res.succeeded is True

    xml_path = bridge.dump_ui_hierarchy(tmp_path)
    assert xml_path.name == "ui_dump.xml"

    log_out = bridge.capture_logcat_snapshot(lines=50, filter_spec="*:W")
    assert "Slow operation" in log_out

    with pytest.raises(ValueError, match="Invalid Android package identifier"):
        validate_package_name("com.bad.pkg; rm -rf /")

    with pytest.raises(ValueError, match="Unsafe or empty settings value"):
        validate_setting_tweak(
            SystemSettingTweak(
                namespace="global", key="window_animation_scale", value="0.5; reboot"
            )
        )
