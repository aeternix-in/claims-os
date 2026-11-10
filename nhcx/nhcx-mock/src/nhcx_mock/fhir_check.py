"""Request-bundle checks modelled on the official NHCX v6.5.0 examples.

These are structural/semantic checks, NOT full profile validation. Full conformance
(cardinality, terminology bindings) needs the HL7 FHIR Validator + the ndhm.in#6.5.0
package - planned next step.

Rules enforced (source in brackets):
- Bundle.type fixed to "collection"                        [ClaimBundle profile]
- exactly one Claim; Claim.use matches the endpoint        [ClaimBundle profile / workflow]
- every entry has a unique fullUrl                         [FHIR bdl-7]
- Bundle/Claim meta.profile present (strict mode only)     [mock rule; official examples always set it]
- Claim.patient/insurer/provider/insurance.coverage
  references resolve to entries inside the bundle          [official examples are self-contained]
- Claim.item[].net sum equals Claim.total (when both set)  [mock business rule]
"""
from .config import get_settings
from .errors import HcxError
from .workflows import Workflow

PROFILE_BASE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/"
BUNDLE_PROFILE = {"Claim": PROFILE_BASE + "ClaimBundle"}


def _err(msg: str) -> HcxError:
    return HcxError(400, "ERR_INVALID_DOMAIN_PAYLOAD", msg)


def find_resources(bundle: dict, resource_type: str) -> list[dict]:
    return [
        e["resource"] for e in bundle.get("entry", [])
        if isinstance(e, dict) and isinstance(e.get("resource"), dict)
        and e["resource"].get("resourceType") == resource_type
    ]


def find_entry(bundle: dict, resource_type: str) -> dict | None:
    for e in bundle.get("entry", []):
        if isinstance(e, dict) and (e.get("resource") or {}).get("resourceType") == resource_type:
            return e
    return None


def _resolvable(bundle: dict) -> set[str]:
    refs = set()
    for e in bundle.get("entry", []):
        r = e.get("resource") or {}
        if e.get("fullUrl"):
            refs.add(e["fullUrl"])
        if r.get("resourceType") and r.get("id"):
            refs.add(f"{r['resourceType']}/{r['id']}")
    return refs


def check_request_bundle(bundle: dict, wf: Workflow) -> dict:
    if bundle.get("resourceType") != "Bundle":
        raise _err("FHIR payload must be a Bundle")
    if bundle.get("type") != "collection":
        raise _err(f"Bundle.type must be 'collection' (got {bundle.get('type')!r})")
    entries = bundle.get("entry")
    if not isinstance(entries, list) or not entries:
        raise _err("Bundle.entry must be a non-empty list")

    full_urls = [e.get("fullUrl") for e in entries if isinstance(e, dict)]
    if any(not u for u in full_urls):
        raise _err("Every Bundle.entry must have a fullUrl")
    if len(set(full_urls)) != len(full_urls):
        raise _err("Bundle.entry.fullUrl values must be unique (bdl-7)")

    found = find_resources(bundle, wf.request_resource)
    if len(found) != 1:
        raise _err(f"Bundle must contain exactly one {wf.request_resource} (found {len(found)})")
    resource = found[0]

    if get_settings().strict_profiles:
        expected = BUNDLE_PROFILE.get(wf.request_resource)
        if expected and expected not in (bundle.get("meta") or {}).get("profile", []):
            raise _err(f"Bundle.meta.profile must include {expected}")
        res_profile = PROFILE_BASE + wf.request_resource
        if res_profile not in (resource.get("meta") or {}).get("profile", []):
            raise _err(f"{wf.request_resource}.meta.profile must include {res_profile}")

    if wf.request_resource != "Claim":
        return resource

    if wf.claim_use and resource.get("use") != wf.claim_use:
        raise _err(f"Claim.use must be '{wf.claim_use}' for /{wf.name}/{wf.action} "
                   f"(got {resource.get('use')!r})")

    resolvable = _resolvable(bundle)
    for field in ("patient", "insurer", "provider"):
        ref = (resource.get(field) or {}).get("reference")
        if not ref:
            raise _err(f"Claim.{field}.reference is required")
        if ref not in resolvable:
            raise _err(f"Claim.{field}.reference {ref!r} does not resolve to an entry in the bundle")
    insurance = resource.get("insurance") or []
    if not insurance:
        raise _err("Claim.insurance is required")
    for ins in insurance:
        ref = (ins.get("coverage") or {}).get("reference")
        if not ref or ref not in resolvable:
            raise _err(f"Claim.insurance.coverage.reference {ref!r} does not resolve to an entry in the bundle")

    items = resource.get("item") or []
    total = (resource.get("total") or {}).get("value")
    nets = [(i.get("net") or {}).get("value") for i in items]
    if items and total is not None and all(n is not None for n in nets):
        if abs(sum(nets) - total) > 0.01:
            raise _err(f"Claim.total ({total}) does not equal the sum of item.net ({sum(nets)})")
    return resource
