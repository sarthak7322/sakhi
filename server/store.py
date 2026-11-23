"""Append-only local store for saved visit records (JSON lines). Synthetic data only."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime

from . import config, record

_lock = threading.Lock()


def save(rec: dict, asha_id: str, session_id: str | None = None) -> dict:
    out = {k: v for k, v in rec.items()}
    out["record_id"] = uuid.uuid4().hex[:10]
    out["asha_id"] = asha_id
    if session_id:
        out["session_id"] = session_id  # links the record to its voice log for the PDF report
    out["saved_at"] = datetime.now().isoformat(timespec="seconds")
    out["followups"] = record.followups(rec)
    config.DATA_DIR.mkdir(exist_ok=True)
    with _lock, open(config.RECORDS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    return out


def _norm_id(bid) -> str | None:
    return re.sub(r"\D", "", str(bid)) or None if bid else None


def all_records() -> list[dict]:
    if not config.RECORDS_FILE.exists():
        return []
    with open(config.RECORDS_FILE, encoding="utf-8") as f:
        recs = [json.loads(line) for line in f if line.strip()]
    for r in recs:  # older records stored spoken ids like "1 0 2 5"
        r["beneficiary_id"] = _norm_id(r.get("beneficiary_id"))
    return recs


def followup_list() -> list[dict]:
    """Open follow-ups: latest saved visit per beneficiary, urgent first, then by due date."""
    latest: dict[str, dict] = {}
    for r in all_records():
        latest[_norm_id(r.get("beneficiary_id")) or r["record_id"]] = r
    items = []
    for r in latest.values():
        for f in r.get("followups", []):
            if "is not in the register" in f.get("action", ""):
                continue  # older records: an unregistered ID is now a valid new beneficiary, not a problem
            items.append({**f, "beneficiary_id": _norm_id(r.get("beneficiary_id")), "beneficiary_name": r.get("beneficiary_name"),
                          "record_id": r["record_id"]})
    rank = {"urgent": 0, "high": 1, "normal": 2, "check": 3}
    items.sort(key=lambda i: (rank.get(i["priority"], 9), i.get("due") or "9999"))
    return items
