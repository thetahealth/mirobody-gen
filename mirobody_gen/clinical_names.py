"""Clinical profile: names (`build --clinical`).

A default build draws a patient's printed name without regard to sex, and the fiction pool joins
given-name characters freely, so review saw men printed under women's names, names that mix a
feminine and a masculine character, and two people of one cohort under the same name. In clinical
mode a patient's name reads as the person's sex, never mixes, and differs from every other
patient's while the pool lasts; a signing doctor's or technician's name never mixes. The default
draws are still made.
"""

from __future__ import annotations

import hashlib
import math

from . import spec

#: Given-name characters of the fiction pool by the sex they usually read as in mainland usage. The
#: rest (乐佳华宁安平文晨楠清然睿) read as either.
FEMININE = frozenset("芳娜敏静丽艳娟秀霞桂英玉萍红欣怡婷雪琳瑶悦萱涵彤蕾薇岚")
MASCULINE = frozenset("伟强磊军洋勇杰涛明超刚建辉力宇浩鑫博泽轩航凯松柏峰川海波翔腾飞健康阳远源帆")
#: English first names of the pool by sex; the romanised Chinese ones (Wei, Ling, ...) read as either.
FEMALE_FIRST_EN = frozenset({"Mary", "Linda", "Susan", "Karen", "Nancy", "Laura", "Emily", "Megan", "Hannah",
                             "Grace", "Chloe", "Alice", "Ella", "Nora"})
MALE_FIRST_EN = frozenset({"James", "John", "David", "Daniel", "Paul", "Mark", "Steven", "Kevin", "Brian", "Eric",
                           "Adam", "Ryan", "Owen", "Lucas", "Henry"})


def name_sex(name: str) -> str:
    """"female", "male", "neutral" or "mixed": how a fiction-pool name reads."""
    if " " in name:
        first = name.split(" ", 1)[0]
        return "female" if first in FEMALE_FIRST_EN else "male" if first in MALE_FIRST_EN else "neutral"
    female = any(ch in FEMININE for ch in name[1:])
    male = any(ch in MASCULINE for ch in name[1:])
    return "mixed" if female and male else "female" if female else "male" if male else "neutral"


def _side(name: str) -> str:
    """A name's sex, with neutral names split between the sexes by hash so the two never share one."""
    kind = name_sex(name)
    if kind != "neutral":
        return kind
    return "female" if int(hashlib.sha256(name.encode()).hexdigest(), 16) % 2 else "male"


def patient_name(person, pool: list[str], drawn: str) -> str:
    """A name of the person's sex, distinct within a cohort: the person's number times a stride
    coprime to the size of that sex's pool. `drawn` (the default draw) when the pool has none."""
    own = sorted(n for n in pool if name_sex(n) != "mixed" and _side(n) == person.sex)
    if not own:
        return drawn
    stride = next(s for s in range(max(1, int(len(own) * 0.618)), 10 * len(own) + 2) if math.gcd(s, len(own)) == 1)
    index = int("".join(ch for ch in person.person_id if ch.isdigit()) or 0)
    return own[(index * stride) % len(own)]


def signatures(entries: list[tuple[str, str]], english: bool) -> list[tuple[str, str]]:
    """(role, name) signature lines with every mixed name replaced as `staff_name` replaces it."""
    pool = spec.fiction()["person_names_en" if english else "person_names_zh"]
    return [(role, staff_name(name, pool)) for role, name in entries]


def staff_name(drawn: str, pool: list[str]) -> str:
    """The drawn name, or the next one in the pool if it mixes both sexes."""
    if name_sex(drawn) != "mixed":
        return drawn
    start = pool.index(drawn) if drawn in pool else 0
    return next((pool[(start + k) % len(pool)] for k in range(len(pool))
                 if name_sex(pool[(start + k) % len(pool)]) != "mixed"), drawn)
