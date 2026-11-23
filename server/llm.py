"""One LLM call per user turn. The model streams a short JSON update first, then the spoken reply.

    <update>{...}</update>
    <say>spoken text</say>

The update is applied to the record as soon as </update> arrives (UI card refreshes), and the
<say> text streams sentence by sentence into TTS, so first audio does not wait for the full reply.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable

from . import config, danger, record

log = logging.getLogger("llm")

_client = None


def client():
    """Anthropic client, or an OpenAI-compatible one (Groq, Gemini, OpenRouter, Ollama...)."""
    global _client
    if _client is None:
        if config.LLM_PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.AsyncAnthropic(max_retries=1, timeout=anthropic.Timeout(30.0, connect=5.0))
        else:
            from openai import AsyncOpenAI
            # no SDK retries: a 429 falls straight through to the next model instead of sleeping ~20 s
            _client = AsyncOpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY or "none",
                                  max_retries=0, timeout=20.0)
    return _client


class _Final:
    cache_read = 0
    model = ""


_WAIT = object()


_COOLDOWN: dict[str, float] = {}  # model -> monotonic time until which it is known to be rate-limited


_backup_clients: dict = {}


def _chain() -> list[str]:
    """Primary provider's models, then backup providers' models ("gemini:gemini-2.5-flash")."""
    return list(config.LLM_MODELS) + [b[0] for b in config.LLM_BACKUPS]


def _client_for(model: str):
    """(client, model name to send) for a chain entry."""
    for label, base, key, name in config.LLM_BACKUPS:
        if model == label:
            if label not in _backup_clients:
                from openai import AsyncOpenAI
                _backup_clients[label] = AsyncOpenAI(base_url=base, api_key=key, max_retries=0, timeout=20.0)
            return _backup_clients[label], name
    return client(), model


def _model_attempts():
    """The model chain (skipping models still cooling down from a 429), then one short wait for the
    free-tier per-minute window to refill and the full chain again."""
    now = time.monotonic()
    chain = _chain()
    ready = [m for m in chain if _COOLDOWN.get(m, 0) <= now]
    yield from ready or chain
    yield _WAIT
    yield from chain


def all_busy() -> bool:
    """Every model in the chain is cooling down from a 429 right now."""
    now = time.monotonic()
    return config.LLM_PROVIDER != "anthropic" and all(_COOLDOWN.get(m, 0) > now for m in _chain())


def _retry_after(e: Exception | None) -> float:
    try:
        h = getattr(getattr(e, "response", None), "headers", {}) or {}
        if h.get("retry-after"):
            return float(h["retry-after"])
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(e or ""))
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 2.0


def _is_retryable(e: Exception) -> bool:
    code = getattr(e, "status_code", None)
    return code in (408, 409, 413, 429, 500, 502, 503, 504) or type(e).__name__ in ("APIConnectionError", "APITimeoutError")


async def _stream_text(history: list[dict], context: str, final: _Final, on_wait=None):
    """Yield text deltas from the configured provider. On an OpenAI-compatible endpoint, a rate-limit or
    server error before the first token moves on to the next model in LLM_MODELS."""
    messages = history + [{"role": "user", "content": context}]
    if config.LLM_PROVIDER == "anthropic":
        kwargs = dict(
            model=config.LLM_MODEL, max_tokens=2000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=messages, output_config={"effort": config.LLM_EFFORT},
        )
        if config.LLM_FAST:
            kwargs["speed"] = "fast"
        async with client().messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
            msg = await stream.get_final_message()
            final.cache_read = getattr(msg.usage, "cache_read_input_tokens", 0) or 0
            final.model = config.LLM_MODEL
        return
    last_err: Exception | None = None
    for model in _model_attempts():
        if model is _WAIT:  # every model is rate-limited: Groq frees tokens within seconds, so wait once
            # wait for the soonest model to free up (Groq says when), capped so she is never left hanging long
            now = time.monotonic()
            soonest = min([_COOLDOWN.get(m, now) - now for m in _chain()] + [_retry_after(last_err)])
            if soonest > 8.0:
                log.warning("all LLM models out of budget for %.0fs: not waiting", soonest)
                break  # daily budget: waiting a few seconds cannot help, let the caller fall back locally
            delay = min(max(soonest, 1.0), 5.0)
            log.warning("all LLM models rate-limited, waiting %.1fs", delay)
            if on_wait is not None:
                await on_wait(delay)
            await asyncio.sleep(delay)
            continue
        extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
        if "qwen3" in model:
            extra = {"reasoning_effort": "none"}
        cl, name = _client_for(model)
        try:
            stream = await cl.chat.completions.create(
                model=name, max_tokens=600, temperature=0.2, stream=True,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages, **extra,
            )
        except Exception as e:
            if not _is_retryable(e) and getattr(e, "status_code", None) != 400:
                raise
            log.warning("LLM %s unavailable (%s), trying next", model, str(e)[:120])
            if getattr(e, "status_code", None) == 429:  # don't pay this round trip again on the next turns
                # per-minute limits clear in seconds; the daily budget ("try again in 3m") takes minutes
                _COOLDOWN[model] = time.monotonic() + min(max(_retry_after(e), 2.0), 900.0)
            last_err = e
            continue
        final.model = model
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        finally:
            try:
                await stream.close()
            except Exception:
                pass
        return
    raise last_err or RuntimeError("no LLM model available")


def _registry_for_prompt() -> str:
    return "\n".join(f"{b['id']} {b['name']} / {b['name_local']} ({b['type']})" for b in record.REGISTRY)


SYSTEM_PROMPT = f"""You are Sakhi, a voice assistant filling the home-visit register for an ASHA worker in India.
She dictates while walking, in an Indian language mixed with English health words; transcripts may be noisy.
She only hears you through a phone speaker.

