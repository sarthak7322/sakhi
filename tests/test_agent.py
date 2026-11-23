"""End-to-end turn flow with a scripted fake LLM (no network): escalation order, state machine, save."""
import json
import time

import pytest

from server import agent, config, llm, store


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)

    async def __call__(self, history, context, on_update, on_sentence, on_wait=None):
        upd, say = self.script.pop(0)
        await on_update(upd)
        await on_sentence(say)
        return f"<update>{json.dumps(upd)}</update><say>{say}</say>", {"ttft_ms": 1}


async def run(script, utterances, tmp_path, monkeypatch, fastpath=False):
    monkeypatch.setattr(llm, "run_turn", FakeLLM(script))
    monkeypatch.setattr(config, "FASTPATH", fastpath)
    monkeypatch.setattr(config, "RECORDS_FILE", tmp_path / "r.jsonl")
    out = []

    async def send_json(m):
        out.append(m)

    async def send_bytes(b):
        pass

    s = agent.Session(send_json, send_bytes, lang="hi-IN", use_audio=False)
    await s.start(greet=False)
    for u in utterances:
        await s.on_utterance(u)
        await s.turn.task
        await s.turn.speaker
    return s, out


@pytest.mark.asyncio
async def test_full_visit(tmp_path, monkeypatch):
    script = [
        ({"intent": "dictation", "set": {"beneficiary_id": "1004", "weight_kg": 2.5}}, "पूजा का बच्चा, वज़न ढाई किलो."),
        ({"intent": "correction", "set": {"weight_kg": 2.8}}, "ठीक है, वज़न दो दशमलव आठ किलो."),
        ({"intent": "finish", "set": {}}, "पूजा का बच्चा, ID 1004, वज़न 2.8 किलो. सब सही है?"),
        ({"intent": "confirm_yes", "set": {}}, "सेव हो गया."),
    ]
    s, out = await run(script, ["1004 baby weight dhai kilo", "nahi weight 2.8 kilo", "bas ho gaya", "haan sahi hai"],
                       tmp_path, monkeypatch)
    assert s.state == "saved"
    saved = [m for m in out if m["type"] == "saved"][0]["record"]
    assert saved["weight_kg"] == 2.8 and saved["beneficiary_name"] == "Baby of Pooja Yadav"
    assert (tmp_path / "r.jsonl").exists()


@pytest.mark.asyncio
async def test_danger_escalates_before_llm_reply(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}, "danger_signs": []}, "नोट किया.")]
    s, out = await run(script, ["Sunita 1001 ko bleeding ho rahi hai"], tmp_path, monkeypatch)
    texts = [m["text"] for m in out if m["type"] == "agent_text"]
    assert "PHC" in texts[0] and texts[1] == "नोट किया."
    assert s.rec["referral_required"] and s.rec["danger_signs"][0]["id"] == "MAT_BLEEDING"


@pytest.mark.asyncio
async def test_llm_flag_and_threshold_union(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1004", "temperature_f": 101},
                "danger_signs": ["NB_JAUNDICE"]}, "नोट किया.")]
    s, _ = await run(script, ["baby 1004 temperature 101, aankhen peeli si"], tmp_path, monkeypatch)
    assert {d["id"] for d in s.rec["danger_signs"]} == {"NB_FEVER", "NB_JAUNDICE"}


@pytest.mark.asyncio
async def test_cannot_save_without_id(tmp_path, monkeypatch):
    script = [({"intent": "finish", "set": {"weight_kg": 60}}, "लाभार्थी का नंबर बताइए."),
              ({"intent": "confirm_yes", "set": {}}, "...")]
    s, out = await run(script, ["weight 60 kilo bas", "haan"], tmp_path, monkeypatch)
    assert s.state == "collecting" and not [m for m in out if m["type"] == "saved"]


@pytest.mark.asyncio
async def test_guardrail_replaces_treatment_advice(tmp_path, monkeypatch):
    script = [({"intent": "other", "set": {}}, "Give paracetamol 250 mg.")]
    s, out = await run(script, ["bukhar ki dawai kya du"], tmp_path, monkeypatch)
    texts = [m["text"] for m in out if m["type"] == "agent_text"]
    assert "paracetamol" not in " ".join(texts).lower()
    assert any("ANM" in t for t in texts)


