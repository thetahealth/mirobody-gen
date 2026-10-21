"""The pre-commit hook's name-field predicate reads the same under a C and a UTF-8 locale.

Under LC_ALL=C the bracket `[:：]` matched any byte of the full-width colon, so the full-width comma
after 受检者 in narratives.json rejected every commit that staged that file on such a machine.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

HOOK = pathlib.Path(__file__).resolve().parents[1] / ".githooks" / "pre-commit"


def _check(tmp_path: pathlib.Path, text: str, locale: str) -> int:
    repo = tmp_path / locale
    repo.mkdir(parents=True)
    env = {**os.environ, "LC_ALL": locale}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "note.txt").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "note.txt"], cwd=repo, check=True, env=env)
    return subprocess.run(["bash", str(HOOK), "--check"], cwd=repo, env=env, capture_output=True).returncode


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_a_full_width_comma_is_not_a_name_field(tmp_path, locale):
    assert _check(tmp_path, "已按规定通知受检者，请立即前往相应专科就诊。\n", locale) == 0


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_a_name_field_is_still_rejected(tmp_path, locale):
    assert _check(tmp_path, "姓名" + "：" + "某某\n", locale) == 1
    assert _check(tmp_path / "en", "Patient Name" + ": X\n", locale) == 1
