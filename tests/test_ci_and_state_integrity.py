"""End-to-end CI, Zero-Stub, and PROJECT_STATE.json integrity tests (T-006)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from src.arm64_bootstrap import write_bootstrap_bundle
from src.usb_provisioner import provision_agy_drive, verify_agy_drive_layout


def test_ci_workflow_exists_and_runs_all_verifiers() -> None:
    """Verify docs/ci/ci.yml exists and includes AST, bash -n, ruff, mypy, and pytest."""
    repo_root = Path(__file__).resolve().parent.parent
    ci_file = repo_root / "docs" / "ci" / "ci.yml"
    assert ci_file.is_file()

    content = ci_file.read_text(encoding="utf-8")
    assert "ast.parse" in content
    assert "bash -n run.sh" in content
    assert "ruff check src tests" in content
    assert "mypy src tests" in content
    assert "pytest -v" in content


def test_zero_stub_and_zero_placeholder_policy_across_codebase() -> None:
    """Verify no Python module in src/ or tests/ contains pass stubs or NotImplementedError."""
    repo_root = Path(__file__).resolve().parent.parent
    py_files = list((repo_root / "src").rglob("*.py")) + list(
        (repo_root / "tests").rglob("*.py")
    )
    assert len(py_files) >= 9

    for py_path in py_files:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Pass):
                raise AssertionError(f"Forbidden 'pass' statement found in {py_path}")
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                func = node.exc.func
                if isinstance(func, ast.Name) and func.id == "NotImplementedError":
                    raise AssertionError(
                        f"Forbidden 'NotImplementedError' found in {py_path}"
                    )


def test_project_state_json_schema_and_tasks_integrity() -> None:
    """Verify PROJECT_STATE.json adheres to the Architect Prime JSON state schema."""
    repo_root = Path(__file__).resolve().parent.parent
    state_path = repo_root / "PROJECT_STATE.json"
    assert state_path.is_file()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["project_name"] == "Gravity_In_Hand"
    assert state["github_repository_url"] == "https://github.com/BlattCo/Gravity_In_Hand"
    assert state["architecture_target"] == "Antigravity-Universal-Agent-Runtime"
    assert state["concurrency_limit"] == 2
    assert isinstance(state["deviations_from_plan"], list)

    tasks = state["tasks"]
    assert len(tasks) == 6
    expected_ids = ["T-001", "T-002", "T-003", "T-004", "T-005", "T-006"]
    assert [t["task_id"] for t in tasks] == expected_ids


def test_end_to_end_usb_drive_and_arm64_bootstrap_provisioning(tmp_path: Path) -> None:
    """Verify full end-to-end provisioning of AGY_DRIVE with ARM64 scripts and rule sync."""
    repo_root = Path(__file__).resolve().parent.parent
    simulated_usb = tmp_path / "AGY_DRIVE"
    simulated_usb.mkdir(parents=True, exist_ok=True)

    prov_report = provision_agy_drive(
        mount_root=simulated_usb,
        source_repo_root=repo_root,
    )
    assert verify_agy_drive_layout(simulated_usb) is True
    assert len(prov_report.synced_rule_files) >= 4

    bundle = write_bootstrap_bundle(mount_root=simulated_usb)
    assert bundle.bash_syntax_verified is True
    assert (simulated_usb / "AGY_System" / "scripts" / "boot-agy.sh").is_file()
    assert (
        simulated_usb / "AGY_System" / "scripts" / "provision-ubuntu-arm64.sh"
    ).is_file()
    assert (
        simulated_usb / "AGY_System" / "scripts" / "launch-dex-desktop.sh"
    ).is_file()