@pytest.mark.asyncio
async def test_echo_ignored(tmp_path, monkeypatch):
    s, out = await run([], [], tmp_path, monkeypatch)
    s._recent_speech.append((time.monotonic(), "वज़न दो दशमलव आठ किलो, नोट किया."))
    s.playing_until = time.monotonic() + 2  # Sakhi is still audible
    assert s._looks_like_echo("वज़न दो दशमलव आठ किलो नोट किया")
    assert not s._looks_like_echo("नहीं, वज़न दो दशमलव पाँच किलो")


@pytest.mark.asyncio
async def test_repeating_her_words_after_playback_is_an_answer(tmp_path, monkeypatch):
    s, out = await run([], [], tmp_path, monkeypatch)
    s._recent_speech.append((time.monotonic(), "दीदी, BP 80/120 सही है या 120/80?"))
    s.playing_until = time.monotonic() - 3  # finished speaking 3 s ago
    assert not s._looks_like_echo("120/80")
    assert not s._looks_like_echo("BP 120/80 सही है")


@pytest.mark.asyncio
async def test_fragment_merged_with_previous(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {}, "unclear": "hb"}, "Hb का मान बताइए."),
              ({"intent": "dictation", "set": {"hb_g_dl": 8}}, "Hb 8, नोट किया.")]
    s, out = await run(script, ["hemoglobin है मेरा"], tmp_path, monkeypatch)
    await s.on_utterance("8")
    await s.turn.task
    await s.turn.speaker
    assert s.turn.text == "hemoglobin है मेरा 8"
    assert s.rec["hb_g_dl"] == 8
    assert sum('hemoglobin' in m["content"] for m in s.history) == 1


@pytest.mark.asyncio
async def test_escalation_repeated_if_cut_off(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}}, "नोट किया."),
              ({"intent": "dictation", "set": {"weight_kg": 58}}, "वज़न नोट किया.")]
    s, out = await run(script, ["1001 ko bleeding"], tmp_path, monkeypatch)
    s._announced.clear()  # as if she interrupted the referral message
    await s.on_utterance("weight 58 kilo")
    await s.turn.task
    await s.turn.speaker
    texts = [m["text"] for m in out if m["type"] == "agent_text"]
    assert sum("PHC" in t for t in texts) == 2 and texts[-1] == "वज़न नोट किया."
    # once fully spoken it is not repeated again
    assert s._announced == {"MAT_BLEEDING"}


@pytest.mark.asyncio
async def test_threshold_flag_cleared_on_correction(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1004", "temperature_f": 88.4}}, "88.4 नोट किया."),
              ({"intent": "other", "set": {}}, "तापमान 88.4 सुना, सही है?"),  # validation question pass
              ({"intent": "correction", "set": {"temperature_f": 98.4}}, "98.4 नोट किया.")]
    s, _ = await run(script, ["1004 temperature 88.4", "nahi 98.4"], tmp_path, monkeypatch)
    assert s.rec["danger_signs"] == [] and not s.rec["referral_required"]


@pytest.mark.asyncio
async def test_keyword_flag_survives_correction(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1004"}}, "नोट किया."),
              ({"intent": "correction", "set": {"weight_kg": 2.8}}, "नोट किया.")]
    s, _ = await run(script, ["1004 baby ko jhatke aa rahe hain", "weight 2.8"], tmp_path, monkeypatch)
    assert [d["id"] for d in s.rec["danger_signs"]] == ["NB_CONVULSION"]


