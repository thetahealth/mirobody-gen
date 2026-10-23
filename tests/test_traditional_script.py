"""script_of files Traditional spellings as Traditional, so simplified documents never print them, and no
printable name carries a region tag."""

from __future__ import annotations

import re

import pytest

from mirobody_gen import spec
from mirobody_gen.layout import script_of


@pytest.mark.parametrize("variant", ["身長", "收縮壓", "舒張壓", "乙型肝炎表面抗體", "總膽固醇"])
def test_traditional_spellings_are_traditional(variant):
    assert script_of(variant) == "zh-Hant"


@pytest.mark.parametrize("variant", ["身高", "收缩压", "舒张压", "乙型肝炎表面抗体", "总胆固醇"])
def test_simplified_spellings_stay_simplified(variant):
    assert script_of(variant) == "zh-Hans"


#: A region or locale tag ("空腹血糖(HK)") marks where a spelling is used; printed, it reads as part of
#: the item name, as clinician review saw on "(HK)" checkup rows.
REGION_TAG = re.compile(r"[(（]\s*(?:HK|TW|MO|CN|SG|US|UK|JP|港|台|澳|中国|香港|台湾|澳门)\s*[)）]", re.I)


def _printable_names():
    for key, item in spec.indicators().items():
        for name in (item["zh"], item["en"], item["abbr"], *(item.get("name_variants") or [])):
            if name:
                yield key, name


def test_no_printable_name_carries_a_region_tag():
    assert not [(key, name) for key, name in _printable_names() if REGION_TAG.search(name)]


def test_every_name_filed_as_simplified_is_in_the_simplified_character_set():
    """GB2312 holds Simplified characters only, so this check does not lean on script_of's own table."""
    def outside(name):
        return [ch for ch in name if ch >= "⺀" and not ch.encode("gb2312", errors="ignore")]

    assert not [(key, name) for key, name in _printable_names() if script_of(name) == "zh-Hans" and outside(name)]
