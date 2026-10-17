"""The package installs outside the checkout and plays a game from there."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import badactors

ROOT = Path(__file__).resolve().parent.parent
UV = shutil.which("uv")

pytestmark = pytest.mark.skipif(UV is None, reason="uv build needs uv")


def _run(*args: str | Path, cwd: Path, env: dict[str, str] | None = None) -> str:
    clean = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
    }
    done = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=clean | (env or {}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, f"{args}\n{done.stdout}\n{done.stderr}"
    return done.stdout


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    assert UV is not None
    dist = tmp_path_factory.mktemp("dist")
    # The default build makes the sdist, then the wheel from the sdist, so this
    # also checks that the sdist carries everything the wheel needs.
    _run(UV, "build", "--out-dir", dist, cwd=ROOT)
    (built,) = dist.glob("badactors-*.whl")
    return built


def test_the_wheel_carries_the_castle_and_nothing_else(wheel: Path) -> None:
    names = zipfile.ZipFile(wheel).namelist()
    for shipped in ("map.json", "cast.json", "balance.json", "qualification.json.xz"):
        assert f"badactors/castle/configs/{shipped}" in names
    assert all(
        name.startswith(("badactors/", f"badactors-{badactors.__version__}.dist-info/"))
        for name in names
    )
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)


def test_castle_game_runs_from_the_wheel_in_a_clean_venv(
    wheel: Path, tmp_path: Path
) -> None:
    assert UV is not None
    venv = tmp_path / "venv"
    _run(UV, "venv", "--python", sys.executable, venv, cwd=tmp_path)
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    # The locked dependencies, then the wheel on its own. uv sync reads wheels
    # from its cache where it can, so this needs no network after `uv sync`.
    _run(
        UV, "sync", "--frozen", "--no-dev", "--no-install-project",
        "--python", python, cwd=ROOT, env={"UV_PROJECT_ENVIRONMENT": str(venv)},
    )  # fmt: skip
    _run(UV, "pip", "install", "--no-deps", "--python", python, wheel, cwd=tmp_path)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    castle_game = python.parent / "castle-game"
    out = _run(castle_game, "--seed", "7", cwd=elsewhere)

    assert "EPISODE 7" in out
    assert "This one's bots." in out
    where = _run(
        python, "-c",
        "import badactors, badactors.castle.configs as c; "
        "print(badactors.__version__); print(c.MAP)",
        cwd=elsewhere,
    )  # fmt: skip
    version, shipped_map = where.split()
    assert version == badactors.__version__
    assert Path(shipped_map).is_relative_to(venv)
