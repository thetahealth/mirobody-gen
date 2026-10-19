"""script_of files Traditional spellings as Traditional, so simplified documents never print them."""

from __future__ import annotations

import pytest

from mirobody_gen.layout import script_of


@pytest.mark.parametrize("variant", ["身長", "收縮壓", "舒張壓", "乙型肝炎表面抗體", "總膽固醇"])
def test_traditional_spellings_are_traditional(variant):
    assert script_of(variant) == "zh-Hant"


@pytest.mark.parametrize("variant", ["身高", "收缩压", "舒张压", "乙型肝炎表面抗体", "总胆固醇"])
def test_simplified_spellings_stay_simplified(variant):
    assert script_of(variant) == "zh-Hans"
