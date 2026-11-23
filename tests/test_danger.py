import pytest

from server import danger


def ids(text, btype=None):
    return sorted(h.sign_id for h in danger.detect_text(text, btype) if not h.negated)


@pytest.mark.parametrize("text,btype,expected", [
    ("Sunita ko kal se bleeding ho rahi hai", "pregnant", ["MAT_BLEEDING"]),
    ("सुनीता को खून आ रहा है", "pregnant", ["MAT_BLEEDING"]),
    ("baby ko tez bukhar hai", "newborn", ["NB_FEVER"]),
    ("बच्चा दूध नहीं पी रहा", "newborn", ["NB_NOT_FEEDING"]),
    ("bacche ko jhatke aa rahe hain", "newborn", ["NB_CONVULSION"]),
    ("pasli chal rahi hai", "newborn", ["NB_CHEST_INDRAWING", "NB_FAST_BREATHING"]),
    ("chehre par sujan hai aur tez sir dard", "pregnant", ["MAT_HEADACHE_VISION", "MAT_SWELLING"]),
    ("baccha hil nahi raha, halchal kam hai", "pregnant", ["MAT_FETAL_MOVEMENT"]),
    ("नाभि में मवाद है", "newborn", ["NB_UMBILICUS"]),
    ("haath pair thande hain", "newborn", ["NB_COLD"]),
    ("baby sust hai", "newborn", ["NB_LETHARGY"]),
    ("আজ রক্ত পড়ছে", "pregnant", ["MAT_BLEEDING"]),
    ("குழந்தைக்கு காய்ச்சல்", "newborn", ["NB_FEVER"]),
    ("bleeding hai bukhar nahi hai", "pregnant", ["MAT_BLEEDING"]),
])
def test_positive(text, btype, expected):
    assert ids(text, btype) == expected


@pytest.mark.parametrize("text,btype", [
    ("bleeding nahi hai", "pregnant"),
    ("कोई ब्लीडिंग नहीं", "pregnant"),
    ("no fever", "newborn"),
    ("BP 120 by 80, blood pressure normal", "pregnant"),
    ("खून की कमी की जांच हुई", "pregnant"),
    ("तापमान अट्ठानवे", "newborn"),
    ("weight 2.8 kilo, TT injection lag gaya", "pregnant"),
    ("saans normal hai", "newborn"),
    ("breathing 40 per minute", "newborn"),
    ("IFA goli di, ANC checkup ho gaya", "pregnant"),
])
def test_negative(text, btype):
    assert ids(text, btype) == []


def test_unknown_type_still_flags():
    assert ids("kal raat se bleeding", None) == ["MAT_BLEEDING"]


def test_thresholds_newborn():
    rec = {"beneficiary_type": "newborn", "temperature_f": 100.4, "respiratory_rate": 64, "weight_kg": 1.6}
    got = sorted(h.sign_id for h in danger.detect_thresholds(rec))
    assert got == ["NB_FAST_BREATHING", "NB_FEVER", "NB_LOW_WEIGHT"]


def test_thresholds_maternal():
    rec = {"beneficiary_type": "pregnant", "bp_systolic": 150, "bp_diastolic": 95, "hb_g_dl": 6.5}
    got = sorted(h.sign_id for h in danger.detect_thresholds(rec))
    assert got == ["MAT_HIGH_BP", "MAT_SEVERE_ANAEMIA"]


def test_normal_values_no_flags():
    assert danger.detect_thresholds({"beneficiary_type": "newborn", "temperature_c": 36.8, "weight_kg": 2.8}) == []
    assert danger.detect_thresholds({"beneficiary_type": "pregnant", "bp_systolic": 118, "bp_diastolic": 76}) == []


def test_escalation_every_language():
    for lang in danger.ESCALATION:
        msg = danger.escalation_text(["MAT_BLEEDING"], lang)
        assert "PHC" in msg


def test_guardrail():
    from server import llm
    assert llm.is_unsafe("Give paracetamol 500 mg")
    assert llm.is_unsafe("बच्चे को दवाई दे दीजिए")
    assert not llm.is_unsafe("वज़न दो दशमलव आठ किलो, नोट किया.")


@pytest.mark.parametrize("text,btype,expected", [
    ("breath hai", "postpartum", []),
    ("saans 40 per minute", "newborn", []),
    ("saans lene mein taklif hai", "pregnant", ["MAT_BREATHING"]),
    ("बच्चा तेज़ सांस ले रहा है", "newborn", ["NB_FAST_BREATHING"]),
    ("difficulty breathing since morning", "pregnant", ["MAT_BREATHING"]),
    ("सांस फूल रही है", "pregnant", ["MAT_BREATHING"]),
    ("सांस तेज़ चल रही है", "newborn", ["NB_FAST_BREATHING"]),
    ("saans bahut tez hai", "newborn", ["NB_FAST_BREATHING"]),
    ("breathing fast hai", "child", ["CH_BREATHING"]),
    ("saans tez nahi hai", "newborn", []),
])
def test_breathing_needs_difficulty(text, btype, expected):
    assert ids(text, btype) == expected


def test_reversed_bp_judged_by_larger_as_systolic():
    assert danger.detect_thresholds({"beneficiary_type": "pregnant", "bp_systolic": 80, "bp_diastolic": 120}) == []
    got = danger.detect_thresholds({"beneficiary_type": "pregnant", "bp_systolic": 100, "bp_diastolic": 150})
    assert [h.sign_id for h in got] == ["MAT_HIGH_BP"]


@pytest.mark.parametrize("text,btype,expected", [
    ("her feet and face are swollen", "pregnant", ["MAT_SWELLING"]),
    ("she had a fit", "pregnant", ["MAT_CONVULSION"]),
    ("baby movement has reduced", "pregnant", ["MAT_FETAL_MOVEMENT"]),
    ("baby is not moving", "pregnant", ["MAT_FETAL_MOVEMENT"]),
    ("paani ki thaili phat gayi", "pregnant", ["MAT_WATER_LEAK"]),
    ("pet mein bahut dard hai", "pregnant", ["MAT_ABDOMINAL_PAIN"]),
    ("baby is very cold", "newborn", ["NB_COLD"]),
    ("chest is pulling in", "newborn", ["NB_CHEST_INDRAWING"]),
    ("baby is very sleepy", "newborn", ["NB_LETHARGY"]),
    ("baby is yellow", "newborn", ["NB_JAUNDICE"]),
    ("pus from navel", "newborn", ["NB_UMBILICUS"]),
    ("low birth weight", "newborn", ["NB_LOW_WEIGHT"]),
    ("child cannot drink", "child", ["CH_UNABLE_TO_DRINK"]),
    ("face is fine, no swelling", "pregnant", []),
    ("pet mein dard nahi hai", "pregnant", []),
    ("baby drinks well", "newborn", []),
])
def test_judge_danger_phrasings(text, btype, expected):
    assert ids(text, btype) == expected


def test_adult_breathing_rate_thresholds():
    got = lambda rr: [h.sign_id for h in danger.detect_thresholds({"beneficiary_type": "pregnant", "respiratory_rate": rr})]
    assert got(5) == ["MAT_BREATHING"] and got(34) == ["MAT_BREATHING"] and got(18) == []
