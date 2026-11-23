"""Synthetic training / test corpus for the local fast path (server/fastpath.py).

Generates thousands of varied ASHA utterances with the exact update they must produce, then measures:
  coverage  = share handled locally (no LLM call)
  precision = share of locally handled ones that are EXACTLY right (must stay 100%)
and lists misses / errors so patterns can be widened. "must_llm" items (danger signs, questions, unclear speech)
must never be handled locally in strict mode.

    PYTHONPATH=. python eval/fastpath_corpus.py            # summary + top misses
    PYTHONPATH=. python eval/fastpath_corpus.py --show 40  # more examples
"""
from __future__ import annotations

import argparse
import itertools
import random
from collections import Counter, defaultdict
from datetime import date, timedelta

from server import fastpath, record

TODAY = date(2026, 11, 18)
R = random.Random(7)

HI_NUM = {1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पाँच", 6: "छह", 7: "सात", 8: "आठ", 9: "नौ", 10: "दस", 12: "बारह",
          15: "पंद्रह", 20: "बीस", 30: "तीस", 40: "चालीस", 50: "पचास", 56: "छप्पन", 58: "अट्ठावन", 60: "साठ"}

NAMES = [("1001", "Sunita Devi", "सुनीता देवी"), ("1002", "Rekha Kumari", "रेखा कुमारी"), ("1007", "Lakshmi Naidu", "लक्ष्मी नायडू"),
         ("1009", "Baby of Fatima Bano", "फ़ातिमा बानो का बच्चा"), ("2001", "Aarav Singh", "आरव सिंह")]


def num_forms(v):
    """Ways a number appears in a transcript: digits mostly, sometimes Hindi words."""
    out = [str(v)]
    if isinstance(v, int) and v in HI_NUM:
        out.append(HI_NUM[v])
    return out


# ---------------------------------------------------------------------------------------------- generators
def g_id():
    for bid, en, hi in NAMES:
        for tpl in ["ID {id}", "आईडी {id}", "नंबर {id} है", "ID है {id}", "उसका ID {id} है", "beneficiary ID {id}",
                    "{en}, ID {id}", "{hi}, ID {id}", "{hi} का visit, ID {id}", "{en} ka visit, ID {id}",
                    "लाभार्थी नंबर {id}", "id number {id}", "तो नंबर है {id}", "{hi} नंबर {id}", "ID {id} वाली {hi}",
                    "registration number {id}", "beneficiary number {id}", "ID {id} है"]:
            yield tpl.format(id=bid, en=en, hi=hi), {"beneficiary_id": bid}


def g_visit():
    vt = {"ANC": ["ANC", "anc"], "PNC": ["PNC"], "HBNC": ["HBNC"], "Immunisation": ["टीकाकरण", "immunisation", "Immunization"]}
    for v, words in vt.items():
        for w in words:
            for tpl in ["{w} visit", "{w} विज़िट", "आज {w} visit है", "{w} का visit", "visit का reason है {w}",
                        "ये {w} visit है", "{w} checkup", "visit type {w}", "{w} के लिए आई हूँ", "{w} visit के लिए"]:
                yield tpl.format(w=w), {"visit_type": v}
    for bid, en, hi in NAMES[:3]:
        for tpl in ["{hi} का ANC visit", "{en} ka ANC visit", "{hi}, ID {id}, ANC visit", "आज {hi} के घर ANC visit"]:
            yield tpl.format(id=bid, en=en, hi=hi), {"beneficiary_id": bid, "visit_type": "ANC"}


def g_weight():
    for w in [2.1, 2.8, 3.5, 50, 56, 58, 62.5]:
        for n in num_forms(w):
            for tpl in ["वज़न {n} किलो", "weight {n} kg", "वजन {n} kilo है", "उसका वज़न {n} किलो है", "{n} किलो वज़न",
                        "wajan {n} kilo", "weight is {n} kg", "बच्चे का वज़न {n} किलो", "वज़न {n} kg निकला",
                        "वज़न लिया, {n} किलो", "weight {n} kilo hai", "वज़न {n} किलो आया", "वज़न {n}", "weight {n}",
                        "आज वज़न {n} किलो", "वज़न है {n} किलो", "वज़न मापा {n} किलो", "{n} kg weight"]:
                yield tpl.format(n=n), {"weight_kg": w}