@pytest.mark.asyncio
async def test_confirm_without_readback_forces_readback(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1009", "weight_kg": 2.1}}, "नोट किया."),
              ({"intent": "confirm_yes", "set": {}}, "Saved, didi."),         # LLM jumps ahead: must not be spoken
              # (the read-back now comes from the record itself, no second LLM pass)
              ({"intent": "confirm_yes", "set": {}}, "सेव हो गया.")]
    s, out = await run(script, ["1009 weight 2.1", "bas save karo haan sahi hai"], tmp_path, monkeypatch)
    texts = [m["text"] for m in out if m["type"] == "agent_text"]
    assert "Saved, didi." not in texts and texts[-1].endswith("सब सही है?")
    assert s.state == "confirming" and not [m for m in out if m["type"] == "saved"]
    await s.on_utterance("haan")
    await s.turn.task
    await s.turn.speaker
    assert s.state == "saved"


@pytest.mark.asyncio
async def test_bare_yes_while_collecting_is_not_a_save(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1025", "visit_type": "PNC"}}, "noted."),
              ({"intent": "confirm_yes", "set": {}}, "Record saved, didi.")]
    s, out = await run(script, ["ID 1025 PNC visit", "Ji."], tmp_path, monkeypatch)
    texts = [m["text"] for m in out if m["type"] == "agent_text"]
    assert s.state == "collecting" and "Record saved, didi." not in texts
    assert not [m for m in out if m["type"] == "saved"]


@pytest.mark.asyncio
async def test_new_beneficiary_pnc_gets_maternal_thresholds(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1099", "visit_type": "PNC", "hb_g_dl": 6.2}}, "noted.")]
    s, _ = await run(script, ["ID 1099 PNC, Hb 6.2"], tmp_path, monkeypatch)
    assert [d["id"] for d in s.rec["danger_signs"]] == ["MAT_SEVERE_ANAEMIA"]


@pytest.mark.asyncio
async def test_llm_cannot_override_measured_threshold(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1099", "visit_type": "PNC", "hb_g_dl": 7},
                "danger_signs": ["MAT_SEVERE_ANAEMIA"]}, "Hb 7 noted.")]
    s, _ = await run(script, ["ID 1099 PNC, HB 7"], tmp_path, monkeypatch)
    assert s.rec["danger_signs"] == []   # Hb 7 is not below 7


@pytest.mark.asyncio
async def test_finish_without_readback_forces_one(tmp_path, monkeypatch):
    script = [
        ({"intent": "dictation", "set": {"beneficiary_id": "1004", "weight_kg": 2.8}}, "नोट किया."),
        ({"intent": "finish", "set": {}}, "सब सही है?"),                       # no read-back: must be replaced
        ({"intent": "finish", "set": {}}, "ID 1004, वज़न 2.8 किलो. सब सही है?"),  # forced read-back pass
    ]
    s, out = await run(script, ["1004 weight 2.8 kilo", "bas save karo"], tmp_path, monkeypatch)
    said = [m["text"] for m in out if isinstance(m, dict) and m.get("type") == "agent_text"]
    # the read-back is now built from the record: it names the ID and the weight, then asks
    assert any("1004" in t for t in said) and any("2.8" in t for t in said) and said[-1] == "सब सही है?"
    assert s.state == "confirming"


@pytest.mark.asyncio
async def test_unusual_value_asks_instead_of_noted(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001", "bp_systolic": 80, "bp_diastolic": 120}},
               "BP 80/120 नोट किया."),
              ({"intent": "other", "set": {}}, "BP 80/120 सुना, क्या 120/80 है?")]
    s, out = await run(script, ["1001 BP 80 by 120"], tmp_path, monkeypatch)
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert "BP 80/120 नोट किया." not in said and any("120/80" in t for t in said)
    assert any(m.get("type") == "event" and m["kind"] == "validate" for m in out)
    assert any(m.get("type") == "turn_info" and m["changed"].get("bp_systolic") == 80 for m in out)


@pytest.mark.asyncio
async def test_llm_referral_without_flag_is_dropped(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1007", "hb_g_dl": 7}},
               "Hb 7 नोट किया. Hb कम है, PHC में रेफर करें.")]
    s, out = await run(script, ["1007 hemoglobin 7"], tmp_path, monkeypatch)
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert not any("PHC" in t for t in said) and not s.rec["danger_signs"]
    assert any(m.get("type") == "event" and m["kind"] == "guard" for m in out)


