"""All saved sessions as one dataset, plus user bug reports ("feedback").

Every session writes a text-only voice log to data/voicelogs/<session>.jsonl (no audio). This module turns
them into one row per turn, the material for widening the local model, adding regression tests and finding
bugs:

    {session, turn, lang, source, text, heard (raw STT), handled_by (local|learned|llm|failed|none),
     intent, set, changed, record_after, replies, events, danger, ttfa_ms, llm_raw, feedback[]}

Feedback reported from the app (⚑ on a voice-log turn) is stored in data/feedback.jsonl and joined in.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime

from . import config

LOGS = config.DATA_DIR / "voicelogs"
FEEDBACK = config.DATA_DIR / "feedback.jsonl"
_lock = threading.Lock()


def add_feedback(item: dict) -> dict:
    sid = str(item.get("session") or "")
    if not re.fullmatch(r"[\w-]{1,40}", sid):
        raise ValueError("bad session id")
    row = {"at": datetime.now().isoformat(timespec="seconds"), "session": sid, "turn": int(item.get("turn") or 0),
           "problem": str(item.get("problem") or "")[:500], "expected": str(item.get("expected") or "")[:500],
           "kind": str(item.get("kind") or "wrong")[:40]}
    config.DATA_DIR.mkdir(exist_ok=True)
    with _lock, open(FEEDBACK, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def feedback() -> list[dict]:
    if not FEEDBACK.exists():
        return []
    return [json.loads(l) for l in FEEDBACK.read_text(encoding="utf-8").splitlines() if l.strip()]


def _session_rows(path, fb: dict) -> list[dict]:
    sid = path.stem
    entries = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    lang = next((e.get("lang") for e in entries if e["type"] == "ready"), None)
    asha = next((e.get("asha_id") for e in entries if e["type"] == "ready"), None)
    turns: dict[int, dict] = {}
    last_heard = None

    def T(i):
        return turns.setdefault(i, {"session": sid, "turn": i, "lang": lang, "asha_id": asha, "replies": [], "events": [],
                                    "danger": [], "heard": None})
    cur = 0
    for e in entries:
        k = e["type"]
        if k == "transcript":
            last_heard = {"text": e.get("text"), "lang": e.get("detected_lang"), "stt_ms": e.get("stt_latency_ms")}
        elif k == "user_turn":
            cur = e["turn"]
            t = T(cur)
            t.update(text=e.get("text"), source=e.get("source"), merged_from=e.get("merged_from"), t=e.get("t"))
            if e.get("source") == "voice":
                t["heard"] = last_heard
        elif k == "agent_text" and e.get("turn"):
            T(e["turn"])["replies"].append(e.get("text"))
        elif k == "event":
            T(e.get("turn") or cur)["events"].append({"kind": e.get("kind"), "text": e.get("text")})
        elif k == "danger":
            T(cur)["danger"] += e.get("signs") or []
        elif k == "turn_info":
            T(e["turn"]).update(intent=e.get("intent"), set=e.get("set"), changed=e.get("changed"),
                                unclear=e.get("unclear"), state_after=e.get("state"), record_after=e.get("record"))
        elif k == "metrics" and e.get("turn"):
            T(e["turn"]).update(ttfa_ms=e.get("ttfa_ms"), model=e.get("model"))
        elif k == "llm_raw":
            T(e["turn"]).update(llm_raw=e.get("raw"), state_before=e.get("state_before"))
        elif k == "error":
            T(cur)["events"].append({"kind": "error", "text": e.get("message")})
        elif k == "saved":
            T(cur)["saved"] = True
    rows = []
    for i, t in sorted(turns.items()):
        if i == 0 or not t.get("text"):
            continue
        kinds = {ev["kind"] for ev in t["events"]}
        model = t.get("model")
        t["handled_by"] = ("local" if model == "local" else "learned" if model == "learned" else
                           "failed" if ("error" in kinds or ("busy" in kinds and not t.get("intent"))) else
                           "llm" if model or t.get("llm_raw") else "none")
        t["feedback"] = fb.get((sid, i), [])
        rows.append(t)
    return rows


def build() -> list[dict]:
    fb: dict = {}
    for f in feedback():
        fb.setdefault((f["session"], f["turn"]), []).append({k: f[k] for k in ("problem", "expected", "kind", "at")})
    rows = []
    if LOGS.exists():
        for p in sorted(LOGS.glob("*.jsonl")):
            try:
                rows += _session_rows(p, fb)
            except (OSError, ValueError, KeyError):
                continue
    return rows


def summary(rows: list[dict] | None = None) -> dict:
    rows = build() if rows is None else rows
    by = {}
    for r in rows:
        by[r["handled_by"]] = by.get(r["handled_by"], 0) + 1
    return {"sessions": len({r["session"] for r in rows}), "turns": len(rows), "handled_by": by,
            "reports": sum(len(r["feedback"]) for r in rows),
            "local_share": round((by.get("local", 0) + by.get("learned", 0)) / max(len(rows), 1), 3)}