def g_bp():
    for s, d in [(110, 70), (120, 80), (130, 85), (150, 100)]:
        for tpl in ["BP {s} by {d}", "बीपी {s}/{d}", "BP {s} बटा {d}", "blood pressure {s} over {d}", "BP {s} पर {d}",
                    "उसका BP {s}/{d} है", "BP {s} by {d} था", "BP है {s}/{d}", "bp {s}/{d} hai", "BP {s} {d}",
                    "BP {s} और {d}", "BP नापा {s} by {d}", "ब्लड प्रेशर {s} बटा {d}", "BP {s}/{d} आया"]:
            yield tpl.format(s=s, d=d), {"bp_systolic": s, "bp_diastolic": d}


def g_hb():
    for h in [7, 8, 9.5, 10.5, 11]:
        for n in num_forms(h):
            for tpl in ["Hb {n}", "hemoglobin {n}", "हीमोग्लोबिन {n} है", "HB {n} gram", "Hb {n} आया", "hb {n} hai",
                        "Hb है {n}", "हीमोग्लोबिन {n} ग्राम", "Hb की जाँच {n}", "haemoglobin {n} g/dl", "Hb {n} निकला",
                        "उसका Hb {n} है", "एचबी {n}"]:
                yield tpl.format(n=n), {"hb_g_dl": h}


def g_temp():
    for t, key in [(98.4, "temperature_f"), (99.8, "temperature_f"), (97, "temperature_f"), (37.2, "temperature_c"), (36.5, "temperature_c")]:
        for tpl in ["temperature {t}", "तापमान {t}", "तापमान {t} डिग्री", "temp {t}", "{t} degree", "तापमान {t} है",
                    "body temperature {t}", "temperature {t} degree", "तापमान है {t}", "टेम्परेचर {t}", "temperature {t} आया",
                    "तापमान नापा {t}"]:
            yield tpl.format(t=t), {key: t}
    for tpl in ["temperature {t} F", "{t} degree Fahrenheit", "तापमान {t} फ़ारेनहाइट"]:
        yield tpl.format(t=98.6), {"temperature_f": 98.6}
    for tpl in ["temperature {t} C", "{t} degree celsius", "तापमान {t} सेल्सियस"]:
        yield tpl.format(t=37.5), {"temperature_c": 37.5}


def g_rr():
    for r in [12, 30, 40, 48]:
        for n in num_forms(r):
            for tpl in ["saans {n} per minute", "सांस {n} प्रति मिनट", "breathing {n}/min", "{n} breaths per minute",
                        "respiratory rate {n}", "सांस {n} है", "साँस {n} पर मिनट", "सांस की गिनती {n}", "saans {n} hai",
                        "एक मिनट में {n} सांस", "सांस गिनी, {n}", "breathing rate {n}", "{n} सांस प्रति मिनट"]:
                yield tpl.format(n=n), {"respiratory_rate": r}


def g_ifa():
    for k in [30, 60, 100]:
        for n in num_forms(k):
            for tpl in ["IFA की {n} गोली दी", "{n} IFA tablets दी", "IFA {n} tablets given", "आयरन की {n} गोली दी",
                        "IFA गोली {n} दी", "{n} गोलियाँ IFA की दीं", "IFA {n}", "{n} IFA गोली दी", "IFA की {n} गोलियाँ दीं",
                        "iron की {n} गोली", "IFA tablets {n}", "आयरन गोली {n}"]:
                yield tpl.format(n=n), {"ifa_tablets_given": k}
    for tpl in ["IFA की गोली नहीं दी", "IFA नहीं दी", "आज IFA नहीं दी", "IFA की गोली नहीं ली", "कोई IFA नहीं",
                "no IFA tablets given", "IFA गोली नहीं लेती"]:
        yield tpl, {"ifa_tablets_given": 0}


