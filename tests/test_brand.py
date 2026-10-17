"""Nothing an audience reads names the TV format this game riffs on (AGENTS.md
rule 6). The game's own word, lower-case "the traitors", is fine; the show's
title and the old project's folder are not."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN = re.compile(r"The Traitors|AI Traitors")

AUDIENCE = (
    "README.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "CITATION.cff",
    "pyproject.toml",
    "frontend/index.html",
    "frontend/src/",
    "clips/render/src/",
    "src/badactors/castle/steward.py",
    "src/badactors/castle/rules.py",
    "src/badactors/castle/episode.py",
    ".github/ISSUE_TEMPLATE/",
    ".github/PULL_REQUEST_TEMPLATE.md",
)
"""Tracked files and folders whose words reach an audience: the README, the
site, the clips, what the Steward says, the rulebook, the printed episode, the
package metadata and the community files."""


def violations(text: str) -> list[str]:
    return [
        f"line {text.count(chr(10), 0, found.start()) + 1}: {found.group()!r}"
        for found in FORBIDDEN.finditer(text)
    ]


def _tracked() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout to know which files are tracked")
    listed = subprocess.run(
        [git, "ls-files", "-z", "--", *AUDIENCE],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [ROOT / name for name in listed.split("\0") if name]


def test_the_check_catches_the_title_and_the_old_folder() -> None:
    assert violations("Like The Traitors, but with poison.") != []
    assert violations("ported from ../AI Traitors/packages") != []
    assert violations("The traitors win on day 4. Two traitors remain.") == []


def test_audience_copy_never_names_the_format() -> None:
    files = _tracked()
    assert any(path.name == "README.md" for path in files)
    assert any("frontend/src" in path.as_posix() for path in files)
    found = {
        path.relative_to(ROOT).as_posix(): hits
        for path in files
        if path.is_file()
        and (hits := violations(path.read_text(encoding="utf-8", errors="replace")))
    }
    assert found == {}
