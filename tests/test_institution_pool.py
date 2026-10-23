import random
from types import SimpleNamespace

import pytest

from mirobody_gen import clinical, corpus, layout


@pytest.fixture(autouse=True)
def every_institution(monkeypatch):
    """Pool mechanics on stand-in registries, which have no templates for the clinical profile to filter."""
    monkeypatch.setattr(clinical, "ENABLED", False)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("seed", [7, 8, 9, 14])
def test_english_hospital_pool_can_finish_the_existing_200_draw_replay(seed, enabled, monkeypatch):
    monkeypatch.setattr(clinical, "ENABLED", enabled)
    registry = layout.Registry(seed)
    process = corpus.Institutions(registry, seed)
    rng = random.Random(f"services:{seed}:en")
    for _ in range(200):
        index = process.draw(rng, "hospital", "en")
        assert registry.institutions[index]["kind"] == "hospital"
        assert registry.institutions[index]["language"] == "en"
        process.use(index)


def _registry(pool, *, big=None):
    return SimpleNamespace(institutions=[{"kind": "hospital", "language": "zh"},
                                        {"kind": "hospital", "language": "en"},
                                        {"kind": "hospital", "language": "en"},
                                        {"kind": "clinic", "language": "en"}],
                           pools={("en", "hospital"): pool}, big=big or {})


def test_exhausted_fresh_branch_reuses_only_same_pool_with_actual_weights():
    process = corpus.Institutions(_registry([1, 2]), 7)
    process.counts = {0: 999, 1: 3, 2: 9, 3: 888}

    class CheckRandom:
        def random(self): return 1.0  # force the formerly failing fresh-institution branch

        def choices(self, population, weights):
            assert population == [1, 2] and weights == [3, 9]
            return [2]

    assert process.draw(CheckRandom(), "hospital", "en") == 2


def test_reserved_unseen_big_client_is_not_stolen_when_reusing_exhausted_pool():
    process = corpus.Institutions(_registry([1, 2], big={"hospital": 1}), 7)
    process.use(2)
    # A fresh draw must reuse2, rather than consuming the unused reserved big-client1.
    assert process.draw(random.Random(2), "hospital", "en") == 2


@pytest.mark.parametrize("pool,big", [([], {}), ([1], {"hospital": 1})])
def test_no_available_or_seen_candidate_fails_explicitly_instead_of_stop_iteration(pool, big):
    process = corpus.Institutions(_registry(pool, big=big), 7)
    with pytest.raises(ValueError, match="No available or reusable institution"):
        process.draw(random.Random(7), "hospital", "en")
