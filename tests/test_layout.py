"""
The standalone scripts live in subdirectories but import `config` and `src`
from the repository root, and they must not execute on import. Both properties
are easy to break in a reorganisation, so they are pinned here.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = sorted(
    p.relative_to(ROOT).as_posix()
    for p in list((ROOT / "analysis").glob("*.py")) + list((ROOT / "scripts").glob("*.py"))
)


def test_scripts_are_discovered():
    assert SCRIPTS, "expected standalone scripts under analysis/ and scripts/"


@pytest.mark.parametrize("relpath", SCRIPTS)
def test_script_imports_without_running(relpath):
    spec = importlib.util.spec_from_file_location(f"_probe_{relpath}", ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # raises if the import path is broken
    assert hasattr(module, "main"), "entry point should be behind a __main__ guard"
