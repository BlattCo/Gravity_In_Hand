"""USB Thumbdrive Partition Inspector and exFAT Provisioning Engine.

Inspects USB block devices via lsblk JSON, computes safe non-destructive
partition plans for a 50GB exFAT partition (AGY_DRIVE) alongside existing
ext4 partitions (such as Kali Linux), and provisions the AGY_System/ and
AGY_Workspace/ directory tree and portable Antigravity rule configuration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GIB_BYTES: int = 1024 * 1024 * 1024
MIB_BYTES: int = 1024 * 1024
PARTITION_TABLE_OVERHEAD_BYTES: int = 16 * MIB_BYTES
MIN_EXT4_FREE_HEADROOM_BYTES: int = 4 * GIB_BYTES

PROTECTED_MOUNTPOINTS: frozenset[str] = frozenset(
    {"/", "/boot", "/boot/efi", "/home", "/var", "/usr"}
)

REQUIRED_AGY_DIRECTORIES: tuple[str, ...] = (
    "AGY_System/rootfs",
    "AGY_System/installers",
    "AGY_System/scripts",
    "AGY_Workspace/projects",
    "AGY_Workspace/apks/in",
    "AGY_Workspace/apks/decompiled",
    "AGY_Workspace/apks/patched",
    "AGY_Workspace/apks/signed",
    "AGY_Workspace/logs",
    "AGY_Workspace/.gemini/config/rules",
)


@dataclass(frozen=True)
class PartitionInfo:
    """Metadata for a single partition on a block device."""

    name: str
    path: str
    size_bytes: int
    fstype: str | None
    label: str | None
    mountpoint: str | None
    start_sector: int | None = None
    end_sector: int | None = None

    @property
    def is_mounted(self) -> bool:
        """Return True if the partition currently has an active mountpoint."""
        return bool(self.mountpoint and self.mountpoint.strip())


@dataclass(frozen=True)
class BlockDevice:
    """Metadata for a physical or loop block device and its child partitions."""

    name: str
    path: str
    size_bytes: int
    model: str
    tran: str
    removable: bool
    partitions: tuple[PartitionInfo, ...]

    @property
    def allocated_bytes(self) -> int:
        """Return the sum of all partition sizes on this block device."""
        return sum(part.size_bytes for part in self.partitions)

    @property
    def unallocated_bytes(self) -> int:
        """Return estimated unallocated bytes after accounting for GPT/MBR overhead."""
        usable_total = max(0, self.size_bytes - PARTITION_TABLE_OVERHEAD_BYTES)
        return max(0, usable_total - self.allocated_bytes)

    @property
    def has_protected_mount(self) -> bool:
        """Return True if any child partition is mounted on a protected OS path."""
        for part in self.partitions:
            if part.mountpoint in PROTECTED_MOUNTPOINTS:
                return True
        return False

    @property
    def is_usb_candidate(self) -> bool:
        """Return True if this device is a removable or USB-attached non-system disk."""
        if self.has_protected_mount:
            return False
        tran_norm = self.tran.lower().strip()
        return tran_norm == "usb" or self.removable


@dataclass(frozen=True)
class PartitionPlan:
    """Non-destructive execution plan to create the exFAT AGY_DRIVE partition."""

    device_path: str
    target_label: str
    target_fstype: str
    requested_size_bytes: int
    unallocated_bytes: int
    can_create_without_resize: bool
    shrink_candidate_partition: str | None
    shrink_by_bytes: int
    new_partition_path: str
    commands: tuple[str, ...]
    safety_warnings: tuple[str, ...]


@dataclass(frozen=True)
class ProvisioningReport:
    """Summary of directories and files provisioned onto a mounted AGY_DRIVE."""

    mount_root: str
    created_directories: tuple[str, ...]
    created_files: tuple[str, ...]
    synced_rule_files: tuple[str, ...]
    timestamp: str


def _parse_int(value: Any, default: int = 0) -> int:
    """Safely convert an integer or numeric string from lsblk JSON into an int."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        return int(stripped)
    return int(value)