@pytest.mark.asyncio
async def test_screen_edit_validates_and_escalates(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}}, "नोट किया."),
              ({"intent": "finish", "set": {}}, "ID 1001, ANC. सब सही है?")]
    s, out = await run(script, ["1001 ANC", "bas"], tmp_path, monkeypatch)
    assert s.state == "confirming"
    await s.on_edit("bp", "150/100")
    assert s.rec["bp_systolic"] == 150 and s.rec["bp_diastolic"] == 100
    assert [d["id"] for d in s.rec["danger_signs"]] == ["MAT_HIGH_BP"]
    assert s.state == "collecting"  # edited after read-back: must be read back again
    await s.on_edit("temperature", "99.2")
    assert s.rec["temperature_f"] == 99.2
    await s.on_edit("weight_kg", "abc")
    assert s.rec["weight_kg"] is None


@pytest.mark.asyncio
async def test_post_save_lines_come_from_record(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}}, "नोट किया."),
              ({"intent": "finish", "set": {}}, "सुनीता देवी, ID 1001, ANC. सब सही है?"),
              ({"intent": "confirm_yes", "set": {}}, "सेव हो गया. TT टीका दो दिन में दें.")]
    s, out = await run(script, ["1001 ANC", "bas", "haan sahi"], tmp_path, monkeypatch)
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert s.state == "saved" and not any("दो दिन" in x for x in said)
    assert "सेव हो गया, दीदी." in said and "टीके बाकी: Td-2." in said



@pytest.mark.asyncio
async def test_fastpath_full_visit_without_llm(tmp_path, monkeypatch):
    lines = ["सुनीता देवी, ID 1001, ANC visit", "वज़न 58 किलो, BP 120 by 80", "Hb 10.5, IFA की 30 गोली दी",
             "नहीं नहीं, वज़न 56 किलो है", "Td-2 का टीका आज लगा", "बस, save करो", "हाँ सही है"]
    s, out = await run([], lines, tmp_path, monkeypatch, fastpath=True)  # empty script: any LLM call would fail
    r = store.all_records()[-1]
    assert s.state == "saved" and r["weight_kg"] == 56 and r["bp_systolic"] == 120 and r["hb_g_dl"] == 10.5
    assert r["ifa_tablets_given"] == 30 and r["vaccinations_given_today"] == ["Td-2"]
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert any("सब सही है?" in x for x in said) and "सेव हो गया, दीदी." in said
    assert all(m.get("model") == "local" for m in out if m.get("type") == "metrics")


@pytest.mark.asyncio
async def test_fastpath_hands_over_to_llm(tmp_path, monkeypatch):
    script = [({"intent": "question", "set": {}}, "ANC मतलब गर्भावस्था की जाँच.")]
    s, out = await run(script, ["ANC क्या होता है?"], tmp_path, monkeypatch, fastpath=True)
    assert not any(m.get("type") == "event" and m["kind"] == "local" for m in out)


@pytest.mark.asyncio
async def test_fastpath_odd_value_asks(tmp_path, monkeypatch):
    s, out = await run([], ["ID 1001 ANC visit", "BP 80 by 120"], tmp_path, monkeypatch, fastpath=True)
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert any("सही है?" in x and "80/120" in x for x in said) and not s.rec["danger_signs"]


@pytest.mark.asyncio
async def test_llm_rate_limited_falls_back_to_local(tmp_path, monkeypatch):
    async def busy(*a, **k):
        raise RuntimeError("Error code: 429 - Rate limit reached")
    s, out = await run([], ["ID 1001 ANC visit"], tmp_path, monkeypatch, fastpath=True)
    monkeypatch.setattr(llm, "run_turn", busy)
    await s.on_utterance("मेरा वज़न लगभग 58 किलो होगा शायद")
    await s.turn.task
    await s.turn.speaker
    assert s.rec["weight_kg"] == 58
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert said[-1] == "वज़न 58 किलो, नोट किया." and not any("फिर से" in x for x in said)