def g_vacc():
    for tpl, v in [("Td-2 लगा", ["Td-2"]), ("TT 2 injection दिया", ["Td-2"]), ("BCG और OPV-0 लगाया", ["BCG", "OPV-0"]),
                   ("Penta 1 लगा", ["Penta-1"]), ("आज Penta-1 दिया", ["Penta-1"]), ("टीटी 1 का टीका लगा", ["Td-1"]),
                   ("Td 1 दिया", ["Td-1"]), ("MR 1 का टीका लगा", ["MR-1"]), ("OPV 1, Penta 1 और RVV 1 दिया", ["OPV-1", "Penta-1", "RVV-1"]),
                   ("आज TT-2 लगाया", ["Td-2"]), ("Td-2 injection लगा दिया", ["Td-2"]), ("vaccine Td-2 given", ["Td-2"])]:
        yield tpl, {"vaccinations_given_today": sorted(v)}


def g_next():
    for n, unit, days in [(7, "दिन", 7), (15, "दिन", 15), (2, "हफ़्ते", 14), (1, "महीने", 30), (7, "days", 7), (2, "weeks", 14)]:
        d = (TODAY + timedelta(days=days)).isoformat()
        for tpl in ["अगली विज़िट {n} {u} बाद", "next visit after {n} {u}", "अगली बार {n} {u} बाद", "next visit {n} {u} later",
                    "अगला visit {n} {u} में", "{n} {u} बाद अगली विज़िट", "अगली विज़िट {n} {u} में", "फिर {n} {u} बाद आऊँगी"]:
            yield tpl.format(n=n, u=unit), {"next_visit_date": d}
    for tpl, d in [("next visit on 25 December", "2026-12-25"), ("अगली विज़िट 25 दिसंबर को", "2026-12-25"),
                   ("अगली विज़िट 5 तारीख को", "2026-12-05"), ("next visit 3 december", "2026-12-03")]:
        yield tpl, {"next_visit_date": d}


FINISH = ["बस", "save karo", "हो गया", "बस इतना ही", "रिकॉर्ड सेव कर दो", "that's all", "save it", "done", "विज़िट पूरी हुई",
          "बस, सेव कर दीजिए", "बस save करो", "हो गया save करो", "तो अब आप register कर दीजिए", "उसको दर्ज कर लीजिए", "इतना ही है",
          "बस हो गया दीदी", "ok save", "सेव कर दो", "सब बता दिया", "रिपोर्ट पूरी हो गई", "चलिए save कर दीजिए", "visit complete"]
CONFIRM = ["हाँ", "हाँ सही है", "जी हाँ", "सब सही है", "correct", "yes save it", "हाँ save कर दो", "बिल्कुल सही", "हाँ जी सही है",
           "yes", "हां ठीक है", "सही है", "हाँ दर्ज कर लीजिए", "हाँ, सब ठीक है", "yes correct", "जी, सही है"]
MUST_LLM = ["बच्चा दूध नहीं पी रहा", "उसको बहुत खून आ रहा है", "बच्चे को झटके आ रहे हैं", "saans lene mein taklif hai",
            "BP 150 by 100 है और तेज़ सिर दर्द", "ANC क्या होता है?", "बच्चे को कौन सी दवा दूँ?", "बुखार की दवाई बताओ",
            "visit है आपका, एक reason की वजह से", "उसको पीलिया है", "पेट में बहुत दर्द है", "बच्चा बहुत सुस्त है",
            "नाभि से मवाद आ रहा है", "शौर्य को आपकी guard चाहिए", "link list को कैसे solve करें?", "हाथ पैर में सूजन है",
            "बच्चा ठंडा है", "कल से बुखार है", "baby hil nahi raha", "वज़न 58 किलो, बच्चे को पीलिया है"]


def g_correction():
    for w in [55, 2.8]:
        for tpl in ["नहीं, वज़न {w} है", "नहीं नहीं, वज़न {w} किलो है", "no, weight is {w}", "गलत, वज़न {w} किलो", "नहीं weight {w} kg"]:
            yield tpl.format(w=w), {"weight_kg": w}
    for tpl in ["नहीं, BP 120/80 है", "गलत, BP 120 by 80", "no, BP is 120 over 80"]:
        yield tpl, {"bp_systolic": 120, "bp_diastolic": 80}
    for tpl in ["नहीं, Hb 10 है", "no hb 10"]:
        yield tpl, {"hb_g_dl": 10}


