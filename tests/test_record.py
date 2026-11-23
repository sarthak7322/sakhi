from datetime import date

from server import record

TODAY = date(2026, 11, 17)


def make(**updates):
    r = record.new_record(TODAY)
    record.apply_updates(r, updates)
    record.recompute(r, TODAY)
    return r


def test_register_attach_and_visit_type():
    r = make(beneficiary_id="1001")
    assert r["beneficiary_name"] == "Sunita Devi"
    assert r["beneficiary_type"] == "pregnant" and r["visit_type"] == "ANC"
    assert r["context"]["gestation_weeks"] == 30
    assert r["vaccinations_due"] == ["Td-2"]  # Td-1 given in June


def test_td2_cleared_when_given_today():
    r = make(beneficiary_id="1001", vaccinations_given_today=["TT2"])
    assert r["vaccinations_due"] == []


def test_child_due_6_week_vaccines():
    r = make(beneficiary_id="2001")  # DOB 2026-09-29, 49 days old
    assert r["vaccinations_due"] == ["OPV-1", "Penta-1", "RVV-1", "fIPV-1", "PCV-1"]


def test_child_9_month():
    r = make(beneficiary_id="2002")
    assert r["vaccinations_due"] == ["MR-1", "PCV-Booster", "fIPV-3", "VitA-1"]


def test_newborn_hbnc_next_visit():
    r = make(beneficiary_id="1004")  # DOB 2026-11-10 -> day 14 = 2026-11-24
    assert r["visit_type"] == "HBNC"
    assert r["next_visit_suggested"] == "2026-11-24"


def test_weight_range_warning():
    r = make(beneficiary_id="1004", weight_kg=28)
    assert any("weight_kg" in w for w in r["warnings"])
    r = make(beneficiary_id="1004", weight_kg="2,8")
    assert r["weight_kg"] == 2.8 and not r["warnings"]


def test_unknown_id_is_new_beneficiary_without_nagging():
    r = make(beneficiary_id="9999")
    assert not r["registered"] and r["warnings"] == []


def test_correction_overwrites():
    r = make(beneficiary_id="1004", weight_kg=2.5)
    changed = record.apply_updates(r, {"weight_kg": 2.8})
    assert changed == ["weight_kg"] and r["weight_kg"] == 2.8


def test_required_and_followups():
    r = record.new_record(TODAY)
    assert record.missing_required(r) == ["beneficiary_id", "visit_type"]
    r = make(beneficiary_id="2001", next_visit_date="2026-11-24")
    r["referral_required"] = True
    r["danger_signs"] = [{"label_en": "baby having fits"}]
    f = record.followups(r)
    assert f[0]["priority"] == "urgent" and "PHC" in f[0]["action"]
    assert f[1]["action"].startswith("Vaccines due")


def test_temperature_unit_slip():
    r = make(beneficiary_id="1004", temperature_c=98.4)
    assert r["temperature_f"] == 98.4 and r["temperature_c"] is None


def test_new_beneficiary_no_nag_and_type_from_visit():
    r = make(beneficiary_id="1 0 2 5", visit_type="PNC", hb_g_dl=6.5)
    assert r["beneficiary_id"] == "1025" and not r["registered"]
    assert r["beneficiary_type"] == "postpartum" and r["warnings"] == []


def test_bp_reversed_warns():
    r = make(beneficiary_id="1001", bp_systolic=80, bp_diastolic=120)
    assert any("usually the bigger" in w for w in r["warnings"])


def test_temperature_one_unit():
    r = make(beneficiary_id="1004", temperature_c=36.1)
    record.apply_updates(r, {"temperature_f": 97})
    assert r["temperature_f"] == 97 and r["temperature_c"] is None


def test_ifa_not_taken_is_zero():
    r = make(beneficiary_id="1007", ifa_tablets_given="false")
    assert r["ifa_tablets_given"] == 0


def test_impossible_values_rejected_not_stored():
    rej = []
    r = record.new_record(TODAY)
    record.apply_updates(r, {"beneficiary_id": "1001", "hb_g_dl": 310, "bp_systolic": 220, "bp_diastolic": 480, "weight_kg": 55}, rej)
    assert r["hb_g_dl"] is None and r["bp_systolic"] is None and r["bp_diastolic"] is None and r["weight_kg"] == 55
    assert ("hb_g_dl", 310.0) in rej and ("bp", "220/480") in rej


def test_adult_breathing_102_questioned():
    r = make(beneficiary_id="1001", respiratory_rate=102)
    assert any("respiratory_rate" in w for w in r["warnings"])
    r = make(beneficiary_id="1004", respiratory_rate=64)  # newborn: 64 is plausible (and a danger sign), no warning
    assert not any("respiratory_rate" in w for w in r["warnings"])


def test_new_beneficiary_dictated_dues_and_age():
    r = make(beneficiary_id="7777", visit_type="ANC", gestation_months=7, vaccinations_due_noted=["Td-2"])
    assert r["beneficiary_type"] == "pregnant" and r["context"]["gestation_weeks"] in (30, 31)
    assert r["vaccinations_due"] == ["Td-2"]  # what she said beats the schedule guess for an unknown history
    b = make(beneficiary_id="7778", age_days=42)
    assert b["beneficiary_type"] == "child" and "Penta-1" in b["vaccinations_due"]  # 6-week vaccines from the age


def test_visit_type_does_not_override_known_type():
    r = make(beneficiary_id="7001", visit_type="ANC", gestation_months=7)
    assert r["beneficiary_type"] == "pregnant"
    record.apply_updates(r, {"visit_type": "HBNC"})
    record.recompute(r, TODAY)
    assert r["beneficiary_type"] == "pregnant"  # "HBNC टीका" must not turn a pregnant woman into a newborn


def test_pregnant_weight_10_rejected_and_checks_readable():
    rej = []
    r = record.new_record(TODAY)
    record.apply_updates(r, {"beneficiary_id": "155", "visit_type": "ANC", "beneficiary_type": "pregnant", "weight_kg": 10}, rej)
    assert r["weight_kg"] is None and ("weight_kg", 10.0) in rej
    r2 = make(beneficiary_id="1001", respiratory_rate=50)
    assert r2["checks"] and r2["checks"][0]["field"] == "respiratory_rate"
