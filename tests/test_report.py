"""End-of-visit PDF report: renders every section from a saved record and its voice log."""
import json

from server import report


def _rec(**kw):
    base = {"record_id": "abc123", "beneficiary_id": "1004", "beneficiary_name": "Baby of Pooja Yadav",
            "beneficiary_type": "newborn", "visit_type": "HBNC", "visit_date": "2026-11-19", "weight_kg": 2.5,
            "temperature_f": 98.4, "registered": True, "context": {"village": "Rampur", "dob": "2026-11-10"},
            "asha_id": "ASHA-DEMO", "saved_at": "2026-11-19T10:00:00", "danger_signs": [], "referral_required": False,
            "followups": [{"priority": "normal", "action": "Next home visit (HBNC)", "due": "2026-11-24"}]}
    return {**base, **kw}


def test_report_has_details_and_followups():
    html = report.render(_rec())
    for s in ("Baby of Pooja Yadav", "पूजा यादव का बच्चा", "Rampur", "9 days", "2.5 kg", "98.4 °F",
              "Next home visit (HBNC)", "24 Nov 2026", "No danger signs", "Save as PDF"):
        assert s in html, s
    assert "window.print()" in html and "setTimeout" not in html


def test_report_danger_and_escaping():
    sign = {"id": "NB_NOT_FEEDING", "label_en": "baby not feeding", "label_hi": "बच्चा दूध नहीं पी रहा",
            "evidence": "doodh nahi", "guideline": "HBNC", "utterance": "<script>x</script>"}
    html = report.render(_rec(danger_signs=[sign], referral_required=True), auto_print=True)
    assert "REFERRAL REQUIRED" in html and "baby not feeding" in html
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "setTimeout(() => window.print()" in html


def test_transcript_only_this_visit(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "VOICELOGS", tmp_path)
    ev = [{"type": "agent_text", "text": "greeting", "turn": 0},
          {"type": "user_turn", "text": "first visit", "turn": 1}, {"type": "saved", "record_id": "old"},
          {"type": "user_turn", "text": "weight dhai kilo", "turn": 2},
          {"type": "agent_text", "text": "noted", "turn": 2}, {"type": "saved", "record_id": "abc123"},
          {"type": "user_turn", "text": "next visit", "turn": 3}]
    (tmp_path / "s1.jsonl").write_text("\n".join(json.dumps(e) for e in ev))
    tr = report.transcript(_rec(session_id="s1"))
    assert [l["text"] for l in tr] == ["weight dhai kilo", "noted"]
    assert report.transcript(_rec()) == tr  # older records without a session id: found by record id