Each turn, output exactly:
<update>{{"intent":"...","set":{{...}},"danger_signs":[...],"unclear":""}}</update>
<say>one short spoken line</say>

intent: dictation | correction ("nahi, weight 2.8 hai") | finish ("bas", "ho gaya", "save karo", "done") |
confirm_yes | confirm_no | new_visit (next house; if the current record has an id and is unsaved, use finish
instead) | cancel | question (she asks what a term means) | other.
confirm_yes ONLY when state is "confirming" (you just read the record back) and she agrees. In "collecting",
"haan", "ji", "yes", "ok" are just acknowledgements: intent other, and any values in the same sentence still
go in set.

set = ONLY fields stated or corrected in THIS utterance. Fields: beneficiary_id (the number she says, digits
only, e.g. "1025"; any number is fine, a new beneficiary need not be in the register; never guess or change an
id she did not clearly say), beneficiary_name, beneficiary_type (pregnant|postpartum|newborn|child), visit_type
(ANC|PNC|HBNC|Immunisation|Other), weight_kg ("2 kilo 800 gram"=2.8, "dhai"=2.5, "sava teen"=3.25,
"paune teen"=2.75), bp_systolic, bp_diastolic ("120 by 80"), hb_g_dl, temperature_c or temperature_f
(the unit she says, or 95-110 means F; never convert between units), respiratory_rate ("saans 40 per minute"), ifa_tablets_given (0 if none were given/taken, never null), vaccinations_due_noted (vaccines she says are DUE, list),
gestation_months ("7 months pregnant"), age_days (baby's age in days), vaccinations_given_today (list like ["Td-2"],
["Penta-1","OPV-1","RVV-1","PCV-1","fIPV-1"]; TT means Td, rota means RVV, IPV means fIPV),
next_visit_date (YYYY-MM-DD; resolve "15 din baad", "next Monday" from today), notes (short English,
keep earlier notes). null clears a field.
A baby has its own id, separate from the mother's ("Pooja ka baccha" = the baby's id).

danger_signs: ids below that THIS utterance reports as present (not ones said to be absent). If unsure,
include it: missing a danger sign is far worse than an extra referral.
unclear: name a key value you could not make out, else "".
If your last line asked for a value and she now says just a number ("8"), that number IS that value.

<say> rules:
- Always the session language in its own script (Hindi -> Devanagari), also for the read-back; only English
  health words (weight, BP, Hb, ID, visit) stay English. Call her "didi" (or local). Never say field names
  like visit_type or hb_g_dl; say them as she would ("विज़िट", "Hb").
- Numbers as DIGITS exactly as recorded ("2.8 किलो", "120/80"); never spell numbers as words. Ids with
  spaces ("1 0 0 4"). Dates as "24 नवंबर".
