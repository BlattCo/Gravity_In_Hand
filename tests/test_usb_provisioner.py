"""Unit and integration tests for src/usb_provisioner.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.usb_provisioner import (
    GIB_BYTES,
    BlockDevice,
    PartitionInfo,
    build_exfat_partition_plan,
    discover_usb_devices,
    parse_lsblk_json,
    provision_agy_drive,
    serialize_plan_to_json,
    verify_agy_drive_layout,
)

SAMPLE_LSBLK_JSON = json.dumps(
    {
        "blockdevices": [
            {
                "name": "nvme0n1",
                "path": "/dev/nvme0n1",
                "size": 1000204886016,
                "type": "disk",
                "fstype": None,
                "label": None,
                "mountpoint": None,
                "model": "Samsung SSD 990 PRO 1TB",
                "tran": "nvme",
                "rm": False,
                "children": [
                    {
                        "name": "nvme0n1p1",
                        "path": "/dev/nvme0n1p1",
                        "size": 536870912,
                        "type": "part",
                        "fstype": "vfat",
                        "label": "EFI",
                        "mountpoint": "/boot/efi",
                    },
                    {
                        "name": "nvme0n1p2",
                        "path": "/dev/nvme0n1p2",
                        "size": 999668015104,
                        "type": "part",
                        "fstype": "ext4",
                        "label": "ROOT",
                        "mountpoint": "/",
                    },
                ],
            },
            {
                "name": "sdb",
                "path": "/dev/sdb",
                "size": 128 * GIB_BYTES,
                "type": "disk",
                "fstype": None,
                "label": None,
                "mountpoint": None,
                "model": "SanDisk Ultra USB-C",
                "tran": "usb",
                "rm": True,
                "children": [
                    {
                        "name": "sdb1",
                        "path": "/dev/sdb1",
                        "size": 64 * GIB_BYTES,
                        "type": "part",
                        "fstype": "ext4",
                        "label": "KALI_PERSIST",
                        "mountpoint": None,
                    }
                ],
            },
        ]
    }
)


def test_parse_lsblk_and_discover_usb_filters_system_disk() -> None:
    """Verify discover_usb_devices returns only the USB thumbdrive and never the OS root disk."""
    devices = parse_lsblk_json(SAMPLE_LSBLK_JSON)
    assert len(devices) == 2

    nvme_dev = devices[0]
    usb_dev = devices[1]
    assert nvme_dev.has_protected_mount is True
    assert nvme_dev.is_usb_candidate is False
    assert usb_dev.has_protected_mount is False
    assert usb_dev.is_usb_candidate is True

    discovered = discover_usb_devices(runner=lambda _cmd: SAMPLE_LSBLK_JSON)
    assert len(discovered) == 1
    assert discovered[0].path == "/dev/sdb"
    assert discovered[0].partitions[0].label == "KALI_PERSIST"


def test_build_exfat_plan_in_unallocated_space() -> None:
    """Verify direct partition creation when >= 50GB unallocated space exists alongside Kali ext4."""
    usb_devices = discover_usb_devices(runner=lambda _cmd: SAMPLE_LSBLK_JSON)
    usb_dev = usb_devices[0]

    plan = build_exfat_partition_plan(usb_dev, target_size_gb=50, label="AGY_DRIVE")
    assert plan.can_create_without_resize is True
    assert plan.shrink_candidate_partition is None
    assert plan.shrink_by_bytes == 0
    assert plan.new_partition_path == "/dev/sdb2"
    assert plan.requested_size_bytes == 50 * GIB_BYTES
    assert any("mkpart primary" in cmd for cmd in plan.commands)
    assert any("mkfs.exfat -n AGY_DRIVE /dev/sdb2" in cmd for cmd in plan.commands)

    serialized = json.loads(serialize_plan_to_json(plan))
    assert serialized["target_label"] == "AGY_DRIVE"
    assert serialized["new_partition_path"] == "/dev/sdb2"


def test_build_exfat_plan_with_offline_ext4_shrink() -> None:
    """Verify safe offline e2fsck + resize2fs shrink plan when ext4 occupies the full USB drive."""
    full_ext4_usb = BlockDevice(
        name="sdc",
        path="/dev/sdc",
        size_bytes=128 * GIB_BYTES,
        model="Samsung BAR Plus USB-C",
        tran="usb",
        removable=True,
        partitions=(
            PartitionInfo(
                name="sdc1",
                path="/dev/sdc1",
                size_bytes=120 * GIB_BYTES,
                fstype="ext4",
                label="KALI_FULL",
                mountpoint=None,
            ),
        ),
    )

    plan = build_exfat_partition_plan(full_ext4_usb, target_size_gb=50, label="AGY_DRIVE")
    assert plan.can_create_without_resize is False
    assert plan.shrink_candidate_partition == "/dev/sdc1"
    assert plan.shrink_by_bytes > 40 * GIB_BYTES
    assert plan.new_partition_path == "/dev/sdc2"
    assert plan.commands[0] == "sudo e2fsck -f /dev/sdc1"
    assert plan.commands[1].startswith("sudo resize2fs /dev/sdc1 ")
    assert any("resizepart 1" in cmd for cmd in plan.commands)
    assert plan.commands[-1] == "sudo mkfs.exfat -n AGY_DRIVE /dev/sdc2"


def test_build_exfat_plan_rejects_mounted_ext4_shrink() -> None:
    """Verify that attempting to shrink a currently mounted ext4 partition raises RuntimeError."""
    mounted_usb = BlockDevice(
        name="sdc",
        path="/dev/sdc",
        size_bytes=128 * GIB_BYTES,
        model="Samsung BAR Plus USB-C",
        tran="usb",
        removable=True,
        partitions=(
            PartitionInfo(
                name="sdc1",
                path="/dev/sdc1",
                size_bytes=120 * GIB_BYTES,
                fstype="ext4",
                label="KALI_LIVE",
                mountpoint="/media/blatt/KALI_LIVE",
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="currently mounted"):
        build_exfat_partition_plan(mounted_usb, target_size_gb=50, label="AGY_DRIVE")


def test_build_exfat_plan_rejects_protected_system_disk() -> None:
    """Verify that system disks mounted on / or /boot/efi are strictly rejected."""
    devices = parse_lsblk_json(SAMPLE_LSBLK_JSON)
    nvme_disk = devices[0]

    with pytest.raises(PermissionError, match="Refusing to plan partition changes"):
        build_exfat_partition_plan(nvme_disk, target_size_gb=50, label="AGY_DRIVE")


def test_provision_and_verify_agy_drive_layout(tmp_path: Path) -> None:
    """Verify provision_agy_drive creates all directories, manifest.json, and syncs AGENT.md."""
    fake_mount = tmp_path / "AGY_DRIVE"
    fake_mount.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parent.parent
    report = provision_agy_drive(mount_root=fake_mount, source_repo_root=repo_root)

    assert Path(report.mount_root) == fake_mount.resolve()
    assert len(report.created_directories) == 10
    assert verify_agy_drive_layout(fake_mount) is True

    manifest_file = fake_mount / "AGY_System" / "manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["volume_label"] == "AGY_DRIVE"
    assert manifest_data["architecture"] == "aarch64-linux-android"
    assert "rish" in manifest_data["components"]["modding_tools"]

    synced_agent_md = fake_mount / "AGY_Workspace" / "AGENT.md"
    synced_global_rule = (
        fake_mount / "AGY_Workspace" / ".gemini" / "config" / "rules" / "AGENTS.md"
    )
    synced_state = fake_mount / "AGY_Workspace" / "PROJECT_STATE.json"
    assert synced_agent_md.is_file()
    assert synced_global_rule.is_file()
    assert synced_state.is_file()