@pytest.mark.asyncio
async def test_users_session_with_ai_out_of_budget(tmp_path, monkeypatch):
    async def busy(*a, **k):
        raise RuntimeError("Error code: 429 - Rate limit reached ... tokens per day")
    s, out = await run([], [], tmp_path, monkeypatch, fastpath=True)
    monkeypatch.setattr(llm, "run_turn", busy)
    lines = ["ID है 1001, ANC visit", "वज़न है 55 kg, BP है 120/80, hemoglobin है 10।",
             "तापमान है 97 degree Fahrenheit, साँस है 12 per minute। IFA की गोली नहीं ली और टीका भी नहीं है।",
             "चलिए, इसको save कर लीजिए।", "wait, wait, wait, wait. BP है 130/85।", "कुछ भी बोल रही हूँ", "और कुछ भी",
             "हाँ सही है"]
    for u in lines:
        await s.on_utterance(u)
        await s.turn.task
        await s.turn.speaker
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    r = store.all_records()[-1]
    assert s.state == "saved" and r["bp_systolic"] == 130 and r["temperature_f"] == 97 and r["ifa_tablets_given"] == 0
    assert not any("एक सेकंड" in x for x in said)
    assert sum("सीधी बातें" in x or "हाँ सही है' बोलिए" in x for x in said) == 1  # hint once, then quiet


@pytest.mark.asyncio
async def test_impossible_hb_asks_again_even_after_yes(tmp_path, monkeypatch):
    s, out = await run([], ["ID 1001 ANC visit", "hemoglobin है मेरा 310"], tmp_path, monkeypatch, fastpath=True)
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert s.rec["hb_g_dl"] is None and any("संभव नहीं" in x for x in said)


@pytest.mark.asyncio
async def test_stt_reconnect_backs_off_when_offline(monkeypatch):
    from server import gnani_stt
    calls, events = [], []

    async def on_event(m):
        events.append(m["type"])

    async def fail_connect(*a, **k):
        calls.append(1)
        raise OSError("Network is unreachable")
    monkeypatch.setattr(gnani_stt, "connect", fail_connect)
    s = gnani_stt.SttStream("hi-IN", on_event)
    for _ in range(300):  # ~10 s of audio frames during an outage
        await s.send_audio(b"\x00" * 1024)
    assert len(calls) == 1 and events == ["stt_offline"]  # one attempt, then back off (not 300 attempts)


@pytest.mark.asyncio
async def test_rejected_bp_does_not_leave_a_high_bp_flag(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1234", "visit_type": "ANC"}}, "नोट किया."),
              ({"intent": "dictation", "set": {"bp_systolic": 300, "bp_diastolic": 4}, "danger_signs": ["MAT_HIGH_BP"]},
               "BP 300/4, नोट किया.")]
    s, out = await run(script, ["ID 1234 ANC", "BP है 300/4"], tmp_path, monkeypatch)
    assert s.rec["danger_signs"] == [] and s.rec["bp_systolic"] is None


@pytest.mark.asyncio
async def test_llm_bp_flag_cleared_by_normal_measurement(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}, "danger_signs": ["MAT_HIGH_BP"]}, "नोट किया."),
              ({"intent": "dictation", "set": {"bp_systolic": 110, "bp_diastolic": 80}}, "BP 110/80, नोट किया.")]
    s, _ = await run(script, ["1001 BP बहुत हाई था", "BP 110 by 80"], tmp_path, monkeypatch)
    assert s.rec["danger_signs"] == [] and not s.rec["referral_required"]


@pytest.mark.asyncio
async def test_spoken_danger_survives_and_flag_claims_dropped(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}}, "नोट किया."),
              ({"intent": "correction", "set": {}}, "फ़्लैग हटा दिया गया, अब रिकॉर्ड साफ़ है.")]
    s, out = await run(script, ["1001 को बहुत खून आ रहा है", "flag हटाइए"], tmp_path, monkeypatch)
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert [d["id"] for d in s.rec["danger_signs"]] == ["MAT_BLEEDING"]  # she said it: stays
    assert not any("हटा दिया" in x for x in said)
    await s.on_unflag("MAT_BLEEDING", "test")
    assert s.rec["danger_signs"] == [] and any(m.get("type") == "event" and m["kind"] == "unflag" for m in out)


