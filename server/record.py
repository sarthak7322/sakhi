"""Visit record schema, synthetic register lookup, immunisation schedule, validation and follow-ups."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import config

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

VISIT_TYPES = ["ANC", "PNC", "HBNC", "Immunisation", "Other"]
BENEFICIARY_TYPES = ["pregnant", "postpartum", "newborn", "child"]

# Fields the LLM may set (name -> type). Everything else is computed.
EDITABLE = {
    "beneficiary_id": str,
    "beneficiary_name": str,
    "beneficiary_type": str,
    "visit_type": str,
    "weight_kg": float,
    "bp_systolic": int,
    "bp_diastolic": int,
    "hb_g_dl": float,
    "temperature_c": float,
    "temperature_f": float,
    "respiratory_rate": int,
    "ifa_tablets_given": int,
    "vaccinations_given_today": list,
    "vaccinations_due_noted": list,   # dues she dictates ("TT2 due hai"), used when the register can't tell
    "gestation_months": float,        # "7 महीने की गर्भवती" (new beneficiary: estimates LMP)
    "age_days": int,                  # "बच्चा 10 दिन का" (new beneficiary: estimates DOB)
    "next_visit_date": str,
    "notes": str,
}

REQUIRED_TO_SAVE = ["beneficiary_id", "visit_type"]

RANGES = {
    "weight_kg": {"newborn": (0.5, 6.0), "child": (1.0, 30.0), "pregnant": (25.0, 150.0),
                  "postpartum": (25.0, 150.0), None: (0.5, 150.0)},
    "bp_systolic": (60, 250), "bp_diastolic": (30, 160), "hb_g_dl": (2.0, 20.0),
    "temperature_c": (34.0, 42.5), "temperature_f": (93.0, 108.5),
    "respiratory_rate": {"newborn": (20, 100), "child": (15, 70), "pregnant": (8, 40), "postpartum": (8, 40), None: (8, 100)},
    "ifa_tablets_given": (0, 200),
}


def new_record(today: date) -> dict:
    return {
        "beneficiary_id": None, "beneficiary_name": None, "beneficiary_type": None, "visit_type": None,
        "visit_date": today.isoformat(), "weight_kg": None, "bp_systolic": None, "bp_diastolic": None,
        "hb_g_dl": None, "temperature_c": None, "temperature_f": None, "respiratory_rate": None,
        "ifa_tablets_given": None, "vaccinations_given_today": [], "vaccinations_due": [], "vaccinations_due_noted": [],
        "gestation_months": None, "age_days": None,
        "next_visit_date": None, "next_visit_suggested": None, "notes": None,
        "danger_signs": [], "referral_required": False, "registered": False, "context": {},
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Synthetic register
# ---------------------------------------------------------------------------

def load_registry() -> list[dict]:
    with open(config.REGISTRY_FILE, encoding="utf-8") as f:
        return json.load(f)["beneficiaries"]


REGISTRY = load_registry()
REGISTRY_BY_ID = {b["id"]: b for b in REGISTRY}

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def find_in_text(text: str) -> dict | None:
    """Register entry named in an utterance (4-digit id, or full name in either script)."""
    t = text.translate(_DEVANAGARI_DIGITS)
    for m in re.finditer(r"(?<!\d)(\d{4})(?!\d)", t):
        if m.group(1) in REGISTRY_BY_ID:
            return REGISTRY_BY_ID[m.group(1)]
    low = t.lower()
    hits = [b for b in REGISTRY if b["name"].lower() in low or b["name_local"] in t]
    return max(hits, key=lambda b: len(b["name"])) if hits else None  # "Baby of Pooja Yadav" beats "Pooja Yadav"


def lookup(bid: str | None) -> dict | None:
    if not bid:
        return None
    return REGISTRY_BY_ID.get(re.sub(r"\D", "", str(bid).translate(_DEVANAGARI_DIGITS)))


# ---------------------------------------------------------------------------
# Immunisation schedule (Universal Immunization Programme, MoHFW) – age in days
# ---------------------------------------------------------------------------

CHILD_SCHEDULE = [
    # (vaccine, due_at_days, last_valid_day or None)
    ("BCG", 0, 365), ("OPV-0", 0, 15), ("HepB-0", 0, 1),
    ("OPV-1", 42, None), ("Penta-1", 42, None), ("RVV-1", 42, None), ("fIPV-1", 42, None), ("PCV-1", 42, None),
    ("OPV-2", 70, None), ("Penta-2", 70, None), ("RVV-2", 70, None),
    ("OPV-3", 98, None), ("Penta-3", 98, None), ("RVV-3", 98, None), ("fIPV-2", 98, None), ("PCV-2", 98, None),
    ("MR-1", 270, None), ("PCV-Booster", 270, None), ("fIPV-3", 270, None), ("VitA-1", 270, None),
    ("MR-2", 480, None), ("DPT-B1", 480, None), ("OPV-B", 480, None), ("VitA-2", 480, None),
]
VACCINE_ALIASES = {
    "bcg": "BCG", "opv0": "OPV-0", "opv 0": "OPV-0", "zero dose polio": "OPV-0", "hepb": "HepB-0", "hep b": "HepB-0",
    "penta": "Penta", "pentavalent": "Penta", "opv": "OPV", "polio": "OPV", "rota": "RVV", "rvv": "RVV",
    "ipv": "fIPV", "fipv": "fIPV", "pcv": "PCV", "mr": "MR", "measles": "MR", "khasra": "MR", "dpt": "DPT-B1",
    "vitamin a": "VitA", "vit a": "VitA", "tt": "Td", "td": "Td", "tetanus": "Td",
}


def _parse(d: str | None) -> date | None:
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def canonical_vaccine(name: str) -> str:
    n = name.strip()
    for v, _, _ in CHILD_SCHEDULE:
        if n.lower().replace(" ", "") == v.lower().replace(" ", ""):
            return v
    if n.lower() in ("td-1", "td1", "tt1", "tt-1"):
        return "Td-1"
    if n.lower() in ("td-2", "td2", "tt2", "tt-2"):
        return "Td-2"
    if n.lower() in ("td-booster", "tt booster", "td booster"):
        return "Td-Booster"
    return n


def vaccines_due(rec: dict, today: date) -> tuple[list[str], date | None]:
    """Return (due/overdue now, date of next scheduled dose)."""
    b = lookup(rec.get("beneficiary_id")) or {}
    given = {canonical_vaccine(v["name"]) for v in b.get("vaccinations_given", [])}
    given |= {canonical_vaccine(v) for v in rec.get("vaccinations_given_today") or []}
    btype = rec.get("beneficiary_type")
    if btype in ("newborn", "child"):
        dob = _parse(b.get("dob")) or _parse(rec.get("context", {}).get("dob"))
        if not dob:
            return [], None
        age = (today - dob).days
        due = [v for v, at, last in CHILD_SCHEDULE if v not in given and at <= age and (last is None or age <= last)]
        upcoming = sorted({dob + timedelta(days=at) for v, at, _ in CHILD_SCHEDULE if v not in given and at > age})
        return due, (upcoming[0] if upcoming else None)
    if btype == "pregnant":
        if "Td-1" not in given and "Td-Booster" not in given:
            return ["Td-1"], None
        if "Td-1" in given and "Td-2" not in given and "Td-Booster" not in given:
            td1 = next((_parse(v.get("date")) for v in b.get("vaccinations_given", []) if canonical_vaccine(v["name"]) == "Td-1"), None)
            if td1 is None or "Td-1" in {canonical_vaccine(v) for v in rec.get("vaccinations_given_today") or []}:
                return [], today + timedelta(days=28)
            if today >= td1 + timedelta(days=28):
                return ["Td-2"], None
            return [], td1 + timedelta(days=28)
    return [], None


HBNC_DAYS = [1, 3, 7, 14, 21, 28, 42]


def suggest_next_visit(rec: dict, today: date) -> date | None:
    b = lookup(rec.get("beneficiary_id")) or {}
    btype = rec.get("beneficiary_type")
    if btype in ("newborn", "postpartum"):
        base = _parse(b.get("dob")) or _parse(b.get("delivery_date"))
        if base:
            for d in HBNC_DAYS:
                if base + timedelta(days=d) > today:
                    return base + timedelta(days=d)
        return today + timedelta(days=7)
    if btype == "child":
        _, nxt = vaccines_due(rec, today)
        return nxt or today + timedelta(days=30)
    if btype == "pregnant":
        lmp = _parse(b.get("lmp"))
        if lmp and (today - lmp).days >= 36 * 7:
            return today + timedelta(days=7)
        return today + timedelta(days=30)
    return None


# ---------------------------------------------------------------------------
# Applying updates
# ---------------------------------------------------------------------------

_TYPE_TO_VISIT = {"pregnant": "ANC", "postpartum": "PNC", "newborn": "HBNC", "child": "Immunisation"}
_VISIT_TO_TYPE = {v: k for k, v in _TYPE_TO_VISIT.items()}


def _coerce(name: str, value):
    typ = EDITABLE[name]
    if value is None or value == "":
        return [] if typ is list else None
    if typ is list:
        if isinstance(value, str):
            value = [v for v in re.split(r"[,;]", value) if v.strip()]
        return [canonical_vaccine(str(v)) for v in value]
    if typ is float:
        return round(float(str(value).replace(",", ".")), 2)
    if typ is int:
        if isinstance(value, bool) or str(value).strip().lower() in ("false", "true", "no", "none", "nahi", "नहीं"):
            return int(value is True or str(value).strip().lower() == "true")  # "IFA नहीं ली" -> 0 tablets
        return int(round(float(value)))
    return str(value).strip()


def attach_beneficiary(rec: dict, b: dict) -> None:
    rec["beneficiary_id"] = b["id"]
    rec["beneficiary_name"] = b["name"]
    rec["beneficiary_type"] = b["type"]
    rec["registered"] = True
    ctx = {"village": b.get("village")}
    for k in ("lmp", "dob", "delivery_date", "birth_weight_kg"):
        if b.get(k):
            ctx[k] = b[k]
    rec["context"] = ctx
    if not rec.get("visit_type"):
        rec["visit_type"] = _TYPE_TO_VISIT[b["type"]]


# Values outside these are physically impossible (a mishearing): never stored, even if "confirmed".
# Temperatures allow both scales because a unit slip (98.4 said as C) is fixed later in recompute().
HARD_LIMITS = {"weight_kg": (0.3, 250), "bp_systolic": (40, 300), "bp_diastolic": (20, 200), "hb_g_dl": (1.0, 25.0),
               "temperature_c": (30.0, 113.0), "temperature_f": (30.0, 113.0), "respiratory_rate": (4, 150),
               "ifa_tablets_given": (0, 400)}


def apply_updates(rec: dict, updates: dict, rejected: list | None = None) -> list[str]:
    """Merge field updates. Returns list of changed field names; impossible values go to `rejected`."""
    changed = []
    updates = dict(updates or {})
    if "bp_systolic" in updates or "bp_diastolic" in updates:  # BP is one reading: both halves or neither
        def _ok(k):
            try:
                v = updates.get(k)
                return v is None or HARD_LIMITS[k][0] <= float(v) <= HARD_LIMITS[k][1]
            except (TypeError, ValueError):
                return False
        if not (_ok("bp_systolic") and _ok("bp_diastolic")):
            if rejected is not None:
                rejected.append(("bp", f"{updates.get('bp_systolic', '?')}/{updates.get('bp_diastolic', '?')}"))
            updates.pop("bp_systolic", None)
            updates.pop("bp_diastolic", None)
    for k, v in updates.items():
        if k not in EDITABLE:
            continue
        try:
            nv = _coerce(k, v)
        except (TypeError, ValueError):
            continue
        lim = HARD_LIMITS.get(k)
        if k == "weight_kg":  # who she is decides what is physically possible
            bt = updates.get("beneficiary_type") or rec.get("beneficiary_type")
            lim = {"pregnant": (25, 200), "postpartum": (25, 200), "newborn": (0.3, 8), "child": (1, 45)}.get(bt, lim)
        if lim and nv is not None and not (lim[0] <= nv <= lim[1]):
            if rejected is not None:
                rejected.append((k, nv))
            continue
        if k == "visit_type" and nv:
            nv = next((t for t in VISIT_TYPES if t.lower() == nv.lower()), "Other")
        if k == "beneficiary_type" and nv not in BENEFICIARY_TYPES:
            continue
        if k == "beneficiary_id" and nv:
            nv = re.sub(r"\D", "", nv.translate(_DEVANAGARI_DIGITS)) or nv  # "1 0 2 5" -> "1025"
        if k in ("temperature_c", "temperature_f") and nv is not None:
            rec["temperature_f" if k == "temperature_c" else "temperature_c"] = None  # one unit at a time
        if k == "vaccinations_given_today":
            merged = list(dict.fromkeys((rec.get(k) or []) + nv)) if nv else []
            nv = merged
        if rec.get(k) != nv:
            rec[k] = nv
            changed.append(k)
    if "beneficiary_id" in changed:
        b = lookup(rec["beneficiary_id"])
        if b:
            attach_beneficiary(rec, b)
        else:
            rec["registered"] = False
    if rec.get("beneficiary_type") and not rec.get("visit_type"):
        rec["visit_type"] = _TYPE_TO_VISIT[rec["beneficiary_type"]]
    # New (unregistered) beneficiary: the visit type tells us who she is, so danger thresholds still apply.
    if (not rec.get("registered") and rec.get("visit_type") in _VISIT_TO_TYPE and not rec.get("beneficiary_type")
            and "beneficiary_type" not in (updates or {})):
        rec["beneficiary_type"] = _VISIT_TO_TYPE[rec["visit_type"]]
    return changed


def recompute(rec: dict, today: date) -> None:
    """Derived fields + validation warnings. Call after every update."""
    # unit slips: "temperature_c 98.4" is Fahrenheit, "temperature_f 37.2" is Celsius
    if rec.get("temperature_c") is not None and rec["temperature_c"] > 50:
        rec["temperature_f"], rec["temperature_c"] = rec["temperature_c"], None
    if rec.get("temperature_f") is not None and rec["temperature_f"] < 50:
        rec["temperature_c"], rec["temperature_f"] = rec["temperature_f"], None
    ctx = rec.setdefault("context", {})
    if not rec.get("registered"):  # a new beneficiary: dictated age / months of pregnancy stand in for the register
        if rec.get("gestation_months") and not ctx.get("lmp"):
            ctx["lmp"] = (today - timedelta(days=round(float(rec["gestation_months"]) * 30.44))).isoformat()
            rec["beneficiary_type"] = rec.get("beneficiary_type") or "pregnant"
        if rec.get("age_days") is not None and not ctx.get("dob"):
            ctx["dob"] = (today - timedelta(days=int(rec["age_days"]))).isoformat()
            rec["beneficiary_type"] = rec.get("beneficiary_type") or ("newborn" if int(rec["age_days"]) <= 28 else "child")
    due, _ = vaccines_due(rec, today)
    noted = [v for v in (rec.get("vaccinations_due_noted") or []) if v not in (rec.get("vaccinations_given_today") or [])]
    if noted and not rec.get("registered"):
        due = noted  # no vaccination history for a new beneficiary: what she says is due beats a schedule guess
    else:
        due = due + [v for v in noted if v not in due]
    rec["vaccinations_due"] = due
    sug = suggest_next_visit(rec, today)
    rec["next_visit_suggested"] = sug.isoformat() if sug else None
    ctx = rec.setdefault("context", {})
    lmp = _parse(ctx.get("lmp"))
    if lmp:
        ctx["gestation_weeks"] = (today - lmp).days // 7
    dob = _parse(ctx.get("dob"))
    if dob:
        ctx["age_days"] = (today - dob).days

    warnings = []
    for f, rng in RANGES.items():
        v = rec.get(f)
        if v is None:
            continue
        lo, hi = rng.get(rec.get("beneficiary_type"), rng[None]) if isinstance(rng, dict) else rng
        if not (lo <= v <= hi):
            warnings.append(f"{f}={v} looks unusual (expected {lo}-{hi}); please confirm")
    s, d = rec.get("bp_systolic"), rec.get("bp_diastolic")
    if s and d and s <= d:
        warnings.append(f"BP {s}/{d}: the first number is usually the bigger one; please confirm (e.g. {d}/{s})")
    # An id that is not in the demo register is simply a new beneficiary: no warning, no nagging.
    nv = _parse(rec.get("next_visit_date"))
    if rec.get("next_visit_date") and (nv is None or nv < today):
        warnings.append(f"next_visit_date {rec['next_visit_date']} is invalid or in the past")
    rec["warnings"] = warnings
    checks = []
    for f, rng in RANGES.items():
        v = rec.get(f)
        if v is None:
            continue
        lo, hi = rng.get(rec.get("beneficiary_type"), rng[None]) if isinstance(rng, dict) else rng
        if not (lo <= v <= hi):
            checks.append({"field": f, "value": v, "lo": lo, "hi": hi, "kind": "range"})
    if s and d and s <= d:
        checks.append({"field": "bp", "value": f"{s}/{d}", "kind": "bp_order"})
    rec["checks"] = checks


def missing_required(rec: dict) -> list[str]:
    return [f for f in REQUIRED_TO_SAVE if not rec.get(f)]


def followups(rec: dict) -> list[dict]:
    """Next actions for the ASHA worker, most urgent first."""
    items = []
    if rec.get("referral_required"):
        names = ", ".join(d["label_en"] for d in rec["danger_signs"])
        items.append({"priority": "urgent", "action": f"Refer to PHC now: {names}", "due": rec["visit_date"]})
    if rec.get("vaccinations_due"):
        items.append({"priority": "high", "action": "Vaccines due: " + ", ".join(rec["vaccinations_due"]),
                      "due": rec.get("next_visit_date") or rec.get("next_visit_suggested")})
    nxt = rec.get("next_visit_date") or rec.get("next_visit_suggested")
    if nxt:
        items.append({"priority": "normal", "action": f"Next home visit ({rec.get('visit_type') or 'visit'})", "due": nxt})
    for w in rec.get("warnings", []):
        items.append({"priority": "check", "action": "Verify: " + w, "due": rec["visit_date"]})
    return items