- dictation/correction: confirm just what you captured, max 12 words, as a statement ("weight 53 kg, noted").
  Do NOT end with "sab sahi?" or any yes/no question. Ask only for an unclear value, a validation warning,
  or (once) a missing beneficiary id / visit type.
- question: explain the register term in one simple sentence, then continue. Terms: beneficiary id = the
  woman's or child's number in your register; ANC = check-up during pregnancy; PNC = check-up of the mother
  after delivery; HBNC = home visit for the newborn baby; Immunisation = vaccination visit; Hb = haemoglobin
  (blood level, g/dL); BP = blood pressure; IFA = iron-folic acid tablets; Td/TT = tetanus injection;
  ANM = सहायक नर्स मिडवाइफ (Auxiliary Nurse Midwife, the health worker at the sub-centre); PHC = प्राथमिक
  स्वास्थ्य केंद्र; ASHA = आशा, the village health worker. Explaining these terms is allowed; normal ranges,
  doses and medical advice are not.
- You are a woman: in Hindi use feminine first person ("समझ गई", "नोट कर लिया", "बता रही हूँ"), never "समझ गया".
- Numbers: copy exactly what she said. Never move a decimal point or "fix" a number (420 stays 420; the system
  rejects impossible values and asks her again).
- Do not change visit_type or beneficiary_type unless she clearly says the visit type itself ("ये PNC visit है").
  A vaccine or word like "HBNC टीका" is not a visit-type change.
- IFA: ifa_tablets_given only when she says a number; "IFA ली/दी" without a number -> leave it empty and put
  "IFA given, count not said" in notes. Never invent a count.
- If she asks what is left ("और क्या बताना है?"), name the important fields that are still empty.
- After the read-back (state "confirming"), if she says something unrelated, only say: "सही है तो 'हाँ सही है'
  बोलिए, या जो बदलना है वो बताइए." Never claim the record is fine or move on to another house.
- Never say a danger flag was added, kept or removed: the system sets flags from what she says and from the
  measured numbers. If she says a flag is wrong, ask her to correct the value (e.g. "BP 120 by 80 बताइए").
- finish (or a correction while confirming): read back the WHOLE record (name, id, visit type, every value,
  vaccines today, vaccines due, next visit or the suggested date, danger signs in plain words, never ids),
  then ask "sab sahi hai?".
  If required fields are missing, ask for them instead.
- confirm_yes while confirming: say saved, then the follow-ups (PHC referral if flagged, vaccines due, next
  visit). Do not ask another question.
- A fixed referral message is spoken for danger signs before your line; do not repeat it.
Safety: never diagnose or suggest medicines, doses, remedies or treatment; for medical questions (what to give,
what illness it is) say you only fill the register and she should ask the ANM or PHC doctor. Off-topic requests
(not about the visit): say in one line that you only help fill the visit register. Never call a value high/low or
advise referral yourself; referral is announced by the system only for flagged danger signs. Never invent values. Max 60 words (read-back 90).

DANGER SIGNS:
{chr(10).join(f"{s.id}: {s.en}" for s in danger.SIGNS.values())}

