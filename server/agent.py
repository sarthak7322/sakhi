"""Per-connection voice session: STT -> danger check -> LLM -> TTS, with barge-in and turn merging."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
import time
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable

from . import config, danger, fastpath, learned, llm, numwords, record, store
from .gnani_stt import SttStream
from .gnani_tts import TtsClient

log = logging.getLogger("agent")

WAIT_LINE = {
    "hi-IN": "एक सेकंड, दीदी.", "en-IN": "One second, didi.", "mr-IN": "एक सेकंद, ताई.", "bn-IN": "এক সেকেন্ড, দিদি.",
    "gu-IN": "એક સેકન્ડ, બહેન.", "pa-IN": "ਇੱਕ ਸਕਿੰਟ, ਭੈਣ ਜੀ.", "ta-IN": "ஒரு நொடி, அக்கா.", "te-IN": "ఒక్క క్షణం, అక్కా.",
    "kn-IN": "ಒಂದು ಕ್ಷಣ, ಅಕ್ಕ.", "ml-IN": "ഒരു നിമിഷം, ചേച്ചി.",
}

BUSY_HINT = {  # [confirming][english]
    False: ("अभी मैं सीधी बातें ही समझ पा रही हूँ. एक-एक करके बोलिए, जैसे 'वज़न 50 किलो' या 'BP 120 by 80'.",
            "Right now I understand simple lines only. Say one thing at a time, like 'weight 50 kg' or 'BP 120 by 80'."),
    True: ("सही है तो 'हाँ सही है' बोलिए. कुछ बदलना है तो सीधे बोलिए, जैसे 'BP 120 by 80'.",
           "If it is correct say 'yes, correct'. To change something just say it, like 'BP 120 by 80'."),
}

GREETING = {
    "hi-IN": "नमस्ते दीदी, मैं सखी हूँ, आपकी AI सहायक. विज़िट के बारे में बोलिए, लाभार्थी के नाम या नंबर से शुरू कीजिए.",
    "en-IN": "Hello didi, I am Sakhi, your AI assistant. Tell me about the visit, starting with the beneficiary's name or number.",
    "mr-IN": "नमस्कार ताई, मी सखी, तुमची AI सहायक. भेटीबद्दल सांगा, लाभार्थीचे नाव किंवा नंबरने सुरू करा.",
    "bn-IN": "নমস্কার দিদি, আমি সখী, আপনার AI সহকারী. ভিজিটের কথা বলুন, উপকারভোগীর নাম বা নম্বর দিয়ে শুরু করুন.",
    "gu-IN": "નમસ્તે બહેન, હું સખી છું, તમારી AI સહાયક. મુલાકાત વિશે કહો, લાભાર્થીના નામ કે નંબરથી શરૂ કરો.",
    "pa-IN": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ ਭੈਣ ਜੀ, ਮੈਂ ਸਖੀ ਹਾਂ, ਤੁਹਾਡੀ AI ਸਹਾਇਕ. ਮੁਲਾਕਾਤ ਬਾਰੇ ਦੱਸੋ, ਲਾਭਪਾਤਰੀ ਦੇ ਨਾਮ ਜਾਂ ਨੰਬਰ ਨਾਲ ਸ਼ੁਰੂ ਕਰੋ.",
    "ta-IN": "வணக்கம் அக்கா, நான் சகி, உங்கள் AI உதவியாளர். வருகை பற்றி சொல்லுங்கள், பயனாளியின் பெயர் அல்லது எண்ணுடன் தொடங்குங்கள்.",
    "te-IN": "నమస్తే అక్కా, నేను సఖి, మీ AI సహాయకురాలిని. సందర్శన గురించి చెప్పండి, లబ్ధిదారు పేరు లేదా నంబర్‌తో మొదలుపెట్టండి.",
    "kn-IN": "ನಮಸ್ಕಾರ ಅಕ್ಕ, ನಾನು ಸಖಿ, ನಿಮ್ಮ AI ಸಹಾಯಕಿ. ಭೇಟಿಯ ಬಗ್ಗೆ ಹೇಳಿ, ಫಲಾನುಭವಿಯ ಹೆಸರು ಅಥವಾ ಸಂಖ್ಯೆಯಿಂದ ಪ್ರಾರಂಭಿಸಿ.",
    "ml-IN": "നമസ്കാരം ചേച്ചി, ഞാൻ സഖി, നിങ്ങളുടെ AI സഹായി. സന്ദർശനത്തെക്കുറിച്ച് പറയൂ, ഗുണഭോക്താവിന്റെ പേരോ നമ്പറോ ആദ്യം പറയൂ.",
}

_MEASURE = re.compile(r"\b(weight|wajan|vajan|bp|hb|hemoglobin|haemoglobin|temperature|temp|saans|breathing|id)\b"
                      r"|वज़न|वजन|बीपी|हीमोग्लोबिन|तापमान|टेम्परेचर|सांस|साँस|आईडी", re.I)


def _looks_unfinished(text: str) -> bool:
    """A measurement named without any number, in a short utterance: she is probably mid-sentence."""
    t = numwords.normalize(text)
    if danger.detect_text(text, None):
        return False  # never delay a danger sign
    return bool(_MEASURE.search(t)) and not re.search(r"\d", t) and len(t.split()) <= 3 \
        and not re.search(r"[?]|क्या|kya|kaun|कौन|matlab|मतलब|nahi|नहीं|normal|नॉर्मल|ठीक|theek|thik", t, re.I)


_MONTHS_HI = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December"]


def _snapshot(rec: dict) -> dict:
    """The record without empty fields (for the voice log / dataset)."""
    return {k: v for k, v in rec.items() if v not in (None, [], {}, "") and k != "context"}


def saved_summary(rec: dict, lang: str) -> list[str]:
    """Fixed post-save lines (Hindi / English) built only from the saved record and its follow-ups."""
    hi = lang in ("hi-IN", "mr-IN")
    if lang not in ("hi-IN", "en-IN"):
        return []  # other languages: the LLM line, still filtered by the guards
    def day(iso):
        try:
            y, m, d = (int(x) for x in iso.split("-"))
            return f"{d} {(_MONTHS_HI if hi else _MONTHS_EN)[m - 1]}"
        except (ValueError, AttributeError, IndexError):
            return iso
    out = ["सेव हो गया, दीदी." if hi else "Saved, didi."]
    if rec.get("referral_required"):
        out.append("याद रखिए: आज ही PHC रेफ़र करें." if hi else "Remember: refer to the PHC today.")
    if rec.get("vaccinations_due"):
        v = ", ".join(rec["vaccinations_due"])
        out.append(f"टीके बाकी: {v}." if hi else f"Vaccines due: {v}.")
    nxt = rec.get("next_visit_date") or rec.get("next_visit_suggested")
    if nxt:
        out.append(f"अगली विज़िट {day(nxt)}." if hi else f"Next visit {day(nxt)}.")
    return out


_ID_WORDS = re.compile(r"\bid\b|आईडी|आई डी|नंबर|number|नम्बर|लाभार्थी|beneficiary|register में", re.I)
_FLAG_CLAIM = re.compile(r"(फ़्?लैग|flag|निशान)[\s\S]{0,30}(हटा|हट\s|remov|clear|साफ़|लगा\s?(दिया|दी)|रहेगा|अभी भी)", re.I)
_REFERRAL_CLAIM = re.compile(r"refer|रेफ़?र|PHC\s*(में|ले|भेज|जा)|अस्पताल\s*(ले|भेज)", re.I)
_READS_VALUES = re.compile(r"\d")  # a read-back names the ID / measurements, which are always digits
_SAVE_WORDS = re.compile(r"\bsave\b|\bsav\b|सेव|सहेज|\bbas\b|बस|ho gaya|हो गया|\bdone\b|khatam|ख़त्म|खत्म", re.I)
_SAVED_CLAIM = re.compile(r"\bsaved\b|सेव हो गया|सेव कर दिया|सहेजा गया|सहेज लिया|save ho gaya|save kar diya", re.I)

HISTORY_TURNS = 3  # user+assistant pairs kept as LLM context


@dataclass
class Turn:
    id: int
    text: str
    t_end: float  # monotonic time the utterance ended (STT "processing" or transcript arrival)
    update_applied: bool = False
    raw_update: str = ""
    first_audio_at: float | None = None
    timing: dict = field(default_factory=dict)
    task: asyncio.Task | None = None
    speaker: asyncio.Task | None = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    blocked: bool = False  # guardrail tripped: drop further sentences
    need_readback: bool = False  # LLM tried to confirm without a read-back: replace its line with one
    check_warnings: list = field(default_factory=list)  # new validation warnings: ask her to confirm them
    source: str = "voice"
    said_wait: bool = False
    llm_upd: dict | None = None
    rejected: bool = False
    rejected_fields: set = field(default_factory=set)
    changed: list = field(default_factory=list)  # record fields this turn changed
    intent: str = ""


class Session:
    LOGGED = {"ready", "user_turn", "agent_text", "danger", "barge_in", "metrics", "turn_info", "event", "saved",
              "transcript", "error", "llm_raw", "session_end"}

    def __init__(self, send_json: Callable[[dict], Awaitable[None]], send_bytes: Callable[[bytes], Awaitable[None]],
                 lang: str = "hi-IN", asha_id: str = "ASHA-DEMO", use_audio: bool = True,
                 persist_log: bool | None = None):
        self._send_raw = send_json
        self.persist_log = use_audio if persist_log is None else persist_log
        self.send_bytes = send_bytes
        self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self._log_path = config.DATA_DIR / "voicelogs" / f"{self.session_id}.jsonl"
        self._t0 = time.monotonic()
        self.lang = lang if lang in config.LANG_CODES else "hi-IN"
        self.asha_id = asha_id
        self.use_audio = use_audio
        self.today = date.today()
        self.rec = record.new_record(self.today)
        self.state = "collecting"  # collecting | confirming | saved
        self.history: list[dict] = []
        self.turn: Turn | None = None
        self._turn_seq = 0
        self._last_processing: float | None = None
        self.playing_until = 0.0
        self.paused_for_user = False
        names = [b["name"] for b in record.REGISTRY] + [b["name_local"] for b in record.REGISTRY]
        self.stt = SttStream(self.lang, self.on_stt, extra_keyterms=names) if use_audio else None
        self.tts = TtsClient(self.lang) if use_audio else None
        self.spoken: list[str] = []  # transcript of agent speech (for eval / UI)
        self._recent_speech: list[tuple[float, str]] = []
        self._greeting: asyncio.Task | None = None
        self._announced: set[str] = set()  # danger ids whose referral message was fully spoken
        self.pending: str | None = None  # field Sakhi just asked for (a bare number next fills it)
        self._last_busy_hint = -1e9
        self.last_rejected = None  # (field, value) of the last impossible value, until she gives a valid one

    async def send_json(self, m: dict) -> None:
        """Send to the phone and, for conversation events, append to this session's voice log (text only,
        no audio is stored)."""
        if m.get("type") in self.LOGGED and self.persist_log:
            entry = {"t": round(time.monotonic() - self._t0, 3), **m}
            if m.get("type") == "saved":
                entry = {"t": entry["t"], "type": "saved", "record_id": m["record"].get("record_id"),
                         "beneficiary_id": m["record"].get("beneficiary_id")}
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            except OSError:
                log.exception("voice log write failed")
        await self._send_raw(m)

    async def _event(self, kind: str, text: str, **extra) -> None:
        """A pipeline decision worth showing in the voice log (echo ignored, merge, guardrail, ...)."""
        await self.send_json({"type": "event", "kind": kind, "text": text,
                              "turn": self.turn.id if self.turn else 0, **extra})

    # ------------------------------------------------------------------ lifecycle
    async def start(self, greet: bool = True) -> None:
        if self.use_audio:
            await asyncio.gather(self.stt.start(), self.tts.warm())
        await self.push_record()
        await self.send_json({"type": "ready", "lang": self.lang, "session_id": self.session_id,
                              "asha_id": self.asha_id, "llm": f"{config.LLM_PROVIDER}:{config.LLM_MODEL}",
                              "stt": config.STT_MODEL if hasattr(config, "STT_MODEL") else "prisma-v3.0",
                              "tts": f"{config.TTS_MODEL} / {config.TTS_VOICE}"})
        if greet:
            self._greeting = asyncio.create_task(self._speak_standalone(GREETING.get(self.lang, GREETING["en-IN"])))

    async def close(self) -> None:
        if self._turn_seq:
            await self.send_json({"type": "session_end", "state": self.state, "turns": self._turn_seq,
                                  "record": _snapshot(self.rec)})
        if self.turn and self.turn.task:
            self.turn.task.cancel()
        if self.stt:
            await self.stt.close()
        if self.tts:
            await self.tts.close()

    # ------------------------------------------------------------------ inputs
    async def on_audio(self, pcm: bytes) -> None:
        if self.stt:
            await self.stt.send_audio(pcm)

    async def on_stt(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "speech_start":
            await self.send_json({"type": "speech_start"})
            if self.is_speaking():
                # Pause (not stop) playback until we know it was real speech, not a door slam.
                self.paused_for_user = True
                await self.send_json({"type": "pause_playback"})
        elif kind == "processing":
            self._last_processing = msg["_recv"]
            await self.send_json({"type": "processing"})
        elif kind == "transcript":
            text = (msg.get("text") or "").strip()
            stt_info = {"stt_latency_ms": msg.get("latency"), "detected_lang": msg.get("detected_language_code")}
            await self.send_json({"type": "transcript", "text": text, **stt_info})
            if not text or len(text.strip(" .,।?!")) == 0 or self._looks_like_echo(text):
                if text and len(text.strip(" .,।?!")):
                    await self._event("echo", f"ignored own voice picked up by the mic: “{text}”")
                elif self.paused_for_user:
                    await self._event("noise", "noise while Sakhi was speaking: playback resumed")
                if self.paused_for_user:  # it was noise: carry on talking
                    self.paused_for_user = False
                    await self.send_json({"type": "resume_playback"})
                return
            t_end = self._last_processing or msg["_recv"]
            self._last_processing = None
            await self.on_utterance(text, t_end, stt_info)
        elif kind == "error":
            log.warning("STT error: %s", msg.get("message"))
            await self.send_json({"type": "error", "where": "stt", "message": msg.get("message")})
        elif kind == "stt_disconnected":
            await self.send_json({"type": "status", "message": "stt reconnecting"})
        elif kind == "stt_offline":
            await self._event("offline", "no connection to the speech service (internet down?): retrying with backoff")
            await self.send_json({"type": "net", "online": False})
        elif kind == "stt_reconnected":
            await self._event("online", "speech service reconnected")
            await self.send_json({"type": "net", "online": True})

    async def on_commit(self) -> None:
        if self.stt:
            await self.stt.commit()

    async def on_utterance(self, text: str, t_end: float | None = None, stt_info: dict | None = None,
                           source: str = "voice") -> None:
        """A finished user utterance. Handles barge-in and merges split dictation."""
        t_end = t_end or time.monotonic()
        prev = self.turn
        merged_from = None
        log.info("utterance: %s | prev turn running=%s speaking=%s", text,
                 bool(prev and prev.task and not prev.task.done()), self.is_speaking())
        if prev and prev.task and not prev.task.done():
            if not prev.update_applied or self._is_fragment(prev, t_end):
                # She paused mid-sentence and kept talking: merge into one turn (smart endpointing).
                text = f"{prev.text} {text}"
                merged_from = prev.id
            await self._interrupt(prev)
        elif prev and self._is_fragment(prev, t_end):
            # "hemoglobin है मेरा" ... "8": the first half got a reply that changed nothing. Answer both halves
            # as one sentence instead of replying to each piece.
            self._drop_history_of(prev)
            text = f"{prev.text} {text}"
            merged_from = prev.id
            await self._interrupt(prev)
        elif self.is_speaking() or self.paused_for_user:
            await self._interrupt(prev)
        self.paused_for_user = False
        self._turn_seq += 1
        turn = Turn(self._turn_seq, text, t_end, source=source)
        turn.timing.update({k: v for k, v in (stt_info or {}).items() if v is not None})
        self.turn = turn
        await self.send_json({"type": "user_turn", "turn": turn.id, "text": text, "source": source,
                              "merged_from": merged_from, **{k: v for k, v in (stt_info or {}).items() if v is not None}})
        if merged_from:
            await self._event("merge", f"joined with turn {merged_from}: she paused mid-sentence, answering both as one")
        turn.speaker = asyncio.create_task(self._speaker(turn))
        turn.task = asyncio.create_task(self._run_turn(turn))

    async def _interrupt(self, turn: Turn | None) -> None:
        if self._greeting and not self._greeting.done():
            self._greeting.cancel()
        if turn is None:
            if self.tts:
                await self.tts.cancel()
            self.playing_until = 0.0
            await self.send_json({"type": "barge_in", "turn": 0})
            return
        llm_unfinished = turn.task is not None and not turn.task.done()
        for t in (turn.task, turn.speaker):
            if t and not t.done():
                t.cancel()
        if self.tts:
            await self.tts.cancel()
        self.playing_until = 0.0
        await self.send_json({"type": "barge_in", "turn": turn.id})
        if llm_unfinished and turn.update_applied:  # keep LLM context consistent with what was applied
            self._append_history(turn.text, turn.raw_update + "<say>[interrupted by ASHA]</say>")

    # ------------------------------------------------------------------ the turn
    async def _run_turn(self, turn: Turn) -> None:
        log.info("turn %d start (state=%s): %s", turn.id, self.state, turn.text)
        try:
            if _looks_unfinished(turn.text):
                await self._event("hold", f"measurement named without a number: waiting {self.UNFINISHED_HOLD_S:g} s for the value")
                # "hemoglobin है मेरा..." - the number is probably coming. Wait a moment; if she goes on,
                # on_utterance merges both halves into this turn before anything is said.
                await asyncio.sleep(self.UNFINISHED_HOLD_S)
            if self.state == "saved":  # anything after a save starts the next visit
                self._reset_record()
            # 1) instant keyword danger check: escalate before the LLM even answers
            unannounced = [d["id"] for d in self.rec["danger_signs"] if d["id"] not in self._announced]
            if unannounced:  # a referral message was cut off by her speech: say it again
                await turn.queue.put(("escalation", unannounced, danger.escalation_text(unannounced, self.lang)))
            btype = self.rec.get("beneficiary_type")
            if not btype:  # she may name the beneficiary and the sign in one breath
                guess = record.find_in_text(numwords.normalize(turn.text))
                btype = guess["type"] if guess else None
            if not btype and re.search(r"\bbaby\b|बच्च|शिशु|नवजात|bachch|newborn|infant", turn.text, re.I):
                btype = "newborn"  # "baby has high fever" before the type is known: newborn signs, not maternal
            hits = [h for h in danger.detect_text(turn.text, btype) if not h.negated]
            await self._escalate(turn, hits)
            # 2) local fast path: routine dictation is understood without an LLM call
            pending, self.pending = self.pending, None
            fp = fastpath.parse(turn.text, self.rec, self.state, self.lang, pending=pending,
                                rejected=self.last_rejected) if config.FASTPATH else None
            if fp is not None and fp.set:
                self.last_rejected = None
            how = "local"
            if fp is None and config.FASTPATH:
                fp = learned.lookup(turn.text, self.rec, self.state, self.lang)
                if fp is not None:
                    how = "learned"
            if fp is None and config.FASTPATH and llm.all_busy():
                fp = fastpath.parse(turn.text, self.rec, self.state, self.lang, strict=False)
                if fp is not None:
                    await self._event("busy", "free LLM tier busy: took the values understood locally instead of waiting")
            if fp is not None:
                await self._run_local(turn, fp, how)
                return
            state0 = self.state
            # 3) LLM: structured update + spoken reply
            ctx = llm.turn_context(self.rec, self.state, self.lang, self.today.isoformat(), numwords.normalize(turn.text),
                                   [d["id"] for d in self.rec["danger_signs"]])

            held: list[str] | None = None
            held_flushed = [False]

            async def on_update(upd: dict) -> None:
                nonlocal held
                turn.raw_update = f"<update>{json.dumps(upd, ensure_ascii=False)}</update>"
                turn.llm_upd = upd
                await self._apply(turn, upd)
                turn.update_applied = True
                if not turn.rejected:
                    self.pending = fastpath.field_from_hint(upd.get("unclear"))
                if turn.timing.get("intent") == "finish" and self.state == "confirming":
                    held = []

            async def on_sentence(s: str) -> None:
                if turn.blocked:
                    return
                if not self.rec["danger_signs"] and _REFERRAL_CLAIM.search(s):
                    # referral advice must come from a flagged danger sign (fixed template), never from the LLM
                    log.warning("dropped unflagged referral: %s", s)
                    await self._event("guard", f"dropped referral advice with no danger sign flagged: “{s}”")
                    return
                if _FLAG_CLAIM.search(s):
                    log.warning("dropped flag claim: %s", s)
                    await self._event("guard", f"dropped a claim about flags (only the system sets or clears flags): “{s}”")
                    return
                if self.state != "saved" and _SAVED_CLAIM.search(s):
                    log.warning("dropped false save claim: %s", s)
                    await self._event("guard", f"dropped a 'saved' claim before the record was saved: “{s}”")
                    return
                if llm.is_unsafe(s):
                    log.warning("guardrail replaced reply: %s", s)
                    await self._event("guard", f"safety guardrail replaced a reply with medical advice: “{s}”")
                    turn.blocked = True
                    s = llm.SAFE_FALLBACK.get(self.lang, llm.SAFE_FALLBACK["en-IN"])
                if held is not None:  # finish turn: hold lines until one actually reads a value back
                    held.append(s)
                    if not _READS_VALUES.search(s):
                        return
                    for h in held:
                        await turn.queue.put(h)
                    held_flushed[0] = True
                    held.clear()
                    return
                await turn.queue.put(s)

            async def on_wait(delay: float) -> None:
                await self._event("busy", f"free LLM tier busy (per-minute limit): waiting {delay:.1f} s, then retrying")

            raw, timing = await llm.run_turn(self.history, ctx, on_update, on_sentence, on_wait)
            log.info("turn %d llm done: %s", turn.id, raw.replace("\n", " ")[:300])
            await self.send_json({"type": "llm_raw", "turn": turn.id, "model": timing.get("model"), "raw": raw[:2000],
                                  "ttft_ms": timing.get("ttft_ms"), "state_before": state0})
            if held is not None and not held_flushed[0] and not turn.blocked:
                log.warning("finish turn without a read-back, forcing one: %s", held)
                await self._event("readback", "model skipped the read-back: forcing a full read-back")
                turn.need_readback = True
            if turn.check_warnings and not turn.need_readback:
                self._append_history(turn.text, turn.raw_update + "<say>[replaced by a validation question]</say>")
                turn.blocked = False
                ctx2 = llm.turn_context(self.rec, self.state, self.lang, self.today.isoformat(),
                                        "[system] These values failed validation: " + "; ".join(turn.check_warnings)
                                        + ". In ONE short line in the session language, tell her the value you heard "
                                        "and ask her to confirm or correct it. intent: other, set: {}.", [])
                raw, t2 = await llm.run_turn(self.history, ctx2, lambda u: asyncio.sleep(0), on_sentence)
                self._append_history("[validation check]", raw)
                turn.timing.update(timing)
                await turn.queue.put(None)
                return
            if turn.need_readback:
                held = None
                self._append_history(turn.text, turn.raw_update + "<say>[replaced by read-back]</say>")
                turn.blocked = False
                ctx2 = llm.turn_context(self.rec, self.state, self.lang, self.today.isoformat(),
                                        "[system] Nothing is saved yet. Read the whole record back to her now and "
                                        "ask if it is correct (intent: finish).", [])
                raw, t2 = await llm.run_turn(self.history, ctx2, lambda u: asyncio.sleep(0), on_sentence)
                timing["readback_ms"] = t2.get("llm_total_ms")
                self._append_history("[read back the record]", raw)
                turn.timing.update(timing)
                await turn.queue.put(None)
                return
            turn.timing.update(timing)
            self._append_history(turn.text, raw)
            if config.FASTPATH and turn.llm_upd and not turn.blocked and not turn.check_warnings:
                learned.learn(turn.text, turn.llm_upd, state0, self.lang)  # next time: no LLM call
        except asyncio.CancelledError:
            log.info("turn %d cancelled (update_applied=%s)", turn.id, turn.update_applied)
            raise
        except Exception as e:
            log.exception("turn failed")
            busy = "429" in str(e) or "rate limit" in str(e).lower()
            if busy and config.FASTPATH and not turn.update_applied:
                fp = fastpath.parse(turn.text, self.rec, self.state, self.lang, strict=False)
                if fp is not None:  # degrade gracefully: keep what we understood instead of "say it again"
                    await self._event("busy", "free LLM tier busy: kept the values understood locally")
                    await self._run_local(turn, fp)
                    return
            if busy:
                now = time.monotonic()
                if now - self._last_busy_hint > 25:
                    self._last_busy_hint = now
                    await turn.queue.put(BUSY_HINT[self.state == "confirming"][self.lang == "en-IN"])
                else:
                    await self._event("busy", "AI busy and sentence not understood locally: stayed quiet (hint given recently)")
                await turn.queue.put(None)
                return
            await self.send_json({"type": "error", "where": "llm",
                                  "message": "All free LLM models are busy (per-minute limit). Please say it again."
                                  if busy else str(e)[:200]})
            await turn.queue.put("माफ़ कीजिए, फिर से बोलिए." if self.lang == "hi-IN" else "Sorry, please say that again.")
        await turn.queue.put(None)  # not reached when cancelled

    async def _run_local(self, turn: Turn, fp: "fastpath.Parsed", how: str = "local") -> None:
        t0 = time.monotonic()
        was_confirming = self.state == "confirming"
        intent = "finish" if fp.finish_after else fp.intent
        upd = {"intent": intent, "set": fp.set, "danger_signs": [], "unclear": ""}
        turn.raw_update = f"<update>{json.dumps(upd, ensure_ascii=False)}</update>"
        self.pending = fp.ask
        await self._apply(turn, upd, local=True)
        turn.update_applied = True
        if turn.rejected:
            # _apply already asked her to repeat the impossible value; still confirm the good ones
            lines = [fastpath.ack(self.rec, turn.changed, self.lang)] if turn.changed else []
        elif turn.check_warnings:
            lines = [fastpath.confirm_odd(self.rec, turn.changed, self.lang)]
            if self.state == "confirming":
                self.state = "collecting"  # an odd value after the read-back: confirm it, then read back again
                await self.push_record()
        elif fp.say:
            lines = fp.say
        elif self.state == "saved":
            lines = []  # _apply already queued the fixed post-save lines
        elif intent == "finish" or fp.intent == "confirm_yes" or (was_confirming and fp.intent == "correction"):
            lines = fastpath.readback(self.rec, self.lang)
            if was_confirming and record.missing_required(self.rec) == [] and self.state != "saved":
                self.state = "confirming"
                await self.push_record()
        else:
            lines = ([fastpath.ack(self.rec, turn.changed or list(fp.set), self.lang)] if fp.set else []) + fp.extra_say
            lines = lines or [fastpath.ack(self.rec, [], self.lang)]
        ms = round((time.monotonic() - t0) * 1000, 1)
        self._append_history(turn.text, turn.raw_update + "<say>" + " ".join(lines) + "</say>")
        turn.timing.update({"model": how, "ttft_ms": 0, "llm_total_ms": 0, "local_ms": ms})
        await self._event(how, (f"phrasing learned from earlier LLM answers: understood in {ms} ms, no LLM call"
                                if how == "learned" else f"understood locally in {ms} ms, no LLM call ({fp.intent})"))
        for ln in lines:
            await turn.queue.put(ln)
        await turn.queue.put(None)

    async def _apply(self, turn: Turn, upd: dict, local: bool = False) -> None:
        intent = upd.get("intent", "other")
        st = upd.get("set") or {}
        # --- guards on what the LLM is allowed to change
        if intent == "finish" and not (_SAVE_WORDS.search(turn.text) or fastpath._FINISH_WORD.search(turn.text)):
            intent = "other"  # "तो।" is not "finish": a read-back needs her to actually ask for it
            upd["intent"] = "other"
        if (st.get("beneficiary_id") and self.rec.get("beneficiary_id")
                and re.sub(r"\D", "", str(st["beneficiary_id"])) != self.rec["beneficiary_id"]
                and not _ID_WORDS.search(turn.text)):
            # "हाँ, 101" (a temperature) or "register की, जिसको 120" must not change who the visit is for
            await self._event("guard", f"ignored an ID change to {st['beneficiary_id']}: she did not say ID / नंबर")
            st = {k: v for k, v in st.items() if k not in ("beneficiary_id", "beneficiary_name")}
            upd["set"] = st
            if not st:
                turn.blocked = True
                await turn.queue.put(f"ID {self.rec['beneficiary_id']} ही रखा है. बदलना हो तो 'ID है …' बोलिए."
                                     if self.lang != "en-IN" else
                                     f"I kept ID {self.rec['beneficiary_id']}. To change it, say 'ID is …'.")
        if intent == "finish" and fastpath._QUESTION.search(turn.text) and not _SAVE_WORDS.search(turn.text):
            intent = "other"  # "आपने flag क्यों नहीं किया?" is a question, not "finish and read back"
            upd["intent"] = "other"
            turn.blocked = False
        if intent == "confirm_yes" and self.state != "confirming" and not _SAVE_WORDS.search(turn.text):
            intent = "other"  # "ji" / "haan" / "yeah" while dictating: just an acknowledgement
        if intent == "cancel":
            self._reset_record()
        elif intent == "new_visit" and not self.rec.get("beneficiary_id"):
            self._reset_record()  # nothing worth keeping yet
        warn_before = set(self.rec.get("warnings") or [])
        rejected: list = []
        turn.changed = record.apply_updates(self.rec, upd.get("set") or {}, rejected)
        turn.rejected_fields = {"bp_systolic", "bp_diastolic"} if any(k == "bp" for k, _ in rejected) else set()
        turn.rejected_fields |= {k for k, _ in rejected if k != "bp"}
        if rejected:
            k0 = rejected[0][0]
            self.pending = {"bp": "bp", "temperature_c": "temperature", "temperature_f": "temperature"}.get(k0, k0)
            self.last_rejected = (self.pending, rejected[0][1])
            # an impossible value (Hb 310, BP 220/480): not stored, ask again; the LLM's "noted" would be wrong
            turn.blocked = True
            turn.rejected = True
            await self._event("validate", "impossible value not stored: " + ", ".join(f"{k}={v}" for k, v in rejected))
            await turn.queue.put(fastpath.reject_line(rejected, self.lang))
        turn.intent = intent
        record.recompute(self.rec, self.today)
        new_warn = [w for w in self.rec.get("warnings") or [] if w not in warn_before]
        if new_warn and turn.changed and intent in ("dictation", "correction"):
            turn.check_warnings = new_warn
            turn.blocked = True  # her "noted" line would be wrong: ask about the odd value instead
            await self._event("validate", "value looks unusual, asking her to confirm: " + "; ".join(new_warn))
        # keyword flags raised before the beneficiary type was known get the right group's id now
        for d in self.rec["danger_signs"]:
            if d.get("concept") and self.rec.get("beneficiary_type"):
                new_id = danger.remap(d["concept"], self.rec["beneficiary_type"])
                if new_id and new_id != d["id"] and new_id not in {x["id"] for x in self.rec["danger_signs"]}:
                    s = danger.SIGNS[new_id]
                    if d["id"] in self._announced:
                        self._announced.add(new_id)
                    d.update(id=new_id, label_en=s.en, label_hi=s.hi, guideline=s.source)
        # 3) numeric thresholds + LLM flags (union with keyword hits)
        hits = self._reconcile_thresholds()
        for sid in upd.get("danger_signs") or []:
            if sid not in danger.SIGNS:
                continue
            measured = danger.NUMERIC_SIGNS.get(sid)
            if measured and any(self.rec.get(f) is not None for f in measured):
                continue  # a sign defined by a number is decided by the measured number, not the LLM
            if measured and set(measured) & turn.rejected_fields:
                continue  # the number behind it was impossible and rejected: no flag from a misheard value
            hits.append(danger.Hit(sid, "llm", source="llm"))
        await self._escalate(turn, hits)

        if intent == "finish":
            if not record.missing_required(self.rec):
                self.state = "confirming"
        elif intent == "confirm_yes" and self.state != "confirming":
            # She said "save" + "yes" before hearing the read-back (or the LLM misread). Never save unheard
            # data and never let the LLM say "saved": read the record back first.
            turn.blocked = True
            if not record.missing_required(self.rec):
                self.state = "confirming"
            if self.lang in fastpath.LANGS and not local:
                for ln in fastpath.readback(self.rec, self.lang):
                    await turn.queue.put(ln)
            elif not local:
                turn.need_readback = True
        elif intent == "confirm_yes" and self.state == "confirming" and not record.missing_required(self.rec):
            saved = store.save(self.rec, self.asha_id, self.session_id)
            self.state = "saved"
            lines = saved_summary(saved, self.lang)
            if lines:  # the follow-ups she hears come from the saved record, never from the LLM
                turn.blocked = True
                for ln in lines:
                    await turn.queue.put(ln)
            await self.send_json({"type": "saved", "record": saved, "followups": saved["followups"]})
        elif intent == "confirm_no" and self.state == "confirming":
            self.state = "collecting"
        turn.timing["intent"] = intent
        if self.state == "confirming" and intent in ("other", "dictation") and not turn.changed and not turn.blocked:
            # after the read-back, anything unrelated gets the same fixed reminder (no improvised "all fine")
            turn.blocked = True
            await turn.queue.put(BUSY_HINT[True][self.lang == "en-IN"])
        await self.push_record()
        # the read-back is built from the record itself (never an LLM summary that could include rejected values)
        if not local and self.lang in fastpath.LANGS and (
                (intent == "finish" and self.state == "confirming")
                or (self.state == "confirming" and intent == "correction" and turn.changed)):
            turn.blocked = True
            for ln in fastpath.readback(self.rec, self.lang):
                await turn.queue.put(ln)
        await self.send_json({"type": "turn_info", "turn": turn.id, "intent": intent, "text": turn.text,
                              "changed": {f: self.rec.get(f) for f in turn.changed},
                              "set": upd.get("set") or {}, "danger_signs": upd.get("danger_signs") or [],
                              "unclear": upd.get("unclear") or "", "state": self.state,
                              "record": _snapshot(self.rec)})

    def _reconcile_thresholds(self) -> list[danger.Hit]:
        """Numeric danger checks on the current record. A flag raised by a measurement is dropped when she
        corrects that measurement (STT heard 88.4 F, she says 98.4). Keyword and LLM flags are never removed."""
        hits = danger.detect_thresholds(self.rec)
        still = {h.sign_id for h in hits}
        before = len(self.rec["danger_signs"])

        def keep(d):
            if d["source"] == "keyword":
                return True  # she SAID it ("बहुत खून आ रहा है"): never auto-cleared
            measured = danger.NUMERIC_SIGNS.get(d["id"])
            if d["source"] == "threshold" or (measured and any(self.rec.get(f) is not None for f in measured)):
                return d["id"] in still  # a number-based sign follows the current number
            return True
        self.rec["danger_signs"] = [d for d in self.rec["danger_signs"] if keep(d)]
        if len(self.rec["danger_signs"]) != before:
            self._announced &= {d["id"] for d in self.rec["danger_signs"]}
            self.rec["referral_required"] = bool(self.rec["danger_signs"])
        return hits

    EDIT_FIELDS = {"beneficiary_id", "visit_type", "weight_kg", "bp", "hb_g_dl", "temperature", "respiratory_rate",
                   "ifa_tablets_given", "vaccinations_given_today", "next_visit_date", "notes"}

    async def on_unflag(self, sign_id: str, reason: str = "") -> None:
        """She marks a danger flag as wrong on screen (e.g. raised from a misheard number). Logged, never silent."""
        before = len(self.rec["danger_signs"])
        self.rec["danger_signs"] = [d for d in self.rec["danger_signs"] if d["id"] != sign_id]
        if len(self.rec["danger_signs"]) == before:
            return
        self.rec["referral_required"] = bool(self.rec["danger_signs"])
        self._announced.discard(sign_id)
        if self.state == "confirming":
            self.state = "collecting"  # the record changed: read it back again before saving
        await self._event("unflag", f"ASHA removed the flag {sign_id} on screen as wrong" + (f": {reason}" if reason else ""))
        await self.push_record()

    async def on_edit(self, field_: str, value) -> None:
        """A correction typed on the record card (backup for very loud streets). Same validation and danger
        checks as a spoken value; an edit after the read-back sends the record back for another read-back."""
        if field_ not in self.EDIT_FIELDS or self.state == "saved":
            return
        v = str(value if value is not None else "").strip()
        try:
            if field_ == "bp":
                nums = re.findall(r"\d+", v)
                upd = {"bp_systolic": nums[0], "bp_diastolic": nums[1]} if len(nums) >= 2 else {}
            elif field_ == "temperature":
                t = float(re.findall(r"[\d.]+", v)[0])
                upd = {"temperature_f": t} if t > 50 or "f" in v.lower() else {"temperature_c": t}
            elif field_ == "vaccinations_given_today":
                upd = {field_: v}
            elif field_ in ("weight_kg", "hb_g_dl", "respiratory_rate", "ifa_tablets_given"):
                upd = {field_: re.findall(r"[\d.]+", v)[0]} if re.search(r"\d", v) else {field_: None}
            else:
                upd = {field_: v or None}
            rejected: list = []
            changed = record.apply_updates(self.rec, upd, rejected)
            if rejected:
                await self.send_json({"type": "error", "where": "edit", "message": fastpath.reject_line(rejected, "en-IN")})
                return
        except (ValueError, IndexError):
            await self.send_json({"type": "error", "where": "edit", "message": f"could not read “{v}”"})
            return
        record.recompute(self.rec, self.today)
        if self.state == "confirming" and changed:
            self.state = "collecting"  # she must hear the corrected record before it is saved
        await self._event("edit", f"edited on screen: {field_} = {v or '—'}")
        hits = self._reconcile_thresholds()
        await self._escalate(None, hits)
        await self.push_record()

    async def _escalate(self, turn: Turn | None, hits: list[danger.Hit]) -> None:
        have = {d["id"] for d in self.rec["danger_signs"]}
        new = []
        for h in hits:
            if h.sign_id in have:
                continue
            have.add(h.sign_id)
            s = danger.SIGNS[h.sign_id]
            self.rec["danger_signs"].append({"id": s.id, "label_en": s.en, "label_hi": s.hi, "evidence": h.evidence,
                                             "source": h.source, "utterance": turn.text if turn else "(edited on screen)",
                                             "guideline": s.source,
                                             "concept": h.concept})
            new.append(h.sign_id)
        if new:
            self.rec["referral_required"] = True
            await self.send_json({"type": "danger", "signs": new})
            await self.push_record()
            if turn is not None:
                await turn.queue.put(("escalation", new, danger.escalation_text(new, self.lang)))
            else:
                self._announced.update(new)
                asyncio.create_task(self._speak_standalone(danger.escalation_text(new, self.lang)))

    # ------------------------------------------------------------------ speech out
    async def _speaker(self, turn: Turn) -> None:
        """Speak queued sentences in order. The next sentence is synthesized while the current one streams,
        so there is no silent gap between sentences (Timbre takes ~0.8 s to its first chunk)."""
        nxt: asyncio.Task | None = None
        pending = None
        try:
            item = await turn.queue.get()
            while item is not None:
                cur = pending if pending is not None else self._prefetch(item)
                pending = None
                nxt = asyncio.create_task(turn.queue.get())

                def on_chunk():
                    nonlocal pending
                    if pending is None and nxt.done() and nxt.result() is not None:
                        pending = self._prefetch(nxt.result())

                await self._play(cur, turn, on_chunk)
                if isinstance(cur[0], tuple):  # ("escalation", ids, text)
                    self._announced.update(cur[0][1])
                item = await nxt
                nxt = None
                if pending is not None and pending[0] is not item:
                    pending = None
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("speaker failed")
        finally:
            if nxt is not None and not nxt.done():
                nxt.cancel()
            if pending is not None and pending[3] is not None:
                pending[3].cancel()
        if turn.first_audio_at:
            await self._send_metrics(turn)

    def _prefetch(self, item):
        """Start synthesizing a queued item now; returns (item, text, chunk queue, task)."""
        text = item[2] if isinstance(item, tuple) else item
        q: asyncio.Queue = asyncio.Queue()
        if not self.use_audio:
            q.put_nowait(None)
            return item, text, q, None

        async def run():
            try:
                async for c in self.tts.synth(text):
                    await q.put(c)
            except Exception:
                log.exception("TTS failed for: %s", text[:60])
            finally:
                await q.put(None)
        return item, text, q, asyncio.create_task(run())

    async def _play(self, pre, turn: Turn | None, on_chunk=None) -> None:
        _, text, q, task = pre
        log.info("say (turn %s): %s", turn.id if turn else 0, text)
        self.spoken.append(text)
        self._recent_speech.append((time.monotonic(), text))
        self._recent_speech = self._recent_speech[-6:]
        await self.send_json({"type": "agent_text", "text": text, "turn": turn.id if turn else 0})
        if not self.use_audio:
            if turn and turn.first_audio_at is None:
                turn.first_audio_at = time.monotonic()
            if on_chunk:
                await asyncio.sleep(0)
                on_chunk()
            return
        header = struct.pack("<I", turn.id if turn else 0)
        try:
            while True:
                if on_chunk:
                    on_chunk()
                chunk = await q.get()
                if chunk is None:
                    break
                now = time.monotonic()
                if turn and turn.first_audio_at is None:
                    turn.first_audio_at = now
                dur = len(chunk) / 2 / config.TTS_SAMPLE_RATE
                self.playing_until = max(self.playing_until, now) + dur
                await self.send_bytes(header + chunk)
        finally:
            if task is not None and not task.done():
                task.cancel()

    async def _say(self, text: str, turn: Turn | None) -> None:
        await self._play(self._prefetch(text), turn)

    UNFINISHED_HOLD_S = 2.0  # measured: "वज़न है ... 50 किलो" had a 2 s pause
    FRAGMENT_S = 4.0  # a follow-up this soon after an utterance that changed nothing is its continuation

    def _is_fragment(self, prev: Turn, t_end: float) -> bool:
        if self.pending and config.FASTPATH:  # Sakhi just asked for a value: the next words answer it (local model fills it)
            return False
        return (prev.update_applied and not prev.changed and prev.intent in ("dictation", "correction", "other", "question")
                and t_end - prev.t_end < self.FRAGMENT_S and self.state != "saved")

    def _drop_history_of(self, turn: Turn) -> None:
        for i in range(len(self.history) - 2, -1, -2):
            if self.history[i]["content"] == f'ASHA said: "{turn.text}"':
                del self.history[i:i + 2]
                return

    def _looks_like_echo(self, text: str) -> bool:
        """The mic picked up our own TTS (no/weak AEC on the phone): ignore it. Only while Sakhi is audible
        (plus a short tail): after that, words that repeat hers are her real answer ("120/80" to a BP question)."""
        if not (self.is_speaking() or time.monotonic() - self.playing_until < 1.5):
            return False
        recent = [s for ts, s in self._recent_speech if time.monotonic() - ts < 12]
        if not recent:
            return False
        tok = lambda x: set(re.findall(r"[\w\u0900-\u0DFF]+", x.lower()))
        heard, ours = tok(text), tok(" ".join(recent))
        if len(heard) < 2:
            return False
        overlap = len(heard & ours) / len(heard)
        if overlap >= 0.8:
            log.info("ignored echo (%.0f%% overlap): %s", overlap * 100, text)
            return True
        return False

    async def _speak_standalone(self, text: str) -> None:
        try:
            await self._say(text, None)
        except Exception:
            log.exception("greeting TTS failed")

    def is_speaking(self) -> bool:
        busy = self.turn is not None and self.turn.speaker is not None and not self.turn.speaker.done()
        return busy or time.monotonic() < self.playing_until

    async def _send_metrics(self, turn: Turn) -> None:
        ttfa = int((turn.first_audio_at - turn.t_end) * 1000)
        silence = int(config.VAD["x-min-silence-ms"])
        m = {"type": "metrics", "turn": turn.id, "ttfa_ms": ttfa, "ttfa_from_speech_end_ms": ttfa + silence,
             **turn.timing}
        log.info("metrics %s", m)
        await self.send_json(m)

    # ------------------------------------------------------------------ helpers
    def _append_history(self, user_text: str, assistant_raw: str) -> None:
        self.history += [{"role": "user", "content": f'ASHA said: "{user_text}"'},
                         {"role": "assistant", "content": assistant_raw}]
        self.history = self.history[-2 * HISTORY_TURNS:]

    async def push_record(self) -> None:
        await self.send_json({"type": "record", "record": self.rec, "state": self.state,
                              "missing": record.missing_required(self.rec), "followups": record.followups(self.rec)})

    async def new_visit(self) -> None:
        if self.turn:
            await self._interrupt(self.turn)
        self._reset_record()
        self.history = []
        await self.push_record()

    def _reset_record(self) -> None:
        self.rec = record.new_record(self.today)
        self.state = "collecting"
        self._announced = set()
