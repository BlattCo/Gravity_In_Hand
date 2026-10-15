"""Unit and integration tests for src/arm64_bootstrap.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.arm64_bootstrap import (
    Arm64ToolchainSpec,
    generate_samsung_dex_launcher,
    generate_termux_boot_script,
    generate_ubuntu_proot_provision_script,
    validate_bash_syntax,
    write_bootstrap_bundle,
)
from src.usb_provisioner import provision_agy_drive


def test_toolchain_spec_urls_and_defaults() -> None:
    """Verify Arm64ToolchainSpec generates accurate release URLs for jadx, apktool, and signer."""
    spec = Arm64ToolchainSpec()
    assert spec.architecture == "aarch64"
    assert "jadx-1.5.1.zip" in spec.jadx_release_url
    assert "uber-apk-signer-1.3.0.jar" in spec.uber_apk_signer_url
    assert "apktool_2.10.0.jar" in spec.apktool_jar_url
    assert "linux_arm64/android" in spec.android_cli_url


def test_generated_scripts_contain_no_hardcoded_host_paths() -> None:
    """Verify generated ARM64 scripts strictly obey Directive 18.3 (no hardcoded /home/blatt)."""
    boot_sh = generate_termux_boot_script()
    prov_sh = generate_ubuntu_proot_provision_script()
    dex_sh = generate_samsung_dex_launcher()

    for content in (boot_sh, prov_sh, dex_sh):
        assert "/home/blatt" not in content
        assert "set -euo pipefail" in content

    assert "proot-distro login" in boot_sh
    assert "--bind \"${AGY_USB_PATH}/AGY_Workspace:/workspace\"" in boot_sh
    assert "google-antigravity" in prov_sh
    assert "uber-apk-signer" in prov_sh
    assert "jadx-gui" in prov_sh
    assert "termux-x11 :0" in dex_sh
    assert "startxfce4" in dex_sh


def test_write_bootstrap_bundle_and_bash_syntax(tmp_path: Path) -> None:
    """Verify write_bootstrap_bundle writes all scripts and passes `bash -n` syntax validation."""
    fake_usb = tmp_path / "AGY_DRIVE"
    fake_usb.mkdir(parents=True, exist_ok=True)
    provision_agy_drive(fake_usb)

    bundle = write_bootstrap_bundle(fake_usb)
    assert bundle.bash_syntax_verified is True

    boot_path = Path(bundle.termux_boot_script)
    prov_path = Path(bundle.ubuntu_provision_script)
    dex_path = Path(bundle.dex_launcher_script)
    spec_path = Path(bundle.toolchain_spec_json)

    assert boot_path.is_file()
    assert prov_path.is_file()
    assert dex_path.is_file()
    assert spec_path.is_file()

    spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
    assert spec_data["architecture"] == "aarch64"
    assert spec_data["antigravity_pip_package"] == "google-antigravity"


def test_validate_bash_syntax_rejects_invalid_script(tmp_path: Path) -> None:
    """Verify validate_bash_syntax raises ValueError on malformed shell syntax."""
    broken_script = tmp_path / "broken.sh"
    broken_script.write_text("#!/usr/bin/env bash\nif [ -z ; then\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Bash syntax check failed"):
        validate_bash_syntax(broken_script)