REGISTER (synthetic):
{_registry_for_prompt()}
"""

# Words in a reply that suggest treatment advice; such a reply is replaced (output guardrail).
_UNSAFE = re.compile(
    r"\b(\d+\s*mg|paracetamol|crocin|antibiotic|amoxicillin|dose|dosage|dawai do|dawa do|dawai de|ORS (de|do)|"
    r"diagnos|you have (an? )?(infection|disease)|it is (pre-?eclampsia|sepsis|pneumonia|malaria))",
    re.IGNORECASE,
)
_UNSAFE_LOCAL = re.compile(r"(दवाई दे|दवा दे|दवा दीजिए|दवाई दीजिए|गोली खिला|मिलीग्राम|एंटीबायोटिक|पैरासिटामोल)")

SAFE_FALLBACK = {
    "hi-IN": "माफ़ कीजिए, मैं सिर्फ़ रजिस्टर भरने में मदद कर सकती हूँ. इलाज के लिए ANM या PHC डॉक्टर से पूछिए.",
    "en-IN": "Sorry, I can only help fill the register. Please ask the ANM or the PHC doctor about treatment.",
}


def is_unsafe(text: str) -> bool:
    return bool(_UNSAFE.search(text) or _UNSAFE_LOCAL.search(text))


def turn_context(rec: dict, state: str, lang: str, today: str, utterance: str, detected: list[str]) -> str:
    public = {k: (int(v) if isinstance(v, float) and v.is_integer() else v)  # read back "95", not "95.0"
              for k, v in rec.items() if k not in ("warnings", "danger_signs") and v not in (None, [], {}, False)}
    if rec.get("danger_signs"):
        public["danger_signs"] = [d["label_hi"] if lang in ("hi-IN", "mr-IN") else d["label_en"] for d in rec["danger_signs"]]
    return (
        f"today: {today}\nsession language: {config.LANG_NAMES.get(lang, 'Hindi')} ({lang})\n"
        f"state: {state}\nmissing required fields: {record.missing_required(rec)}\n"
        f"validation warnings: {rec.get('warnings') or []}\n"
        f"danger signs already flagged: {len(detected)}\n"
        f"current record: {json.dumps(public, ensure_ascii=False)}\n\n"
        f"ASHA said: \"{utterance}\""
    )


def parse_update(raw: str) -> dict:
    """Tolerant parse of the <update> block: models sometimes add stray characters ("}>", '}"') or extra text."""
    m = re.search(r"<update>(.*?)</update>", raw, re.S)
    body = m.group(1) if m else raw
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return {}
    chunk = body[start:end + 1]
    for cand in (chunk, re.sub(r",\s*}", "}", chunk)):
        try:
            upd = json.loads(cand)
            break
        except json.JSONDecodeError:
            continue
    else:
        log.warning("bad update json: %s", chunk[:200])
        return {}
    if not isinstance(upd, dict):
        return {}
    import difflib
    fixed = []
    for sid in upd.get("danger_signs") or []:
        if not isinstance(sid, str):
            continue
        sid = sid.strip().upper()
        if sid not in danger.SIGNS:  # e.g. "MAT_HEADAGE_VISION"
            close = difflib.get_close_matches(sid, list(danger.SIGNS), n=1, cutoff=0.8)
            sid = close[0] if close else ""
        if sid:
            fixed.append(sid)
    upd["danger_signs"] = fixed
    return upd


SENTENCE_END = re.compile(r"([.!?।|]+)\s")  # needs following space: "2." + "8" is not a sentence end


async def run_turn(history: list[dict], context: str,
                   on_update: Callable[[dict], Awaitable[None]],
                   on_sentence: Callable[[str], Awaitable[None]],
                   on_wait: Callable[[float], Awaitable[None]] | None = None) -> tuple[str, dict]:
    """Stream one turn. Returns (raw assistant text, timing dict)."""
    t0 = time.monotonic()
    timing: dict = {}
    raw = ""
    said_upto = 0  # index in raw of text already sent to TTS
    update_done = False
    say_start = None
    final = _Final()
    stream = _stream_text(history, context, final, on_wait)
    try:
        async for text in stream:
            if "ttft_ms" not in timing:
                timing["ttft_ms"] = int((time.monotonic() - t0) * 1000)
            raw += text
            if not update_done and "</update>" in raw:
                update_done = True
                upd = parse_update(raw)
                timing["update_ms"] = int((time.monotonic() - t0) * 1000)
                await on_update(upd)
            if update_done and say_start is None:
                i = raw.find("<say>")
                if i >= 0:
                    say_start = said_upto = i + len("<say>")
            if say_start is not None:
                end = raw.find("</say>", say_start)
                body = raw[said_upto:end if end >= 0 else len(raw)]
                # flush complete sentences (or the rest, once </say> arrives)
                last = None
                for m in SENTENCE_END.finditer(body):
                    last = m
                if end >= 0:
                    if body.strip():
                        await on_sentence(body.strip())
                    said_upto = end
                    break
                if last is not None and last.end() >= 12:
                    await on_sentence(body[:last.end()].strip())
                    said_upto += last.end()
    finally:
        try:
            await stream.aclose()
        except RuntimeError:  # generator already finishing on cancellation
            pass
    if not update_done:  # model ignored the format: treat everything as speech
        await on_update({})
        body = re.sub(r"<[^>]+>", "", raw).strip()
        if body:
            await on_sentence(body)
    timing["llm_total_ms"] = int((time.monotonic() - t0) * 1000)
    timing["cache_read"] = final.cache_read
    timing["model"] = final.model
    return raw, timing
