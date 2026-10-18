import copy
from datetime import date
import json
import random
import sys

import pytest

from mirobody_gen import build, corpus, layout, person, spec
from mirobody_gen.facility_services import ServiceProfile


def config():
    institutions = spec.fiction()["institutions"]
    picks = []
    for lang in ("zh", "en"):
        picks.extend([i for i in institutions if i["language"] == lang and i["kind"] == "hospital"][:2])
    return {"schema_version": "fictional-facility-services-v1", "profile_id": "test-v1",
            "authority": "authored_synthetic", "required_by_exam_type": {"clinic": "outpatient"},
            "institutions": [{"name": i["name"], "services": ["outpatient"]} for i in picks]}


@pytest.mark.parametrize("mutation", ["authority", "unknown", "duplicate", "service_type", "exam_typo", "empty"])
def test_invalid_profile_is_rejected(mutation):
    data = config()
    if mutation == "authority": data["authority"] = "clinical_gold"
    elif mutation == "unknown": data["institutions"][0]["name"] = "not-a-catalogue-provider"
    elif mutation == "duplicate": data["institutions"].append(data["institutions"][0])
    elif mutation == "service_type": data["institutions"][0]["services"] = "outpatient"
    elif mutation == "exam_typo": data["required_by_exam_type"] = {"clinc": "outpatient"}
    else: data["institutions"] = []
    with pytest.raises(ValueError): ServiceProfile(data)


def test_small_declared_pool_reuses_only_supported_providers():
    profile = ServiceProfile(config())
    registry = layout.Registry(7)
    process = corpus.Institutions(registry, 7, profile)
    for group in ("zh", "en"):
        chosen = []
        for _ in range(300):
            index = process.draw(random.Random(len(chosen)), "hospital", group, required_service="outpatient")
            assert profile.supports(index, "outpatient")
            process.use(index)
            chosen.append(index)
        assert set(chosen) == set(profile.eligible("hospital", group, "outpatient"))


def test_unknown_support_does_not_fall_back_to_unconstrained():
    process = corpus.Institutions(layout.Registry(7), 7)
    with pytest.raises(ValueError, match="explicit service profile"):
        process.draw(random.Random(7), "hospital", "zh", required_service="outpatient")
    process = corpus.Institutions(layout.Registry(7), 7, ServiceProfile(config()))
    with pytest.raises(ValueError, match="No declared facility"):
        process.draw(random.Random(7), "hospital", "zh", required_service="unknown-service")


def test_catalogue_mutation_invalidates_profile():
    profile = ServiceProfile(config())
    catalogue = copy.deepcopy(spec.fiction()["institutions"])
    catalogue[0]["name"] += "changed"
    with pytest.raises(ValueError, match="catalogue changed"): profile.assert_catalog(catalogue)


def test_loaded_profile_does_not_modify_default_draws():
    registry = layout.Registry(7)
    a = corpus.Institutions(registry, 7)
    b = corpus.Institutions(registry, 7, ServiceProfile(config()))
    ar, br = random.Random(9), random.Random(9)
    for _ in range(80):
        x, y = a.draw(ar, "hospital", "en"), b.draw(br, "hospital", "en")
        assert x == y and ar.getstate() == br.getstate()
        a.use(x); b.use(y)


def test_preflight_requires_cross_language_coverage_before_output(tmp_path):
    data = config()
    data["institutions"] = data["institutions"][:2]
    profile = ServiceProfile(data)
    p = person.build_cohort(7, 1)[0]
    enc = person.encounter_for(p, date(2025, 3, 2), "clinic", ["vitals_clinic"], random.Random(7))
    with pytest.raises(ValueError, match="No declared facility"):
        corpus.render_corpus(7, [p], {p.person_id: [enc]}, tmp_path/"absent", service_profile=profile)
    assert not (tmp_path/"absent").exists()


def test_sticky_and_fresh_render_routes_record_service_evidence(tmp_path, monkeypatch):
    profile = ServiceProfile(config())
    p = person.build_cohort(7, 1)[0]
    visits = [person.encounter_for(p, date(2025, 3, d), "clinic", ["vitals_clinic"], random.Random(7))
              for d in range(1, 21)]
    monkeypatch.setattr(corpus, "render_doc", lambda doc, root, rel, *a, **kw: (root/(rel+".pdf"), 1, None))
    tmp_path.mkdir(exist_ok=True)
    records = corpus.render_corpus(7, [p], {p.person_id: visits}, tmp_path, service_profile=profile)
    clinical = [r for r in records if r["exam_type"] == "clinic"]
    assert len(clinical) == 20
    assert all(r["facility_service"]["provider"] == r["institution"] for r in clinical)
    assert all(r["facility_service"]["profile_sha256"] == profile.profile_sha256 for r in clinical)
    assert all(r["institution"] in {spec.fiction()["institutions"][i]["name"] for i in profile.providers}
               for r in clinical)


def test_cli_profile_requires_render_before_generation(tmp_path, monkeypatch):
    path = tmp_path/"profile.json"
    path.write_text(json.dumps(config()))
    monkeypatch.setattr(sys, "argv", ["build", "--facility-services", str(path)])
    monkeypatch.setattr(person, "build_cohort", lambda *a: pytest.fail("must reject before generation"))
    with pytest.raises(SystemExit) as exc: build.main()
    assert exc.value.code == 2


def test_two_services_on_same_date_keep_distinct_evidence(tmp_path, monkeypatch):
    data = config()
    data["required_by_exam_type"]["follow-up"] = "lab_followup"
    for item in data["institutions"]: item["services"].append("lab_followup")
    profile = ServiceProfile(data)
    p = person.build_cohort(7, 1)[0]
    visits = [person.encounter_for(p, date(2025, 3, 2), kind, ["vitals_clinic"], random.Random(7))
              for kind in ("clinic", "follow-up")]
    registry = layout.Registry(7)
    hospital = profile.eligible("hospital", "en", "outpatient")[0]
    centre = registry.pools[("en", "checkup_center")][0]
    monkeypatch.setattr(corpus, "home_institutions", lambda *a: ("en", {"hospital": hospital, "checkup_center": centre}))
    monkeypatch.setattr(corpus, "STICKY_FOLLOWUP", 1)
    monkeypatch.setattr(corpus, "render_doc", lambda doc, root, rel, *a, **kw: (root/(rel+".pdf"), 1, None))
    records = corpus.render_corpus(7, [p], {p.person_id: visits}, tmp_path, service_profile=profile)
    relevant = [r for r in records if r["exam_type"] in profile.required]
    assert {r["exam_type"] for r in relevant} == {"clinic", "follow-up"}
    assert all(r["facility_service"]["required_service"] == profile.required[r["exam_type"]] for r in relevant)
