# Sakhi: a voice field register for ASHA workers

**BolBharat · Gnani Voice AI Innovation Challenge · PS1 Healthcare**

An ASHA worker taps **Start** once and talks while she walks: *"Pooja Yadav ke bachche ka HBNC visit, ID 1004,
weight dhai kilo…"*. The visit card on her phone fills in as she speaks. The agent says back each value it
captured. Danger signs trigger an immediate spoken "refer to PHC now" and a red flag on the record. When she says
*"bas, save karo"*, the agent reads the whole record back. She can fix anything by voice (*"nahi, weight 2.8
kilo"*), and on *"haan sahi hai"* it saves and reads out the follow-up list. She never types.

- **STT:** Gnani **Prisma v3.0** realtime WebSocket (`wss://api.vachana.ai/stt/v3/stream`)
- **TTS:** Gnani **Timbre v3.0** realtime WebSocket (`wss://api.vachana.ai/api/v1/tts`)
- **LLM:** any OpenAI-compatible model, streamed. The default is **Groq's free tier**: `openai/gpt-oss-120b`,
  falling back to `gpt-oss-20b` and then `qwen3.8-27b` on a rate limit. Gemini, OpenRouter, a local Ollama model or Claude can be set in `.env`.
- **Orchestration:** custom asyncio pipeline (no hosted agent platform)
- **Client:** a mobile web app that runs in Chrome on a basic Android phone. There is nothing to install, and it
  can be added to the home screen.
- **Data:** synthetic beneficiaries only (`data/beneficiaries.json`). There is no telephony and no phone numbers.

---

## Pages

- **`/`** is the public landing page: what Sakhi does, a live preview, a **Try it** box that shows how the local
  model reads a sentence (`POST /api/understand`, never calls the LLM), safety, speed and an FAQ.
- **`/app`** is the voice app itself (installable as a PWA). `/app?demo=1` opens it with a one-tap scripted demo
  visit.

## Quick start (a stranger can follow this)

You need Python 3.10+, a Gnani API Platform key (https://app.gnani.ai/voice) and a **free** Groq API key
(https://console.groq.com/keys, no card needed).

```bash
git clone <this repo> sakhi && cd sakhi
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then put GNANI_API_KEY and GROQ_API_KEY in .env
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

On a laptop, open **http://localhost:8000** (the landing page) and press **Start a visit**, or go straight to
**http://localhost:8000/app**. Pick a language, tap **Start**, and allow the microphone. **Watch a demo visit**
plays a scripted visit with Sakhi's real voice and needs no microphone.

**Recommended for a live demo:** also put a free **`GEMINI_API_KEY`** (https://aistudio.google.com/apikey) in
`.env`. Groq's free tier allows 200k tokens per model per day. Any extra provider key is added to the end of the
fallback chain, so questions and danger-sign double-checks still get an AI answer after Groq's daily budget runs
out. Routine dictation is understood locally and needs no AI either way.

**On a phone:** browsers only allow the microphone on HTTPS (or on localhost), so expose the server over HTTPS,
for example with `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`. Then open the `https://`
link in Chrome on the phone.

Run the tests (offline, no keys needed):

```bash
python -m pytest            # 190 tests: danger detection, negation, thresholds, local model, read-back, guardrails
```

### Try it: a 60-second script

| You say | Sakhi does |
|---|---|
| "Pooja Yadav ke bachche ka visit, ID ek zero zero chaar" | Fills the name, type and visit type (HBNC) from the register, and computes the baby's age and the vaccines due |
| "Weight dhai kilo, temperature 98.4" | Says "वज़न ढाई किलो…, नोट किया"; the fields flash on the card |
| "Nahi nahi, weight 2.8 kilo hai" | Overwrites the weight and confirms the new value |
| "Baby doodh nahi pee raha" | **Immediately**: "ध्यान दीजिए: बच्चा दूध नहीं पी रहा. यह खतरे का संकेत है. अभी PHC रेफ़र कीजिए…"; the record turns red and the phone vibrates |
| "Bas, save karo" | Reads the whole record back and asks "सब सही है?" |
| "Haan sahi hai" | Saves the record and says the follow-ups (refer to PHC, vaccines due, next visit on day 14) |

The **फ़ॉलो-अप** tab lists every open action across saved visits, urgent referrals first.

### The app (screens)

| Screen | What it shows |
|---|---|
| **Visit** | A mic orb that shows listening / hearing / thinking / speaking, with a live waveform of her voice or Sakhi's. Below it: live captions, a three-step progress bar (speak → check → saved) and the record card, whose fields flash as they fill. Vaccines due appear as chips, and a red banner shows danger signs. On a wide screen a side panel shows the **speech pipeline** (STT → danger check → LLM → TTS lighting up per turn) with time-to-first-audio figures and a per-turn sparkline |
| **Danger alert** | A full-screen red card with each sign, what she said that triggered it, and the guideline it comes from. The phone vibrates. Sakhi says "refer to PHC now" in the same moment |
| **Voice log** | One card per turn: what she said (voice, typed or demo), every reply from Sakhi, the intent the AI understood, which fields changed, and a timing bar (STT, LLM first token, first audio). Pipeline decisions appear inline: *merged* (she paused mid-sentence), *waiting* (number still coming), *check* (odd value confirmed), *echo* (own voice ignored), *barge-in*, *guard* (safety filter), *busy* (free-tier limit). Every session is saved as text to `data/voicelogs/<session>.jsonl` (no audio is stored). Past sessions can be reopened, and any log can be exported as JSON |
| **Follow-ups** | Today's counts (visits, PHC referrals, urgent, vaccines due) and every open action, grouped as urgent / vaccines / next visits / to verify, with overdue dates in red |
| **Register** | The synthetic beneficiaries, with weeks of pregnancy or the baby's age, vaccines due and the suggested next visit. **Start visit** opens a visit for that person |
| **Records** | Saved visits, which expand to show every field. A **CSV** export is at `/api/records.csv` |

**Interactive extras.** On the start screen, **Test microphone** shows a live level meter. **Tap any field**
on the record card (or the ID) to correct it in a bottom sheet with a number keyboard and quick chips (BP
presets, vaccine names, visit types). This is a backup for very loud streets: an edit gets the same validation
and danger checks as a spoken value, and an edit made after the read-back sends the record back for another
read-back. A saved visit shows a summary card with its follow-ups. The app bar switches language mid-session,
**?** opens help, and on a laptop keyboard shortcuts work (Space = done/yes, M mute, N new visit, D demo,
T type, 1–5 tabs).

**Demo mode (▶).** Four scripted visits: a newborn with danger signs, an ANC visit with a spoken correction,
high BP with headache, and a child immunisation visit. Each line is sent as if she had said it, and Sakhi
answers live with real Timbre audio, waiting for each reply before sending the next line. **Start without
microphone** on the first screen gives the same app with typed input, for laptops without a mic or a noisy
venue. Light and dark themes are both supported.

---

## How it works

```
 Phone (Chrome)                         Server (FastAPI, asyncio)                          Gnani / LLM
 ─────────────                          ─────────────────────────                          ──────────────
 mic → AudioWorklet → 16 kHz PCM16 ──►  /ws ──► SttStream (1024-byte frames, real time) ─►  Prisma v3.0 STT
   (echoCancel + noiseSuppression)            ◄── speech_start / processing / transcript ◄─
                                               │
                                               ├─ 1. keyword danger detector ──(instant)──► escalation sentence
                                               ├─ 2. LLM:    <update>{json}</update><say>…  (streamed)
                                               │      update → record merge → thresholds → LLM flags → UI card
                                               │      say    → split into sentences ───────┐
 speaker ◄─ 24 kHz PCM16 (turn-tagged) ◄──────  └─ speaker queue ─► TtsClient ─────────────┴►  Timbre v3.0 TTS
```

### Streaming at every stage
- **Mic to STT.** The browser resamples to 16 kHz in an AudioWorklet and sends 512-sample frames as they are
  captured. The server forwards exact 1024-byte frames to Prisma at real-time cadence.
- **STT to LLM.** Prisma's VAD closes each utterance and returns the transcript. The LLM call starts immediately.
- **LLM to TTS.** The LLM streams a short JSON update first and then the spoken line. The update reaches the
  card as soon as `</update>` arrives. The spoken line is cut at sentence ends and each sentence goes to Timbre
  as soon as it is complete, so first audio does not wait for the whole reply.
- **TTS to phone.** Raw PCM chunks are forwarded as they arrive and scheduled back to back with Web Audio.

### Latency (time to first audio)
- The persistent STT and TTS sockets are opened (and warmed) when the session starts, not on each turn.
- The danger escalation is a fixed template that goes to TTS before the LLM is called.
- The system prompt is fixed and prompt-cached. History holds only short past utterances; the current record
  goes in the last message.
- Groq serves gpt-oss-120b (reasoning effort low) fast: the first LLM token arrives in 0.5–1.2 s. The
  prompt is about 1,450 tokens. On a 429, the next model in `LLM_MODELS` is tried immediately; each Groq
  model has its own free per-minute budget, so this roughly triples capacity.
- The UI shows **⏱ time to first audio** for every turn. Tap it to see the breakdown: STT latency, LLM first
  token, first audio, and the VAD silence window. Server logs print the same figures (`metrics …`).

### Local understanding model (fewer API calls)

Most of a visit is routine dictation, and sending each line to the LLM costs about 2.5k tokens and 0.5–1 s. The
free Groq tier allows 200k tokens per model per day (about 80 turns). Sakhi therefore understands routine
speech on the server first and calls the LLM only for what remains:

1. **Rules** (`server/fastpath.py`). Hindi, Hinglish and English patterns for ID / name, visit type, weight,
   BP, Hb, temperature, breathing rate, IFA, vaccines, next-visit dates ("15 दिन बाद", "25 दिसंबर"), corrections
   ("नहीं, …"), simple negatives ("IFA नहीं ली"), "बस save करो" and "हाँ सही है", plus the questions Sakhi asks
   herself ("वज़न है" → "वज़न कितना है?" → "50 किलो" fills the weight). The read-back is built from a template.
   A sentence is handled locally **only if every word is understood**. Danger words, questions and unclear
   speech always go to the LLM.
2. **Learning from the LLM** (`server/learned.py`). When the LLM understands a sentence the rules could not,
   its shape is stored, with numbers and names as slots. After two identical answers that shape is handled
   locally. It never learns from danger signs, questions, or values not present in the sentence, and a shape is
   blocked forever if the LLM contradicts itself.

| Measured (`eval/fastpath_corpus.py`, `eval/fastpath_heldout.py`) | Local | Exactly right | Danger / unclear sent to LLM |
|---|---|---|---|
| Practice set, 1,212 generated sentences | 99.8 % | 100 % | 20 / 20 |
| Held-out set, 30 new phrasings | 97 % | 100 % | 17 / 17 |
| Fresh set, 25 sentences (76 % before its misses were fixed) | 96 % | 100 % | 6 / 6 |

On a replay of a real 13-turn visit, the local model made **0 LLM calls**, and Sakhi started speaking in
120–350 ms (it was 600–1,200 ms). The voice log marks these turns **⚡ local** or **🎓 learned**. Set
`FASTPATH=0` to switch both off.

### Session history, problem reports and the bug inbox

Every session is saved as text in `data/voicelogs/<session>.jsonl`; audio is never stored. Each turn records:

- what Prisma heard;
- the final utterance;
- who understood it (local / learned / LLM / failed) and the LLM's raw output;
- the fields changed, the full record after the turn;
- Sakhi's replies, danger flags, pipeline events (merge, hold, rejected value, guard, busy) and timings.

The session ends with a snapshot of the final record.

- **⚑ Report a problem.** On any turn in the voice log, say what went wrong and what Sakhi should have done.
  Reports go to `data/feedback.jsonl`.
- **Dataset.** `GET /api/dataset.jsonl` (the "⬇ Dataset" button) gives one row per turn across all sessions,
  with reports attached. `GET /api/dataset/summary` gives the counts.
- **Bug inbox.** `PYTHONPATH=. python eval/session_report.py` writes `data/BUG_REPORT.md`, which lists:
  - reported problems;
  - failed turns;
  - rejected or odd values;
  - safety-guard drops;
  - every sentence that needed the LLM, re-run through the current local model. These are the candidates
    for new local rules.

  Run it before fixing bugs or widening the local model.

### Speech-layer depth
- **Endpointing.** The VAD is tuned for dictation in noise (threshold 0.5, min speech 120 ms, min segment 120 ms, silence 600 ms, max segment 25 s). These were measured on
  the live API: they keep a one-word "हाँ" at 5 dB SNR, where 0.6/200/200 dropped it, and pure street noise still
  gives no transcript. There is also a big **✓ बोल दिया** button that sends Prisma's
  `commit` to close the utterance at once when street noise keeps the VAD open.
- **Split dictation is merged.** If she pauses mid-sentence and carries on before the LLM has applied the
  first half, the two halves are merged into one turn, so she isn't interrupted. If a reply already went out
  but changed nothing ("hemoglobin है मेरा" → "Hb कितना?"), a follow-up within 4 s ("8") is joined to it and
  answered as one sentence. If she names a measurement with no number, Sakhi waits 1 s for it before answering
  (danger-sign phrases never wait).
- **Barge-in, pause first.** When Prisma reports `speech_start` while Sakhi is talking, the phone *pauses*
  playback (it doesn't drop it). If a real transcript arrives, the turn is cancelled, the TTS socket is closed
  so stale audio stops, the client flushes its buffer (audio is tagged with the turn id) and the new utterance is
  handled. If it was a door slam or honk (an empty transcript), playback resumes where it stopped.
- **Implausible readings are confirmed, and measurement flags follow corrections.** If STT hears 88.4 °F, the
  agent asks her to confirm it. When she corrects it to 98.4, the "baby cold" flag raised by that number is
  removed. Flags from her own words ("jhatke aa rahe hain") are never removed.
- **Echo guard.** While Sakhi is audible (plus 1.5 s), a transcript that is ≥ 80 % the same words as what she
  just said is dropped as speaker echo. This covers phones with weak echo cancellation. After that, repeating
  her words is treated as a real answer ("80/120 या 120/80?" → "120/80").
- **Gap-free speech.** Timbre sends two `complete` messages per request. Each request now skips leftovers
  until its own `start`, so a sentence can't be cut off by the previous one. A small socket pool synthesizes the
  next sentence while the current one plays, so the first chunk arrives in about 0.2 s and there is no
  silence between sentences.
- **No AI-made referrals or advice.** A sentence about referral or the PHC is spoken only when a danger
  sign is flagged on the record; otherwise it is dropped, and the drop is logged. The lines spoken after saving
  ("सेव हो गया. टीके बाकी: Td-2. अगली विज़िट 18 दिसंबर.") are built from the saved record itself, not from the
  LLM, so they can't contain invented timing or treatment advice.
- **Odd values get a question, not "noted".** When a value fails validation (BP 80/120, a 28 kg newborn,
  88.4 °F), the agent drops its "noted" line and asks her to confirm, in the same turn.
- **Code-mix.** Prisma gets the session language, output format `transcribe` (numbers as digits), and **keyterms**: local health words ("TT injection", "ANC checkup",
  "IFA goli", "pasli chalna", "peeliya"…) plus every name in the register in both scripts, and a `topic` hint.
  **Number words are converted to digits in code** (`server/numwords.py`) before the LLM sees them, for example
  "अट्ठानवे point चार" → 98.4, "एक सौ बीस बटा अस्सी" → 120 बटा 80, "ढाई/सवा/पौने", and spoken ids. In testing the
  LLMs often misread these (98 → 88). The LLM still resolves relative dates ("15 din baad"). Replies keep
  numbers as digits, which Timbre reads correctly ("2.8" → "दो दशमलव आठ"), so the LLM never spells a number
  itself.
- **Noise.** The browser's noise suppression, AGC and echo cancellation are on, along with a stricter VAD
  gate, and every value is confirmed out loud so a misheard number is caught at once. Out-of-range values
  (a 28 kg newborn, for example) produce a warning, and the agent asks her to confirm.

### The record and read-back
Fields: beneficiary ID and name, beneficiary type, visit type (ANC/PNC/HBNC/Immunisation), weight, BP, Hb,
temperature (°C or °F), breathing rate, IFA tablets given, vaccines given today, **vaccines due**, next visit
date, notes, danger signs, and whether a referral is required.

- Saying an ID or a name pulls the synthetic register entry, which gives the gestation weeks (from LMP) or the
  baby's age (from DOB).
- **Vaccines due** are computed from the National Immunization Schedule (and Td for pregnancy), using what was
  already given plus what she gave today.
- **Next visit** is suggested from the HBNC schedule (days 1, 3, 7, 14, 21, 28, 42), monthly ANC (weekly after
  36 weeks) or the next vaccine date. A date she says always wins over the suggestion.
- **State machine:** `collecting → (she says done) → confirming → (she says yes) → saved`. Nothing is saved
  without her spoken confirmation, and the code blocks saving while required fields (ID, visit type) are
  missing.

### Danger-sign escalation
See **[docs/DANGER_SIGNS.md](docs/DANGER_SIGNS.md)** for the full list and its sources: the MoHFW MCP Card, the
HBNC Operational Guidelines and IMNCI general danger signs.

Three detectors run as a union. A detector can add a flag but can never remove one.
1. **Keywords** in Hindi, Hinglish and English plus all 10 languages. They handle negation ("bleeding nahi
   hai") and skip look-alikes ("blood pressure", "khoon ki kami ki jaanch").
2. **Numeric thresholds** on the record: BP ≥ 140/90, Hb < 7, newborn temperature ≥ 37.5 °C or < 35.5 °C,
   newborn breathing ≥ 60/min, newborn weight < 1.8 kg.
3. **LLM flags**, restricted to the list ids and biased towards recall.

When a flag is raised, a **fixed template** in her language says it is a danger sign, tells her to refer to
the PHC now, and says the record is flagged. The agent sets `referral_required` and puts the referral at the top
of the follow-ups. The escalation text never comes from the LLM, so it cannot drift into diagnosis.

---

## Responsible AI and compliance

| Baseline | How it is met |
|---|---|
| Synthetic data only | The register is synthetic. Eval audio is Timbre's own synthetic voice plus generated noise. There are no real recordings and no patient data. |
| No diagnosis or treatment advice | The system prompt forbids it. Escalations are fixed templates. An **output guardrail** (`llm.is_unsafe`) replaces any spoken line that mentions medicines, doses or diagnoses with "I can only help fill the register, ask the ANM/PHC doctor". Covered by tests. |
| Danger list from a public guideline | `docs/DANGER_SIGNS.md`, with citations. |
| Human in the loop | Every record is read back and needs her spoken "yes" before it is saved. Corrections are by voice. |
| Transparency | The agent introduces itself as an AI assistant. The setup screen states its limits. |
| Privacy | Audio is streamed for transcription and never stored. The Gnani and LLM keys stay on the server (the browser never sees them). Records are stored locally in `data/records.jsonl`. |
| No voice cloning | A stock Timbre v3.0 voice (`Shivani`). |

---

## Evaluation harness

```bash
python -m eval.run_eval --mode text --pace 6         # transcripts → agent (LLM + danger logic), realistic pacing
python -m eval.run_eval --mode audio --snr 5         # Timbre TTS → street-noise mix → Prisma stream → agent
python -m eval.run_eval --mode audio --noise street.wav --snr 0   # bring your own 16 kHz noise recording
```

**Measured results (text mode, live `gpt-oss-120b` on Groq, paced 6 s per turn):** field accuracy **27/28
(96 %)**, danger-sign recall **100 %** (8/8), **0** false alarms, **8/8** saves correct. First LLM audio
arrived in 0.5–1.2 s, and danger escalations were instant. The one miss was a Telugu number word
(62 → 52), which the agent read back so she could correct it.

Live STT check (Timbre voice, synthetic street noise at 5 dB SNR, Prisma v3.0 `hi-IN`):
"पूजा यादव के बच्चे का visit IB 1004, बच्चे का weight 2.8 kg है।" and "सुनीता को ANC checkup में TT injection लगाया,
IFA goli 30, BP 120/80।". Both were correct.

There are 8 scripted visits in `eval/cases.json`, covering Hinglish, Hindi, English, Marathi and Telugu, with
corrections, negated signs, numeric thresholds, multi-sign notes and the save flow. The harness reports field
accuracy, danger-sign recall, missed and false-alarm counts, a rubric-style penalty (each **miss counts
double**), what Prisma heard, and what the agent said. Details are written to `eval/results.json`.

---

## Project layout

```
server/
  main.py        FastAPI app: phone client + /ws session proxy + /api/records, /api/followups, /api/danger-signs
  agent.py       per-session orchestrator: turns, merging, barge-in, echo guard, escalation, state machine, metrics
  gnani_stt.py   Prisma v3.0 realtime client (headers, VAD tuning, keyterms, commit, auto-reconnect)
  gnani_tts.py   Timbre v3.0 realtime client (raw PCM 24 kHz, cancel on barge-in)
  llm.py         prompt, provider switch (Groq/Gemini/OpenRouter/Ollama/Claude), streaming parser, guardrail
  danger.py      danger-sign list, keyword + threshold detectors, escalation templates (10 languages)
  numwords.py    Hindi number words → digits (deterministic, before the LLM)
  record.py      schema, register lookup, immunisation schedule, HBNC/ANC next visit, validation, follow-ups
  store.py       local JSON-lines store
web/             index.html, app.js, capture-worklet.js (16 kHz resampler), style.css, PWA manifest
data/            synthetic register
eval/            scripted cases + scored runner (text or noisy audio)
tests/           66 offline tests
docs/            danger-sign list and sources
```

## Configuration

See `.env.example`. Settings worth tuning on site: `VAD_THRESHOLD` (raise it for noisier streets),
`VAD_MIN_SILENCE_MS` (lower is snappier, higher splits fewer sentences), `TTS_VOICE`, `TTS_SPEED` (0.8 gives a
slower read-back) and `LLM_EFFORT`.

## Known limitations
- The live Prisma v3.0 stream accepts **one** `language_code` per session. Sending a list such as
  `hi-IN,en-IN` closes the socket with code 1008, even though the docs allow it. In our tests a single Indic
  locale still kept English health words in Latin script ("ANC checkup में TT injection लगाया, BP 120/80").
- **Free-tier limits (Groq, measured):** 8,000 tokens per minute and **200,000 tokens per day** per model
  (about 80 LLM turns per model per day). The local model keeps most visits at 0–1 LLM calls; when the daily
  budget runs out, Sakhi still handles routine dictation locally. The per-minute limit is:
  One turn is about 1.5k tokens, so the three-model chain handles roughly 14 turns a minute and about 3,000
  turns a day. That is enough for testing and a live demo; don't run the eval in a loop on demo day. A model that
  returns 429 is skipped until its window refills. If every model is busy, Sakhi says "एक सेकंड, दीदी", understands
  what it can locally (`fastpath.py`, relaxed mode: any values in the sentence) instead of waiting. Only if
  nothing is understood does it wait once (at most 5 s) and retry before asking her to repeat. Another
  fallback is `LLM_PROVIDER=gemini` with a free Gemini key.
- Number words in languages other than Hindi are left to the LLM, and those are the weakest spot (a Telugu
  "62" became 52 in testing). The spoken read-back is the safety net.
- The escalation templates and UI labels outside Hindi and English were written by the team and need review by
  a native speaker. Danger-sign labels are in Hindi or English.
- The keyword lists for the southern and eastern languages are smaller than the Hindi ones. In those languages
  the LLM flag is the main detector.
- Barge-in depends on Prisma's `speech_start`, so very short interjections may be gated out by the noise-tuned
  VAD. Lower `VAD_MIN_SPEECH_MS` if that happens.
- The local JSON store stands in for a sync to the real ANMOL/RCH backend.
