"""The transformer must reproduce the official NRCeS example from our internal model."""
import copy
import json
from pathlib import Path

from aeternix_core.nhcx.fhir_claim import to_claim_bundle
from aeternix_core.samples import sample_preauth

OFFICIAL = json.loads((Path(__file__).parent / "fixtures" /
                       "nhcx_claimbundle_preauth_example01.json").read_text())


def normalise(bundle: dict) -> list[dict]:
    """Replace generated ids/urns with positional labels so bundles can be compared."""
    b = copy.deepcopy(bundle)
    labels = {e["fullUrl"]: f"ENTRY{i}" for i, e in enumerate(b["entry"])}
    text = json.dumps([e["resource"] for e in b["entry"]])
    for urn, label in labels.items():
        text = text.replace(urn, label).replace(urn.removeprefix("urn:uuid:"), label)
    return json.loads(text)


def strip_known_differences(resources: list[dict]) -> list[dict]:
    # 0. Identifiers: we follow NHA's NHCX guide (CLN, NPI, NIIP, NH, HPIN, PI/ADN); the NRCeS example
    #    uses other types (PRN, ROHINI, MD, untyped policy). Checked separately in test_nhcx_identifier_types.
    for r in resources:
        r.pop("identifier", None)
    claim = resources[0]
    # 1. Timezone: the official example uses +05:00 (likely a typo for IST); we emit +05:30.
    claim.pop("created"), claim.pop("billablePeriod")
    # 2. We add item.quantity (10 ICU days); the example implies it (2000 x 10 = 20000) but omits it.
    for item in claim["item"]:
        item.pop("quantity", None)
    return resources


def test_matches_official_example():
    ours = strip_known_differences(normalise(to_claim_bundle(sample_preauth())))
    official = strip_known_differences(normalise(OFFICIAL))
    for mine, theirs in zip(ours, official, strict=True):
        assert mine == theirs, f"{mine['resourceType']} differs from the official example"


def test_bundle_level_fields():
    b = to_claim_bundle(sample_preauth(), submission_id="sub-1")
    assert b["type"] == "collection"
    assert b["meta"] == OFFICIAL["meta"]
    assert b["identifier"] == {"value": "sub-1"}
    claim = b["entry"][0]["resource"]
    assert claim["created"] == "2025-03-08T11:01:00+05:30"
    assert claim["item"][2]["quantity"] == {"value": 10}


def test_resource_ids_are_deterministic():
    a, b = to_claim_bundle(sample_preauth()), to_claim_bundle(sample_preauth())
    assert [e["fullUrl"] for e in a["entry"]] == [e["fullUrl"] for e in b["entry"]]
    c = to_claim_bundle(sample_preauth(case_id="OTHER"))
    assert a["entry"][0]["fullUrl"] != c["entry"][0]["fullUrl"]


def test_optional_fields_omitted_when_absent():
    case = sample_preauth()
    case.patient.aadhaar_number = None
    case.patient.phone = None
    case.procedures = []
    b = to_claim_bundle(case)
    patient = b["entry"][1]["resource"]
    assert [i["type"]["coding"][0]["code"] for i in patient["identifier"]] == ["PI"]   # PI always, no ADN
    assert "telecom" not in patient
    assert "procedure" not in b["entry"][0]["resource"]


def ident_codes(resource: dict) -> dict[str, str]:
    return {i["type"]["coding"][0]["code"]: i["value"] for i in resource.get("identifier", []) if "type" in i}


def test_nhcx_identifier_types():
    """Identifier types NHA's NHCX guide requires (section 3.3 / 7.3)."""
    case = sample_preauth()
    res = {e["resource"]["resourceType"] + ("/" + e["resource"]["type"][0]["coding"][0]["code"]
                                             if e["resource"]["resourceType"] == "Organization" else ""): e["resource"]
           for e in to_claim_bundle(case)["entry"]}
    assert ident_codes(res["Claim"]) == {"CLN": "7612345"}
    assert ident_codes(res["Organization/prov"]) == {"NPI": "45675454"}
    assert ident_codes(res["Organization/ins"]) == {"NIIP": "mock-payer@hcx"}
    assert res["Organization/prov"]["name"] and res["Organization/ins"]["name"]
    assert ident_codes(res["Coverage"])["NH"] == "XYZHLIP21001V012021"
    assert ident_codes(res["Practitioner"]) == {"HPIN": "21-1521-3828-3227"}
    assert ident_codes(res["Patient"]) == {"PI": "UHID-0042", "ADN": "7225-4829-5255"}
    assert all(" " not in e["fullUrl"] for e in to_claim_bundle(case)["entry"])


def test_outpatient_claim_type():
    from aeternix_core.domain.models import CareSetting
    case = sample_preauth()
    case.care_setting = CareSetting.OUTPATIENT
    assert to_claim_bundle(case)["entry"][0]["resource"]["type"]["coding"][0]["code"] == "737492002"
