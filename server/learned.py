"""Learn new phrasings from the LLM, so the next time they are understood locally (no API call).

When the LLM understands a sentence the local fast path could not, we store the sentence's *shape*:
numbers become <N0>, <N1>…, a register name becomes <NAME>, and we record which slot went into which field
("वज़न लिया <N0> किलो निकला" -> weight_kg = <N0>). After the LLM has produced the SAME mapping for a shape twice,
that shape is answered locally from then on.

Safety rules (a wrong local answer is worse than an LLM call):
  * only dictation / correction turns, in collecting state, with no danger sign, nothing unclear, no question;
  * never a sentence containing a danger keyword (negated or not);
  * every value must be traceable to a number in the sentence (or be a fixed visit / beneficiary type);
  * two consistent observations before use; any disagreement blocks that shape for good.
Stored in data/learned_patterns.json (text only, synthetic data).
"""
from __future__ import annotations

import json
import logging
import re
import threading

from . import config, danger, fastpath, numwords, record

log = logging.getLogger("learned")
PATH = config.DATA_DIR / "learned_patterns.json"
MIN_SEEN = 2
NUMERIC = {"weight_kg", "bp_systolic", "bp_diastolic", "hb_g_dl", "temperature_c", "temperature_f", "respiratory_rate",
           "ifa_tablets_given", "beneficiary_id"}
CONST = {"visit_type", "beneficiary_type"}
_lock = threading.Lock()
_db: dict | None = None


def _load() -> dict:
    global _db
    if _db is None:
        try:
            _db = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _db = {}
    return _db


def _save() -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(_db, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        log.exception("could not save learned patterns")


def shape(text: str) -> tuple[str, list[str], str | None]:
    """(template, numbers in order, register id named) for an utterance."""
    t = numwords.normalize(text)
    t = re.sub(r"(\d+)\s*(?:point|पॉइंट|प्वाइंट|दशमलव)\s*(\d{1,2})(?!\d)", r"\1.\2", t, flags=re.I).lower()
    who = None
    words, b = fastpath._names_words(t)
    if b:
        who = b["id"]
        for w in sorted(words, key=len, reverse=True):
            if len(w) > 2:
                t = re.sub(r"(?<![\wऀ-෿])" + re.escape(w.lower()) + r"(?![\wऀ-෿])", " <name> ", t)
    nums: list[str] = []

    def sub(m):
        nums.append(m.group(0))
        return f" <n{len(nums) - 1}> "
    t = re.sub(r"\d+(?:\.\d+)?", sub, t)
    t = re.sub(r"[,.।!?;:\"'()]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t, nums, who


def _num_eq(a: str, v) -> bool:
    try:
        return abs(float(a) - float(v)) < 1e-9
    except (TypeError, ValueError):
        return False


def learn(text: str, upd: dict, state: str, lang: str) -> None:
    """Record what the LLM did with a sentence the fast path could not handle."""
    intent = upd.get("intent")
    st = upd.get("set") or {}
    if (lang not in fastpath.LANGS or state != "collecting" or intent not in ("dictation", "correction") or not st
            or upd.get("danger_signs") or upd.get("unclear") or fastpath._QUESTION.search(text)
            or danger.detect_text(numwords.normalize(text), None)):
        return
    tpl, nums, who = shape(text)
    if not nums and not who:
        return
    mapping = {}
    for f, v in st.items():
        if f in NUMERIC:
            if f == "beneficiary_id" and who and str(v) == who:
                mapping[f] = ["name"]
                continue
            idx = [i for i, n in enumerate(nums) if _num_eq(n, v)]
            if len(idx) != 1:
                return  # value not (uniquely) in the sentence: the LLM inferred it, don't learn
            mapping[f] = ["num", idx[0]]
        elif f in CONST and isinstance(v, str):
            mapping[f] = ["const", v]
        else:
            return  # notes, dates, vaccines…: not learnable safely
    with _lock:
        db = _load()
        e = db.get(tpl)
        if e is None:
            db[tpl] = {"map": mapping, "intent": intent, "seen": 1, "blocked": False, "example": text}
        elif e["map"] == mapping and e["intent"] == intent:
            e["seen"] += 1
        else:
            e["blocked"] = True  # the LLM disagreed with itself: never use this shape locally
        _save()
    log.info("learned shape (%s): %s -> %s", db[tpl]["seen"], tpl, mapping)


def lookup(text: str, rec: dict, state: str, lang: str) -> "fastpath.Parsed | None":
    if lang not in fastpath.LANGS or state != "collecting" or fastpath._QUESTION.search(text):
        return None
    if danger.detect_text(numwords.normalize(text), None):
        return None
    tpl, nums, who = shape(text)
    e = _load().get(tpl)
    if not e or e.get("blocked") or e["seen"] < MIN_SEEN:
        return None
    out = {}
    for f, how in e["map"].items():
        if how[0] == "num":
            if how[1] >= len(nums):
                return None
            v = nums[how[1]]
            out[f] = v if f == "beneficiary_id" else (float(v) if "." in v else int(v))
        elif how[0] == "name":
            if not who:
                return None
            out[f] = who
        else:
            out[f] = how[1]
    return fastpath.Parsed(e["intent"], out)


def stats() -> dict:
    db = _load()
    return {"shapes": len(db), "active": sum(1 for e in db.values() if e["seen"] >= MIN_SEEN and not e["blocked"]),
            "blocked": sum(1 for e in db.values() if e["blocked"])}
