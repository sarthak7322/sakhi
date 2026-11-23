from datetime import date

import pytest

from server import fastpath, record


def P(text, state="collecting", lang="hi-IN", rec=None):
    p = fastpath.parse(text, rec or record.new_record(date(2026, 11, 18)), state, lang)
    return (p.intent, p.set) if p else None


@pytest.mark.parametrize("text,want", [
    ("सुनीता देवी, ID 1001, ANC visit", ("dictation", {"beneficiary_id": "1001", "visit_type": "ANC"})),
    ("तो नंबर है 1007।", ("dictation", {"beneficiary_id": "1007"})),
    ("वज़न 58 किलो, BP 120 by 80", ("dictation", {"weight_kg": 58, "bp_systolic": 120, "bp_diastolic": 80})),
    ("Hb 10.5, IFA की 30 गोली दी", ("dictation", {"hb_g_dl": 10.5, "ifa_tablets_given": 30})),
    ("hemoglobin है मेरा 8", ("dictation", {"hb_g_dl": 8})),
    ("साँस 12 per minute है।", ("dictation", {"respiratory_rate": 12})),
    ("temperature 99.8", ("dictation", {"temperature_f": 99.8})),
    ("तापमान 37.2 डिग्री", ("dictation", {"temperature_c": 37.2})),
    ("नहीं नहीं, वज़न 56 किलो है", ("correction", {"weight_kg": 56})),
    ("वज़न अट्ठावन किलो", ("dictation", {"weight_kg": 58})),
    ("आज Penta-1, OPV-1 और RVV-1 लगा", ("dictation", {"vaccinations_given_today": ["OPV-1", "Penta-1", "RVV-1"]})),
    ("TT 2 का टीका आज लगा", ("dictation", {"vaccinations_given_today": ["Td-2"]})),
    ("बस, save करो", ("finish", {})),
    ("That is all, save it", ("finish", {})),
    ("Weight 58 kilo, BP 120 by 80", ("dictation", {"weight_kg": 58, "bp_systolic": 120, "bp_diastolic": 80})),
])
def test_understood_locally(text, want):
    assert P(text) == want


@pytest.mark.parametrize("text", [
    "बच्चा दूध नहीं पी रहा",            # danger sign: LLM is the second detector
    "BP 150 by 100 है और तेज़ सिर दर्द",
    "saans lene mein taklif hai",
    "बुखार नहीं था पर अब है",              # negation that is not a simple "none"
    "ANC क्या होता है?",                 # question
    "visit है आपका, एक reason की वजह से",  # unclear phrasing
    "वज़न 58 किलो, बच्चे को पीलिया है",     # value + danger in one breath
])
def test_handed_to_llm(text):
    assert P(text) is None


def test_confirming_yes_and_correction():
    assert P("हाँ सही है", "confirming") == ("confirm_yes", {})
    assert P("Yes, correct", "confirming", "en-IN") == ("confirm_yes", {})
    assert P("नहीं, वज़न 55 है", "confirming") == ("correction", {"weight_kg": 55})
    assert P("नहीं सही नहीं है", "confirming") is None


def test_other_languages_use_llm():
    assert P("वज़न 58 किलो", lang="mr-IN") is None


def test_id_change_mid_visit_goes_to_llm():
    rec = record.new_record(date(2026, 11, 18))
    record.apply_updates(rec, {"beneficiary_id": "1001"})
    assert P("ID 1002", rec=rec) is None


def test_readback_has_values_and_question():
    rec = record.new_record(date(2026, 11, 18))
    record.apply_updates(rec, {"beneficiary_id": "1001", "weight_kg": 56, "bp_systolic": 120, "bp_diastolic": 80})
    record.recompute(rec, date(2026, 11, 18))
    lines = fastpath.readback(rec, "hi-IN")
    text = " ".join(lines)
    assert "Sunita Devi, ID 1001" in text and "वज़न 56 किलो" in text and "BP 120/80" in text and lines[-1] == "सब सही है?"



