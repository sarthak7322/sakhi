"""Deterministic Hindi number-word -> digit normaliser, run on transcripts before the LLM.

LLMs misread Hindi number words surprisingly often ("अट्ठानवे" -> 88). Prisma's `transcribe` format usually
returns digits already, but noisy or code-mixed segments can still contain words, so we convert them in code.
Only runs that look numeric are converted (a run of >= 2 number words, or a number next to a unit or a
measurement word), so "दवा दो" (give medicine) is left alone.
"""
from __future__ import annotations

import re

_UNITS = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
    "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस "
    "सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास "
    "पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ "
    "सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी "
    "इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे "
    "पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()
NUM = {w: i for i, w in enumerate(_UNITS)}
NUM.update({"पांच": 5, "छः": 6, "छे": 6, "छह": 6, "पन्द्रह": 15, "उनासी": 79, "निन्यानबे": 99, "ज़ीरो": 0, "जीरो": 0,
            "zero": 0, "shunya": 0})
FRACTIONS = {"डेढ़": 1.5, "डेढ": 1.5, "ढाई": 2.5}
MODIFIERS = {"साढ़े": 0.5, "साढे": 0.5, "सवा": 0.25, "पौने": -0.25}
HUNDRED = {"सौ"}
POINT = {"दशमलव", "point", "पॉइंट", "प्वाइंट", "पॉईंट"}
UNIT_AFTER = {
    "किलो", "kilo", "kg", "ग्राम", "gram", "डिग्री", "degree", "फ़ारेनहाइट", "फारेनहाइट", "fahrenheit", "बटा", "by",
    "/", "हफ़्ते", "हफ्ते", "सप्ताह", "दिन", "महीने", "महीना", "साल", "गोली", "गोलियाँ", "गोलियां", "टैबलेट", "tablet",
    "प्रति", "per", "मिनट", "तारीख", "तारीख़", "सेंटीमीटर", "cm", "breaths", "breath", "सांस", "साँस", "saans", "ifa", "आईएफए",
    "आयरन", "iron", "हफ़्ते", "weeks", "days", "g/dl", "ग्राम", "gram",
}
MEASURE_BEFORE = {
    "वज़न", "वजन", "weight", "बीपी", "bp", "तापमान", "temperature", "temp", "बुखार", "आईडी", "id", "नंबर", "number",
    "हीमोग्लोबिन", "hemoglobin", "hb", "सांस", "साँस", "breathing", "ifa", "आईएफए", "बटा", "by", "/",
    "गोली", "goli", "गोलियाँ", "गोलियां", "tablet", "tablets", "saans", "sans", "haemoglobin", "एचबी", "आयरन", "iron",
    "rate", "गिनती", "जाँच", "जांच", "नापा", "मापा", "respiratory", "wajan", "vajan", "तापमान", "टेम्परेचर",
}
GLUE_WORDS = {"है", "hai", "था", "का", "की", "के", "मेरा", "मेरी", "उसका", "उसकी", "आज", "is", "was", "गिनी", "नापा", "मापा", "लिया"}
_TOKEN = re.compile(r"\S+")
_STRIP = "।,.?!;:\"'()"


def _val(word: str):
    return NUM.get(word, FRACTIONS.get(word))


_EN_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                          "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_EN_UNITS["oh"] = 0
_EN_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_EN_WORD = re.compile(r"\b(?:" + "|".join(sorted(list(_EN_UNITS) + list(_EN_TENS) + ["hundred", "point", "and", "half"],
                                                 key=len, reverse=True)) + r")\b", re.I)
_EN_CTX_BEFORE = {"weight", "bp", "pressure", "hb", "hemoglobin", "haemoglobin", "temperature", "temp", "id", "number",
                  "breathing", "rate", "ifa", "tablets", "over", "by", "is", "was", "after", "visit", "td", "tt", "penta",
                  "opv", "rvv", "mr", "pcv", "dose"}
_EN_CTX_AFTER = {"kg", "kilo", "kilos", "kilogram", "kilograms", "degree", "degrees", "over", "by", "tablets", "tablet",
                 "per", "days", "day", "weeks", "week", "months", "month", "breaths", "gram", "grams", "fahrenheit", "celsius"}


