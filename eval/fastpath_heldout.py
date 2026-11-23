"""Held-out check for the local fast path: phrasings NOT used while widening the patterns.

Precision is what matters: a wrong local answer is worse than an LLM call. Run:
    PYTHONPATH=. python eval/fastpath_heldout.py
"""
from datetime import date

from server import fastpath, record

TODAY = date(2026, 11, 18)
ROUTINE = [
    ("आज सुनीता जी का ANC है, आईडी 1001", {"beneficiary_id": "1001", "visit_type": "ANC"}),
    ("रेखा कुमारी, 1002 नंबर", {"beneficiary_id": "1002"}),
    ("weight aaj 57 kilo aaya", {"weight_kg": 57}),
    ("बच्चे का वजन तीन किलो है", {"weight_kg": 3}),
    ("BP 118 by 76 tha", {"bp_systolic": 118, "bp_diastolic": 76}),
    ("बीपी एक सौ बीस बटा अस्सी", {"bp_systolic": 120, "bp_diastolic": 80}),
    ("hemoglobin 9 point 8", {"hb_g_dl": 9.8}),
    ("Hb नौ दशमलव पाँच", {"hb_g_dl": 9.5}),
    ("temperature अट्ठानवे point चार", {"temperature_f": 98.4}),
    ("बुखार नहीं है, तापमान 98", None),  # mentions fever (negated): LLM or local both fine, must not be wrong
    ("saans 44 per minute", {"respiratory_rate": 44}),
    ("ek minute mein 38 saans", {"respiratory_rate": 38}),
    ("IFA ki 30 goli di", {"ifa_tablets_given": 30}),
    ("iron ki goli 60 di hai", {"ifa_tablets_given": 60}),
    ("TT-1 injection lagaya aaj", {"vaccinations_given_today": ["Td-1"]}),
    ("BCG, OPV 0 aur Hep B 0 lagaya", {"vaccinations_given_today": ["BCG", "HepB-0", "OPV-0"]}),
    ("agli visit 10 din baad", {"next_visit_date": "2026-11-28"}),
    ("next visit 3 hafte baad", {"next_visit_date": "2026-12-09"}),
    ("नहीं, वजन 2.6 किलो है", {"weight_kg": 2.6}),
    ("galat, BP 124 by 82", {"bp_systolic": 124, "bp_diastolic": 82}),
    ("वज़न 61, BP 130/84, Hb 11.2", {"weight_kg": 61, "bp_systolic": 130, "bp_diastolic": 84, "hb_g_dl": 11.2}),
    ("weight 2.9 kg aur temperature 98.6", {"weight_kg": 2.9, "temperature_f": 98.6}),
]
FINISH = ["bas itna hi, save kar do", "ho gaya didi save karo", "सारी जानकारी दे दी, सेव कीजिए", "record save karo"]
CONFIRM = ["haan sab sahi hai", "हाँ बिल्कुल", "ji haan save karo", "yes it is correct"]
MUST_LLM = [
    "वज़न 58 किलो नहीं 56 है शायद", "BP 160 by 110 है और सिर में बहुत दर्द", "Hb पता नहीं चला", "दो दिन से बुखार है",
    "बच्चे का वज़न कम है 1.6 किलो और दूध नहीं पीता", "उसे खून की उल्टी हुई", "pregnant hai aur bleeding ho rahi hai",
    "बच्चे की आँखें पीली हैं", "उसको क्या खिलाऊँ?", "पिछली बार का वज़न क्या था?", "baby ki pasli chal rahi hai",
    "उसको चक्कर आ रहे हैं और धुंधला दिख रहा है", "kal raat se jhatke aa rahe hain", "उसकी तबीयत ठीक नहीं है",
    "doctor ko dikhana chahiye kya", "मैं कल फिर आऊँगी, वो घर पर नहीं थी", "बच्चा रो रहा है लगातार",
]


def main():
    rec0 = record.new_record(TODAY)
    loc = right = 0
    wrong = []
    miss = []
    for text, want in ROUTINE:
        p = fastpath.parse(text, rec0, "collecting", "hi-IN")
        if p is None:
            miss.append(text)
            continue
        loc += 1
        got = dict(p.set)
        if "vaccinations_given_today" in got:
            got["vaccinations_given_today"] = sorted(got["vaccinations_given_today"])
        if want is None or got == want:
            right += 1
        else:
            wrong.append((text, want, got))
    for text in FINISH:
        p = fastpath.parse(text, rec0, "collecting", "hi-IN")
        loc += p is not None; right += bool(p and p.intent == "finish")
        if p is None: miss.append(text)
        elif p.intent != "finish": wrong.append((text, "finish", p.intent))
    for text in CONFIRM:
        p = fastpath.parse(text, rec0, "confirming", "hi-IN")
        loc += p is not None; right += bool(p and p.intent == "confirm_yes")
        if p is None: miss.append(text)
        elif p.intent != "confirm_yes": wrong.append((text, "confirm_yes", p.intent))
    leaked = [t for t in MUST_LLM if fastpath.parse(t, rec0, "collecting", "hi-IN") is not None]
    n = len(ROUTINE) + len(FINISH) + len(CONFIRM)
    print(f"held-out routine: {n}, local {loc} ({loc / n:.0%}), exactly right {right}/{loc}")
    print(f"must-LLM: {len(MUST_LLM) - len(leaked)}/{len(MUST_LLM)} sent to the LLM")
    for w in wrong:
        print("  WRONG:", w)
    for t in leaked:
        print("  LEAKED (handled locally, should not be):", t, fastpath.parse(t, rec0, "collecting", "hi-IN"))
    for t in miss:
        print("  miss:", t)


if __name__ == "__main__":
    main()