def g_multi():
    parts = [("वज़न 58 किलो", {"weight_kg": 58}), ("BP 120 by 80", {"bp_systolic": 120, "bp_diastolic": 80}),
             ("Hb 10.5", {"hb_g_dl": 10.5}), ("IFA की 30 गोली दी", {"ifa_tablets_given": 30}),
             ("तापमान 98.4", {"temperature_f": 98.4}), ("सांस 30 प्रति मिनट", {"respiratory_rate": 30}),
             ("weight 2.8 kg", {"weight_kg": 2.8}), ("Td-2 लगा", {"vaccinations_given_today": ["Td-2"]})]
    joins = [", ", " और ", " aur ", ". ", " and ", ", फिर "]
    for (a, ua), (b, ub) in itertools.permutations(parts, 2):
        if set(ua) & set(ub):
            continue
        for j in joins:
            yield f"{a}{j}{b}", {**ua, **ub}
    for (a, ua), (b, ub), (c, uc) in R.sample(list(itertools.permutations(parts, 3)), 60):
        if len(set(ua) | set(ub) | set(uc)) < len(ua) + len(ub) + len(uc):
            continue
        yield f"{a}, {b}, {c}", {**ua, **ub, **uc}


CATS = {"id": g_id, "visit": g_visit, "weight": g_weight, "bp": g_bp, "hb": g_hb, "temperature": g_temp, "breathing": g_rr,
        "ifa": g_ifa, "vaccines": g_vacc, "next_visit": g_next, "correction": g_correction, "multi": g_multi}


def run(show: int = 12):
    rec0 = record.new_record(TODAY)
    stats = defaultdict(Counter)
    misses, errors = defaultdict(list), []

    def check(cat, text, want, state="collecting", intent=None):
        p = fastpath.parse(text, rec0, state, "hi-IN")
        stats[cat]["total"] += 1
        if p is None:
            misses[cat].append(text)
            return
        stats[cat]["local"] += 1
        got_set = dict(p.set)
        if "vaccinations_given_today" in got_set:
            got_set["vaccinations_given_today"] = sorted(got_set["vaccinations_given_today"])
        ok = (intent is None or p.intent == intent) and (want is None or got_set == want)
        if ok:
            stats[cat]["right"] += 1
        else:
            errors.append((cat, text, want, p.intent, got_set))

    for cat, gen in CATS.items():
        for text, want in gen():
            check(cat, text, want)
    for text in FINISH:
        check("finish", text, {}, intent="finish")
    for text in CONFIRM:
        check("confirm", text, {}, state="confirming", intent="confirm_yes")
    for text in MUST_LLM:
        p = fastpath.parse(text, rec0, "collecting", "hi-IN")
        stats["must_llm"]["total"] += 1
        if p is None:
            stats["must_llm"]["right"] += 1
        else:
            errors.append(("must_llm", text, "LLM", p.intent, p.set))

    tot = Counter()
    print(f"{'category':12} {'total':>6} {'local':>6} {'coverage':>9} {'precision':>9}")
    for cat, c in stats.items():
        tot.update(c)
        if cat == "must_llm":
            print(f"{cat:12} {c['total']:6} {'':>6} {'':>9} {c['right'] / c['total']:9.0%}  (sent to LLM)")
            continue
        cov = c["local"] / c["total"]
        prec = c["right"] / c["local"] if c["local"] else 1
        print(f"{cat:12} {c['total']:6} {c['local']:6} {cov:9.0%} {prec:9.0%}")
    n = sum(c["total"] for k, c in stats.items() if k != "must_llm")
    loc = sum(c["local"] for k, c in stats.items() if k != "must_llm")
    right = sum(c["right"] for k, c in stats.items() if k != "must_llm")
    print(f"\nALL routine: {n} utterances, local {loc} ({loc / n:.1%}), exactly right {right}/{loc} "
          f"({right / max(loc, 1):.1%}); must-LLM kept: {stats['must_llm']['right']}/{stats['must_llm']['total']}")
    if errors:
        print(f"\n!! {len(errors)} WRONG local answers:")
        for e in errors[:show]:
            print("  ", e)
    for cat, lst in misses.items():
        if lst:
            print(f"\nmissed [{cat}] ({len(lst)}): " + " | ".join(lst[:show]))
    return loc / n, right == loc and stats["must_llm"]["right"] == stats["must_llm"]["total"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=12)
    run(ap.parse_args().show)