def _english_numbers(text: str) -> str:
    """'two point eight kilo' -> '2.8 kilo', 'one twenty over eighty' -> '120 over 80', 'one zero zero four' -> '1004'.
    Only runs in a numeric context, so 'one more thing' is left alone."""
    toks = text.split()
    out, i = [], 0
    low = lambda k: toks[k].lower().strip(",.।?!") if 0 <= k < len(toks) else ""
    while i < len(toks):
        w = low(i)
        if w not in _EN_UNITS and w not in _EN_TENS:
            out.append(toks[i]); i += 1; continue
        j, parts = i, []
        while j < len(toks) and (low(j) in _EN_UNITS or low(j) in _EN_TENS or low(j) == "hundred"
                                 or (low(j) == "and" and parts and low(j + 1) in _EN_UNITS | _EN_TENS.keys())):
            if low(j) != "and":
                parts.append(low(j))
            if toks[j][-1:] in ",.।?!" and low(j + 1) != "point":
                j += 1; break
            j += 1
        dec = ""
        if low(j) == "point" and (low(j + 1) in _EN_UNITS):
            k = j + 1
            while k < len(toks) and low(k) in _EN_UNITS and _EN_UNITS[low(k)] < 10:
                dec += str(_EN_UNITS[low(k)]); k += 1
            j = k
        vals = [("h" if p == "hundred" else _EN_TENS.get(p, _EN_UNITS.get(p))) for p in parts]
        # combine: tens+unit, X hundred Y, "one twenty" (BP style), digit strings (ids)
        if all(isinstance(v, int) and v < 10 for v in vals) and len(vals) > 1:
            num = "".join(str(v) for v in vals)
        else:
            total, cur, groups = 0, 0, []
            for v in vals:
                if v == "h":
                    cur = (cur or 1) * 100
                elif cur and cur % 100 == 0 and v < 100:
                    cur += v
                elif cur and cur < 10 and v >= 20 and v % 10 == 0:
                    cur = cur * 100 + v          # "one twenty" -> 120, "one forty" -> 140
                elif cur and cur >= 20 and cur % 10 == 0 and cur < 100 and v < 10:
                    cur += v                     # "twenty five"
                elif cur and cur % 100 >= 20 and cur % 10 == 0 and v < 10:
                    cur += v                     # "one twenty five" -> 125
                else:
                    if cur:
                        groups.append(cur)
                    cur = v
            groups.append(cur)
            num = " ".join(str(g) for g in groups)
        if dec:
            num = num + "." + dec
        prev, nxt = low(i - 1), low(j)
        ctx = len(parts) > 1 or dec or prev in _EN_CTX_BEFORE or nxt in _EN_CTX_AFTER or parts == ["half"]
        if ctx and num.replace(".", "").replace(" ", "").isdigit():
            out.append(num + (toks[j - 1][-1] if toks[j - 1][-1:] in ",.।?!" and not dec else ""))
        else:
            out.extend(toks[i:j])
        i = j
    return " ".join(out)


def normalize(text: str) -> str:
    text = _english_numbers(text)
    toks = _TOKEN.findall(text)
    out: list[str] = []
    i = 0
    while i < len(toks):
        core = toks[i].strip(_STRIP)
        trail = toks[i][len(toks[i].rstrip(_STRIP)):]
        mod = MODIFIERS.get(core)
        start = i
        if mod is not None and i + 1 < len(toks) and toks[i + 1].strip(_STRIP) in NUM:
            i += 1
            core = toks[i].strip(_STRIP)
            trail = toks[i][len(toks[i].rstrip(_STRIP)):]
        v = _val(core)
        if v is None:
            out.append(toks[start] if start == i else " ".join(toks[start:i + 1]))
            i += 1
            continue
        # collect a numeric run: digits sequence (ids), hundreds, decimals
        run_words = [core]
        digits = [v]
        j = i + 1
        decimal = ""
        value = v
        last_trail = trail
        while j < len(toks) and not last_trail:
            w = toks[j].strip(_STRIP)
            if w in HUNDRED and isinstance(value, int) and value < 10 and len(digits) == 1:
                value *= 100
                k = j + 1
                if k < len(toks) and toks[k].strip(_STRIP) in NUM:
                    value += NUM[toks[k].strip(_STRIP)]
                    last_trail = toks[k][len(toks[k].rstrip(_STRIP)):]
                    j = k + 1
                else:
                    last_trail = toks[j][len(toks[j].rstrip(_STRIP)):]
                    j += 1
                digits = [value]
                run_words.append(w)
                continue
            if w.lower() in POINT:
                k = j + 1
                while k < len(toks) and toks[k].strip(_STRIP) in NUM and NUM[toks[k].strip(_STRIP)] < 10:
                    decimal += str(NUM[toks[k].strip(_STRIP)])
                    last_trail = toks[k][len(toks[k].rstrip(_STRIP)):]
                    k += 1
                    if last_trail:
                        break
                if decimal:
                    run_words.append(w)
                    j = k
                break
            if w in NUM and NUM[w] < 10 and all(isinstance(d, int) and d < 10 for d in digits):
                digits.append(NUM[w])  # spoken digit sequence, e.g. an id "एक शून्य शून्य चार"
                run_words.append(w)
                last_trail = toks[j][len(toks[j].rstrip(_STRIP)):]
                j += 1
                continue
            break
        if len(digits) > 1:
            num = "".join(str(d) for d in digits)
        else:
            num_v = digits[0] + (mod or 0)
            num = (f"{num_v:g}" if isinstance(num_v, float) else str(num_v))
        if decimal:
            num = f"{num}.{decimal}"
        prev = toks[start - 1].strip(_STRIP).lower() if start > 0 else ""
        k = start - 1  # look back across glue words: "Hb है सात", "सांस की गिनती बारह"
        while k > 0 and toks[k].strip(_STRIP).lower() in GLUE_WORDS and start - k < 3:
            k -= 1
            if toks[k].strip(_STRIP).lower() in MEASURE_BEFORE:
                prev = toks[k].strip(_STRIP).lower()
                break
        nxt = toks[j].strip(_STRIP).lower() if j < len(toks) else ""
        numeric_context = (len(run_words) > 1 or mod is not None or core in FRACTIONS
                           or nxt in UNIT_AFTER or prev in MEASURE_BEFORE)
        if core == "दो" and len(run_words) == 1 and nxt not in UNIT_AFTER:
            numeric_context = False  # "goli दो" = "give the tablet"; only a following unit makes it the number 2
        if numeric_context:
            out.append(num + last_trail)
        else:
            out.extend(toks[start:j])
        i = j
    return " ".join(out)
