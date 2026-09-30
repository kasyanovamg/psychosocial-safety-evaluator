"""Built-wheel scenario/selection resources and source-tree-independent smoke."""

import os
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OPENAI_ROOT = ROOT / "integrations" / "openai"


def _assert_mit_license_metadata(archive: zipfile.ZipFile) -> str:
    names = set(archive.namelist())
    metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
    license_name = next(name for name in names if name.endswith(".dist-info/licenses/LICENSE"))
    metadata = archive.read(metadata_name).decode()
    assert "License-Expression: MIT\n" in metadata
    assert archive.read(license_name).decode() == (ROOT / "LICENSE").read_text()
    return metadata


def test_built_wheel_resolves_and_executes_selection_without_source_tree(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    build = subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps",
            "--no-build-isolation", "--wheel-dir", str(wheelhouse),
        ],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = next(wheelhouse.glob("psychosocial_safety_evaluator-*.whl"))
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = _assert_mit_license_metadata(archive)
        assert "Requires-Python: <3.15,>=3.14\n" in metadata
        assert "Requires-Dist: pydantic<3,>=2.12\n" in metadata
        assert 'Requires-Dist: streamlit<2,>=1.55; extra == "ui"\n' in metadata
        assert "psych_eval/selection.py" in names
        assert "psych_eval/integrations/workflow.py" in names
        assert {f"psych_eval/_scenario_pack/RS-{number:03}.yaml" for number in range(1, 21)} <= names
        assert "psych_eval/_fixture_data/demo_targets/relational_sycophancy/RS-001.yaml" in names
        assert "psych_eval/_fixture_data/demo_judges/relational_sycophancy/RS-001.yaml" in names
        archive.extractall(installed)

    script = """
from pathlib import Path
import psych_eval
from psych_eval.cli import execute_fixture_pack
from psych_eval.integrations.workflow import prepare_evaluation
from psych_eval.runs import load_run
from psych_eval.selection import resolve_selection
from psych_eval.suite import discover_pack

installed = Path(__import__('sys').argv[1]).resolve()
assert Path(psych_eval.__file__).resolve().is_relative_to(installed)
assert len(discover_pack()) == 20
assert callable(prepare_evaluation)
selection = resolve_selection(mode='quick')
bundle = Path(__import__('sys').argv[2])
run = execute_fixture_pack(bundle, selection=selection)
assert run.coverage.selected_count == 3
assert load_run(bundle / 'run.json', verify_references=True) == run
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed)
    smoke = subprocess.run(
        [sys.executable, "-c", script, str(installed), str(tmp_path / "bundle")],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr


def test_openai_wheel_includes_mit_license_metadata(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    build = subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel", str(OPENAI_ROOT), "--no-deps",
            "--no-build-isolation", "--wheel-dir", str(wheelhouse),
        ],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = next(wheelhouse.glob("psych_eval_openai-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata = _assert_mit_license_metadata(archive)
        assert "Requires-Python: <3.15,>=3.14\n" in metadata
        assert "Requires-Dist: pydantic<3,>=2.12\n" in metadata
        assert "Requires-Dist: openai<4,>=3.8\n" in metadata