@pytest.mark.parametrize("text,pending,want", [
    ("वज़न है।", None, ("other", {})),
    ("50 किलो।", "weight_kg", ("dictation", {"weight_kg": 50})),
    ("8", "hb_g_dl", ("dictation", {"hb_g_dl": 8})),
    ("120 by 80", "bp", ("dictation", {"bp_systolic": 120, "bp_diastolic": 80})),
    ("1025", "beneficiary_id", ("dictation", {"beneficiary_id": "1025"})),
    ("97 degree Fahrenheit।", None, ("dictation", {"temperature_f": 97.0})),
    ("saans 40 hai", None, ("dictation", {"respiratory_rate": 40})),
    ("मैं कोई भी IFA की गोली नहीं लेती हूँ।", None, ("dictation", {"ifa_tablets_given": 0})),
    ("आज टीके नहीं लगे।", None, ("dictation", {})),
    ("अगली विज़िट 15 दिन बाद", None, ("dictation", {"next_visit_date": "2026-12-03"})),
    ("अगली विज़िट 25 दिसंबर को", None, ("dictation", {"next_visit_date": "2026-12-25"})),
    ("उसका नाम रीना देवी है, गर्भवती है", None, ("dictation", {"beneficiary_name": "रीना देवी", "beneficiary_type": "pregnant"})),
    ("हाँ", None, ("other", {})),
    ("नमस्ते सखी", None, ("other", {})),
])
def test_basic_things_local(text, pending, want):
    p = fastpath.parse(text, record.new_record(date(2026, 11, 18)), "collecting", "hi-IN", pending=pending)
    assert p is not None and (p.intent, p.set) == want


def test_label_only_asks_and_remembers():
    p = fastpath.parse("hemoglobin है मेरा", record.new_record(date(2026, 11, 18)), "collecting", "hi-IN")
    assert p.ask == "hb_g_dl" and p.say == ["Hb कितना है?"]


def test_bare_number_without_pending_goes_to_llm():
    assert P("8") is None


def test_danger_negation_still_llm():
    assert P("बुखार नहीं है") is None and P("बच्चे को बुखार है") is None


def test_bare_yes_while_dictating_is_not_a_save():
    assert P("हाँ")[0] == "other"


@pytest.mark.parametrize("text,want", [
    ("Patient name is Geeta Sharma, ID 3021", {"beneficiary_id": "3021", "beneficiary_name": "Geeta Sharma"}),
    ("This is Meera, beneficiary ID 4410, ANC checkup", {"beneficiary_id": "4410", "beneficiary_name": "Meera", "visit_type": "ANC"}),
    ("Her weight is 54 kilos", {"weight_kg": 54}),
    ("Weight two point eight kilo", {"weight_kg": 2.8}),
    ("BP is one twenty over eighty", {"bp_systolic": 120, "bp_diastolic": 80}),
    ("TT injection lag gaya", {"vaccinations_given_today": ["Td-1"]}),
    ("ANC checkup ho gaya", {"visit_type": "ANC"}),
    ("TT2 due hai", {"vaccinations_due_noted": ["Td-2"]}),
    ("vaccinations due Penta 2 and OPV 2", {"vaccinations_due_noted": ["OPV-2", "Penta-2"]}),
    ("Td-1 लगा, Td-2 due है", {"vaccinations_given_today": ["Td-1"], "vaccinations_due_noted": ["Td-2"]}),
    ("BCG लगा और OPV-1 बाकी है", {"vaccinations_given_today": ["BCG"], "vaccinations_due_noted": ["OPV-1"]}),
    ("7 महीने की गर्भवती है", {"gestation_months": 7.0, "beneficiary_type": "pregnant"}),
    ("baby is 10 days old", {"age_days": 10}),
    ("next visit after two weeks", {"next_visit_date": "2026-12-02"}),
])
def test_judge_style_lines(text, want):
    p = fastpath.parse(text, record.new_record(date(2026, 11, 18)), "collecting", "en-IN")
    assert p is not None and p.set == want


def test_judge_confirmations():
    assert P("that's all, read it back", lang="en-IN")[0] == "finish"
    assert P("yes that's correct", "confirming", "en-IN")[0] == "confirm_yes"


def test_session_bugs_round3():
    rec = record.new_record(date(2026, 11, 18))
    # BP top number only -> keep it, ask for the bottom one
    p = fastpath.parse("BP है 180", rec, "collecting", "hi-IN")
    assert p.set == {"bp_systolic": 180} and p.ask == "bp_diastolic" and "नीचे" in p.say[0]
    assert fastpath.parse("90", rec, "collecting", "hi-IN", pending="bp_diastolic").set == {"bp_diastolic": 90}
    # IFA taken without a count -> no invented number
    p = fastpath.parse("IFA goli ले ली है", rec, "collecting", "hi-IN")
    assert "ifa_tablets_given" not in p.set and p.ask == "ifa_tablets_given"
    # save phrasing with fillers while confirming
    assert fastpath.parse("तो safe तो कीजिए, register कर लीजिए", rec, "confirming", "hi-IN").intent == "confirm_yes"
    # what is left
    p = fastpath.parse("और क्या बताना है?", rec, "collecting", "hi-IN")
    assert p.intent == "other" and "बाकी" in p.say[0]
