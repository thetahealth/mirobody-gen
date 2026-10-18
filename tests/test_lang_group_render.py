"""`--lang-mix ja:1 --render` runs the real build end to end.

A group without its own document vocabulary falls back to English documents (`spec.doc_lang`), while
the demographic group stays `ja` for device time zones. Before the fix, ordinary visits passed the
demographic group straight to the institution pools and the build died with KeyError ('ja', 'hospital')
before writing any file.
"""

import collections
import json
import re

from mirobody_gen import build, person


def test_ja_population_builds_and_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(person, "_LANG_MIX", person._LANG_MIX)
    out = tmp_path / "ja"
    build.main(["--seed", "7", "--people", "2", "--lang-mix", "ja:1", "--render", "--out", str(out)])

    records = [json.loads(line) for line in (out / "files.jsonl").open(encoding="utf-8")]
    assert records and all((out / r["file"]).is_file() for r in records)
    # document wording falls back to the zh/en vocabularies; cross-language visits may be zh
    languages = collections.Counter(r["language"] for r in records)
    assert set(languages) <= {"en", "zh-Hans"} and languages["en"] > languages.get("zh-Hans", 0)

    assert {person.person_lang(7, pid) for pid in ("p001", "p002")} == {"ja"}
    offsets = collections.Counter(re.findall(r'T\d\d:\d\d:\d\d([+-]\d\d:\d\d)"',
                                             (out / "devices.jsonl").read_text(encoding="utf-8")))
    assert offsets and set(offsets) == {"+09:00"}
