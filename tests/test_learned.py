from datetime import date

import pytest

from server import learned, record


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(learned, "PATH", tmp_path / "learned.json")
    monkeypatch.setattr(learned, "_db", None)


REC = record.new_record(date(2026, 11, 18))
ODD = "माँ जी का भार तौला तो 61 के करीब बैठा"   # a phrasing the rules don't know


def test_learns_after_two_consistent_answers():
    assert learned.lookup(ODD, REC, "collecting", "hi-IN") is None
    learned.learn(ODD, {"intent": "dictation", "set": {"weight_kg": 61}}, "collecting", "hi-IN")
    assert learned.lookup(ODD, REC, "collecting", "hi-IN") is None  # seen once: not yet
    learned.learn("माँ जी का भार तौला तो 58 के करीब बैठा", {"intent": "dictation", "set": {"weight_kg": 58}}, "collecting", "hi-IN")
    p = learned.lookup("माँ जी का भार तौला तो 63.5 के करीब बैठा", REC, "collecting", "hi-IN")
    assert p and p.set == {"weight_kg": 63.5}


def test_disagreement_blocks_shape():
    learned.learn(ODD, {"intent": "dictation", "set": {"weight_kg": 61}}, "collecting", "hi-IN")
    learned.learn(ODD, {"intent": "dictation", "set": {"hb_g_dl": 61}}, "collecting", "hi-IN")
    learned.learn(ODD, {"intent": "dictation", "set": {"weight_kg": 61}}, "collecting", "hi-IN")
    assert learned.lookup(ODD, REC, "collecting", "hi-IN") is None


@pytest.mark.parametrize("text,upd", [
    ("बच्चे को 102 बुखार है", {"intent": "dictation", "set": {"temperature_f": 102}}),          # danger word
    ("तौला तो 61 के करीब", {"intent": "dictation", "set": {"weight_kg": 60}}),                 # value not in sentence
    ("क्या 61 सही है?", {"intent": "dictation", "set": {"weight_kg": 61}}),                    # question
    ("तौला तो 61 के करीब", {"intent": "dictation", "set": {"weight_kg": 61}, "danger_signs": ["MAT_X"]}),
])
def test_never_learns_unsafe(text, upd):
    for _ in range(3):
        learned.learn(text, upd, "collecting", "hi-IN")
    assert learned.lookup(text, REC, "collecting", "hi-IN") is None


def test_name_slot():
    for name in ["सुनीता देवी", "रेखा कुमारी"]:
        learned.learn(f"{name} जी के घर पहुँच गई हूँ", {"intent": "dictation", "set": {"beneficiary_id": record.find_in_text(name)["id"]}},
                      "collecting", "hi-IN")
    p = learned.lookup("पूजा यादव जी के घर पहुँच गई हूँ", REC, "collecting", "hi-IN")
    assert p and p.set == {"beneficiary_id": "1003"}