def _parse_optional_int(value: Any) -> int | None:
    """Safely convert an optional integer field from lsblk JSON."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _parse_int(value)


def _extract_mountpoint(node: dict[str, Any]) -> str | None:
    """Extract primary mountpoint from lsblk node ('mountpoint' or 'mountpoints')."""
    mp = node.get("mountpoint")
    if isinstance(mp, str) and mp.strip():
        return mp.strip()
    mps = node.get("mountpoints")
    if isinstance(mps, list):
        for item in mps:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def parse_lsblk_json(raw_json: str) -> list[BlockDevice]:
    """Parse `lsblk -J -b` JSON output into a list of BlockDevice instances."""
    payload = json.loads(raw_json)
    if not isinstance(payload, dict):
        raise ValueError("Invalid lsblk JSON payload: top-level value must be an object.")

    raw_devices = payload.get("blockdevices", [])
    if not isinstance(raw_devices, list):
        raise ValueError("Invalid lsblk JSON payload: 'blockdevices' must be a list.")

    devices: list[BlockDevice] = []
    for node in raw_devices:
        if not isinstance(node, dict):
            continue
        dev_type = str(node.get("type") or "").lower()
        if dev_type != "disk":
            continue

        name = str(node.get("name") or "").strip()
        if not name:
            continue
        path = str(node.get("path") or f"/dev/{name}").strip()
        size_bytes = _parse_int(node.get("size"), 0)
        model = str(node.get("model") or "Unknown Model").strip()
        tran = str(node.get("tran") or "").strip()
        rm_raw = node.get("rm")
        removable = bool(rm_raw in (True, 1, "1", "true", "True"))

        children_raw = node.get("children") or []
        partitions: list[PartitionInfo] = []
        if isinstance(children_raw, list):
            for child in children_raw:
                if not isinstance(child, dict):
                    continue
                child_type = str(child.get("type") or "").lower()
                if child_type != "part":
                    continue
                part_name = str(child.get("name") or "").strip()
                if not part_name:
                    continue
                part_path = str(child.get("path") or f"/dev/{part_name}").strip()
                part_size = _parse_int(child.get("size"), 0)
                fstype_raw = child.get("fstype")
                fstype = (
                    str(fstype_raw).strip()
                    if isinstance(fstype_raw, str) and fstype_raw.strip()
                    else None
                )
                label_raw = child.get("label")
                label = (
                    str(label_raw).strip()
                    if isinstance(label_raw, str) and label_raw.strip()
                    else None
                )
                mountpoint = _extract_mountpoint(child)
                start_sector = _parse_optional_int(child.get("start"))
                end_sector = _parse_optional_int(child.get("ptend"))
                partitions.append(
                    PartitionInfo(
                        name=part_name,
                        path=part_path,
                        size_bytes=part_size,
                        fstype=fstype,
                        label=label,
                        mountpoint=mountpoint,
                        start_sector=start_sector,
                        end_sector=end_sector,
                    )
                )

        devices.append(
            BlockDevice(
                name=name,
                path=path,
                size_bytes=size_bytes,
                model=model,
                tran=tran,
                removable=removable,
                partitions=tuple(partitions),
            )
        )

    return devices


def _default_lsblk_runner(cmd: list[str]) -> str:
    """Execute lsblk command and return stdout as string."""
    completed = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def discover_usb_devices(
    runner: Callable[[list[str]], str] | None = None,
) -> list[BlockDevice]:
    """Discover USB / removable block devices, strictly excluding protected system disks."""
    active_runner = runner if runner is not None else _default_lsblk_runner
    cmd = [
        "lsblk",
        "-J",
        "-b",
        "-o",
        "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,MODEL,TRAN,RM",
    ]
    raw_json = active_runner(cmd)
    all_devices = parse_lsblk_json(raw_json)
    return [dev for dev in all_devices if dev.is_usb_candidate]


def _next_partition_path(device: BlockDevice) -> str:
    """Compute the device node path for the next sequential partition."""
    next_index = len(device.partitions) + 1
    if device.path[-1].isdigit():
        return f"{device.path}p{next_index}"
    return f"{device.path}{next_index}"


def build_exfat_partition_plan(
    device: BlockDevice,
    target_size_gb: int = 50,
    label: str = "AGY_DRIVE",
) -> PartitionPlan:
    """Build a non-destructive partition plan for a 50GB exFAT partition on a USB drive.

    Preserves existing ext4 partitions (such as Kali Linux). If sufficient unallocated
    space already exists on the thumbdrive, generates direct `parted` + `mkfs.exfat`
    commands. Otherwise, identifies the largest unmounted ext4 partition that can be
    safely shrunk offline and generates the exact `e2fsck`, `resize2fs`, `parted`, and
    `mkfs.exfat` command sequence.
    """
    if target_size_gb <= 0:
        raise ValueError("target_size_gb must be a positive integer.")
    if not label or len(label.strip()) > 15:
        raise ValueError("exFAT volume label must be between 1 and 15 characters.")
    if device.has_protected_mount:
        raise PermissionError(
            f"Refusing to plan partition changes on system disk {device.path}."
        )

    for part in device.partitions:
        if part.label == label:
            raise ValueError(
                f"Partition {part.path} on {device.path} already carries label '{label}'."
            )

    requested_bytes = target_size_gb * GIB_BYTES
    unallocated = device.unallocated_bytes
    new_part_path = _next_partition_path(device)
    next_part_num = len(device.partitions) + 1
    warnings: list[str] = []

    if unallocated >= requested_bytes:
        start_mib = (device.allocated_bytes + PARTITION_TABLE_OVERHEAD_BYTES) // MIB_BYTES
        end_mib = start_mib + (requested_bytes // MIB_BYTES)
        commands = (
            f"sudo parted --script {device.path} mkpart primary {start_mib}MiB {end_mib}MiB",
            f"sudo partprobe {device.path}",
            f"sudo mkfs.exfat -n {label} {new_part_path}",
        )
        warnings.append(
            "Verify that all existing partitions on the USB drive are backed up before running parted."
        )
        return PartitionPlan(
            device_path=device.path,
            target_label=label,
            target_fstype="exfat",
            requested_size_bytes=requested_bytes,
            unallocated_bytes=unallocated,
            can_create_without_resize=True,
            shrink_candidate_partition=None,
            shrink_by_bytes=0,
            new_partition_path=new_part_path,
            commands=commands,
            safety_warnings=tuple(warnings),
        )

    deficit_bytes = requested_bytes - unallocated
    ext4_candidates = [
        part
        for part in device.partitions
        if (part.fstype or "").lower() == "ext4"
    ]
    if not ext4_candidates:
        raise ValueError(
            f"Device {device.path} has only {unallocated} unallocated bytes and no ext4 "
            f"partition available to shrink for requested {requested_bytes} bytes."
        )

    largest_ext4 = max(ext4_candidates, key=lambda p: p.size_bytes)
    if largest_ext4.is_mounted:
        raise RuntimeError(
            f"Candidate ext4 partition {largest_ext4.path} is currently mounted at "
            f"'{largest_ext4.mountpoint}'. Unmount it before planning an offline shrink."
        )

    minimum_remaining = requested_bytes + MIN_EXT4_FREE_HEADROOM_BYTES
    if largest_ext4.size_bytes <= deficit_bytes + MIN_EXT4_FREE_HEADROOM_BYTES:
        raise ValueError(
            f"Partition {largest_ext4.path} ({largest_ext4.size_bytes} bytes) is too small "
            f"to shrink by {deficit_bytes} bytes while preserving headroom "
            f"(requires > {minimum_remaining} bytes)."
        )

    new_ext4_size_bytes = largest_ext4.size_bytes - deficit_bytes
    new_ext4_size_gib = new_ext4_size_bytes // GIB_BYTES
    shrink_part_num = next_part_num - 1

    other_allocated_bytes = device.allocated_bytes - largest_ext4.size_bytes
    new_ext4_end_mib = (
        other_allocated_bytes + PARTITION_TABLE_OVERHEAD_BYTES + new_ext4_size_bytes
    ) // MIB_BYTES
    exfat_end_mib = new_ext4_end_mib + (requested_bytes // MIB_BYTES)

    commands = (
        f"sudo e2fsck -f {largest_ext4.path}",
        f"sudo resize2fs {largest_ext4.path} {new_ext4_size_gib}G",
        f"sudo parted --script {device.path} resizepart {shrink_part_num} {new_ext4_end_mib}MiB",
        f"sudo parted --script {device.path} mkpart primary {new_ext4_end_mib}MiB {exfat_end_mib}MiB",
        f"sudo partprobe {device.path}",
        f"sudo mkfs.exfat -n {label} {new_part_path}",
    )
    warnings.extend(
        [
            f"Offline ext4 shrink required on {largest_ext4.path} "
            f"(label={largest_ext4.label or 'unlabeled'}).",
            "Ensure e2fsck completes with zero errors before executing resize2fs and parted resizepart.",
        ]
    )

    return PartitionPlan(
        device_path=device.path,
        target_label=label,
        target_fstype="exfat",
        requested_size_bytes=requested_bytes,
        unallocated_bytes=unallocated,
        can_create_without_resize=False,
        shrink_candidate_partition=largest_ext4.path,
        shrink_by_bytes=deficit_bytes,
        new_partition_path=new_part_path,
        commands=commands,
        safety_warnings=tuple(warnings),
    )


def provision_agy_drive(
    mount_root: Path,
    source_repo_root: Path | None = None,
) -> ProvisioningReport:
    """Provision the AGY_System/ and AGY_Workspace/ directory tree on a mounted exFAT drive.

    Creates all required directories, writes `AGY_System/manifest.json` and
    `AGY_Workspace/README_WORKSPACE.md`, and synchronizes `AGENT.md`, `AGENTS.md`,
    and `PROJECT_STATE.json` if `source_repo_root` is provided.
    """
    resolved_mount = mount_root.resolve()
    if not resolved_mount.exists() or not resolved_mount.is_dir():
        raise FileNotFoundError(
            f"Target mount root '{resolved_mount}' does not exist or is not a directory."
        )

    created_dirs: list[str] = []
    for rel_dir in REQUIRED_AGY_DIRECTORIES:
        target_dir = resolved_mount / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        created_dirs.append(str(target_dir))

    timestamp = datetime.now(UTC).isoformat()
    manifest_path = resolved_mount / "AGY_System" / "manifest.json"
    manifest_payload = {
        "volume_label": "AGY_DRIVE",
        "filesystem": "exfat",
        "architecture": "aarch64-linux-android",
        "target_device": "Samsung Galaxy S23 Ultra (SM-S918U / Snapdragon 8 Gen 2)",
        "provisioned_at": timestamp,
        "directories": list(REQUIRED_AGY_DIRECTORIES),
        "components": {
            "antigravity_arm64_ide": "AGY_System/installers/Antigravity-linux-arm.tar.gz",
            "antigravity_python_sdk": "google-antigravity (manylinux_2_17_aarch64)",
            "android_cli": "AGY_System/installers/android-cli-linux_arm64.tar.gz",
            "modding_tools": [
                "apktool",
                "jadx",
                "uber-apk-signer",
                "aapt2",
                "adb",
                "fastboot",
                "rish",
            ],
        },
    }
    manifest_path.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")

    workspace_readme_path = resolved_mount / "AGY_Workspace" / "README_WORKSPACE.md"
    workspace_readme_content = (
        "# AGY_Workspace (Portable Thumbdrive Workspace)\n\n"
        "This directory resides on the 50GB `exFAT` (`AGY_DRIVE`) partition and is "
        "bind-mounted directly into the ARM64 Ubuntu environment on the Samsung Galaxy "
        "S23 Ultra as `/workspace`.\n\n"
        "## Directory Structure\n"
        "- `projects/`: Active software & Antigravity agent repositories\n"
        "- `apks/in/`: Raw APKs pulled from S23 Ultra via Shizuku (`rish`) or `adb`\n"
        "- `apks/decompiled/`: Decoded resources and smali/Java sources (`apktool` / `jadx`)\n"
        "- `apks/patched/`: Modified APK builds prior to signing\n"
        "- `apks/signed/`: V2/V3/V4 signed APKs ready for `pm install -r -d`\n"
        "- `logs/`: Session logs, `logcat` captures, and audit traces\n"
        "- `.gemini/config/rules/`: Portable Architect Prime `AGENTS.md` enforcement\n"
    )
    workspace_readme_path.write_text(workspace_readme_content, encoding="utf-8")

    created_files: list[str] = [str(manifest_path), str(workspace_readme_path)]
    synced_rules: list[str] = []

    if source_repo_root is not None:
        resolved_source = source_repo_root.resolve()
        rules_dest_dir = resolved_mount / "AGY_Workspace" / ".gemini" / "config" / "rules"
        workspace_root_dir = resolved_mount / "AGY_Workspace"

        for filename in ("AGENT.md", "AGENTS.md", "PROJECT_STATE.json"):
            src_file = resolved_source / filename
            if src_file.is_file():
                dest_workspace_file = workspace_root_dir / filename
                shutil.copyfile(src_file, dest_workspace_file)
                synced_rules.append(str(dest_workspace_file))

                if filename in ("AGENT.md", "AGENTS.md"):
                    dest_rule_file = rules_dest_dir / filename
                    shutil.copyfile(src_file, dest_rule_file)
                    synced_rules.append(str(dest_rule_file))

    return ProvisioningReport(
        mount_root=str(resolved_mount),
        created_directories=tuple(created_dirs),
        created_files=tuple(created_files),
        synced_rule_files=tuple(synced_rules),
        timestamp=timestamp,
    )


def verify_agy_drive_layout(mount_root: Path) -> bool:
    """Return True if all required AGY_DRIVE directories and manifest.json are present."""
    resolved_mount = mount_root.resolve()
    if not resolved_mount.is_dir():
        return False

    for rel_dir in REQUIRED_AGY_DIRECTORIES:
        if not (resolved_mount / rel_dir).is_dir():
            return False

    manifest_path = resolved_mount / "AGY_System" / "manifest.json"
    if not manifest_path.is_file():
        return False

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    return isinstance(data, dict) and data.get("volume_label") == "AGY_DRIVE"


def serialize_plan_to_json(plan: PartitionPlan) -> str:
    """Serialize a PartitionPlan dataclass into formatted JSON."""
    return json.dumps(asdict(plan), indent=2)
