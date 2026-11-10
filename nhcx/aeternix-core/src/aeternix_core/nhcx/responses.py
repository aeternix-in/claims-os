"""Interpret a ClaimResponseBundle from the payer.

Mapping (NHA Implementation Guide 3.2 / 7.4):
  outcome "complete" -> approved; partially_approved when the eligible total < submitted (mock/assumption)
  outcome "error"    -> rejected
  outcome "partial"  -> query    ("Queried"; the document query itself arrives on communication/request)
  outcome "queued"   -> submitted (still being processed: no state change)
Totals: "submitted", and the payable amount from "benefit" (claims) or "eligpercent".
"""
from dataclasses import dataclass


@dataclass
class Decision:
    decision: str
    outcome: str | None
    disposition: str | None
    preauth_ref: str | None
    submitted: float | None
    eligible: float | None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class UnreadableResponse(ValueError):
    pass


def _totals(cr: dict) -> dict[str, float]:
    out = {}
    for t in cr.get("total") or []:
        for coding in (t.get("category") or {}).get("coding", []):
            if coding.get("code"):
                out[coding["code"]] = (t.get("amount") or {}).get("value")
    return out


def interpret(bundle: dict) -> Decision:
    crs = [e.get("resource") for e in bundle.get("entry", [])
           if (e.get("resource") or {}).get("resourceType") == "ClaimResponse"]
    if len(crs) != 1:
        raise UnreadableResponse(f"expected exactly one ClaimResponse, found {len(crs)}")
    cr = crs[0]
    outcome = cr.get("outcome")
    totals = _totals(cr)
    submitted = totals.get("submitted")
    eligible = totals.get("benefit") if totals.get("benefit") is not None else totals.get("eligpercent")

    if outcome == "complete":
        if eligible is not None and submitted is not None and eligible < submitted:
            decision = "partially_approved"
        else:
            decision = "approved"
    elif outcome == "error":
        decision = "rejected"
    elif outcome == "partial":
        decision = "query"
    elif outcome == "queued":
        decision = "submitted"
    else:
        raise UnreadableResponse(f"unknown ClaimResponse.outcome {outcome!r}")
    return Decision(decision, outcome, cr.get("disposition"), cr.get("preAuthRef"), submitted, eligible)