@pytest.mark.asyncio
async def test_confirming_unrelated_gets_fixed_reminder(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}}, "नोट किया."),
              ({"intent": "finish", "set": {}}, "ID 1001, ANC. सब सही है?"),
              ({"intent": "other", "set": {}}, "ठीक है, दीदी, रिकॉर्ड में सब ठीक है.")]
    s, out = await run(script, ["1001 ANC", "bas", "test कीजिए"], tmp_path, monkeypatch)
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert "ठीक है, दीदी, रिकॉर्ड में सब ठीक है." not in said and any("हाँ सही है" in x for x in said)
    assert s.state == "confirming"


@pytest.mark.asyncio
async def test_question_never_starts_readback(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}}, "नोट किया."),
              ({"intent": "finish", "set": {}}, "रिकॉर्ड पढ़ रही हूँ: ID 1001. सब सही है?")]
    s, out = await run(script, ["1001 ANC", "आपने इसको flag क्यों नहीं किया?"], tmp_path, monkeypatch)
    assert s.state == "collecting"


@pytest.mark.asyncio
async def test_breathing_5_flags_and_clears_on_correction(tmp_path, monkeypatch):
    s, out = await run([], ["ID 1001 ANC visit", "सांस 5 प्रति मिनट"], tmp_path, monkeypatch, fastpath=True)
    assert [d["id"] for d in s.rec["danger_signs"]] == ["MAT_BREATHING"]
    await s.on_utterance("नहीं, सांस 18 प्रति मिनट")
    await s.turn.task
    await s.turn.speaker
    assert s.rec["danger_signs"] == []



@pytest.mark.asyncio
async def test_round4_session_bugs(tmp_path, monkeypatch):
    # LLM tries to change the ID from "हाँ, 101" (she meant temperature) -> ignored; read-back has only stored values
    script = [({"intent": "dictation", "set": {"beneficiary_id": "155", "visit_type": "PNC"}}, "नोट किया."),
              ({"intent": "dictation", "set": {"temperature_f": 120}}, "तापमान 120, नोट किया."),
              ({"intent": "correction", "set": {"beneficiary_id": "101"}}, "ID 101 नोट किया."),
              ({"intent": "finish", "set": {}}, "रिकॉर्ड: तापमान 120°F. सब सही है?")]
    s, out = await run(script, ["ID 155 PNC", "तापमान 120", "हाँ, 101", "बस save करो"], tmp_path, monkeypatch)
    said = " ".join(m["text"] for m in out if m.get("type") == "agent_text")
    assert s.rec["beneficiary_id"] == "155" and s.rec["temperature_f"] is None
    assert "120°F" not in said and "ID 155" in said


@pytest.mark.asyncio
async def test_bare_number_after_rejection_fills_that_field(tmp_path, monkeypatch):
    s, out = await run([], ["ID 155 PNC visit", "तापमान 120"], tmp_path, monkeypatch, fastpath=True)
    assert s.pending == "temperature"
    for u in ["नहीं नहीं, 120 ही है", "हाँ, 101"]:
        await s.on_utterance(u)
        await s.turn.task
        await s.turn.speaker
    said = [m["text"] for m in out if m.get("type") == "agent_text"]
    assert any("आम तौर पर" in x for x in said)          # insisting gets the usual range, not "समझ गई"
    assert s.rec["temperature_f"] == 101 and s.rec["beneficiary_id"] == "155"


@pytest.mark.asyncio
async def test_lone_to_is_not_finish(tmp_path, monkeypatch):
    script = [({"intent": "dictation", "set": {"beneficiary_id": "1001"}}, "नोट किया."),
              ({"intent": "finish", "set": {}}, "रिकॉर्ड पढ़ रही हूँ")]
    s, _ = await run(script, ["1001 ANC", "तो।"], tmp_path, monkeypatch)
    assert s.state == "collecting"
