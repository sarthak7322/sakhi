"""Scored evaluation of the agent on synthetic visit dictations.

  python -m eval.run_eval --mode text            # transcripts straight into the agent (LLM + danger logic)
  python -m eval.run_eval --mode audio --snr 5   # Timbre TTS -> noise mix -> Prisma STT stream -> agent
  python -m eval.run_eval --mode audio --noise path/to/street.wav --snr 0

Audio mode uses Timbre's own synthetic voice (not cloning) as the "speaker" and mixes in noise at the given
SNR, so it exercises the real speech layer without any real recordings. Bring your own street/household noise
WAV (16-bit mono, any rate is fine if 16 kHz; otherwise resample first) for realistic conditions.

Score: field accuracy (exact match, numbers within 0.05) + danger-sign recall/precision, where each MISSED
danger sign costs double, mirroring the judging rubric.
"""
from __future__ import annotations

import argparse
import array
import asyncio
import json
import math
import random
import time
import wave
from datetime import date, timedelta
from pathlib import Path

from server import config
from server.agent import Session
from server.gnani_stt import SttStream
from server.gnani_tts import TtsClient

HERE = Path(__file__).parent


# ---------------------------------------------------------------------------- audio helpers
async def synth_16k(text: str, lang: str) -> bytes:
    tts = TtsClient(lang)
    old = config.TTS_SAMPLE_RATE
    config.TTS_SAMPLE_RATE = 16000
    try:
        return b"".join([c async for c in tts.synth(text)])
    finally:
        config.TTS_SAMPLE_RATE = old
        await tts.close()


def make_noise(n: int, seed: int = 7) -> array.array:
    """Synthetic 'street' noise: brown-ish rumble + random honks + chatter-like bursts."""
    rnd = random.Random(seed)
    out = array.array("h", [0] * n)
    b = 0.0
    honk_t = 0
    for i in range(n):
        b = 0.98 * b + rnd.uniform(-1, 1) * 0.2
        v = b + rnd.uniform(-1, 1) * 0.15
        if honk_t <= 0 and rnd.random() < 1 / 48000:
            honk_t = 6000
        if honk_t > 0:
            v += 0.5 * math.sin(2 * math.pi * 420 * i / 16000)
            honk_t -= 1
        out[i] = int(max(-1, min(1, v * 0.5)) * 32767)
    return out


def load_noise(path: str, n: int) -> array.array:
    with wave.open(path) as w:
        data = array.array("h", w.readframes(w.getnframes()))
    if not data:
        return make_noise(n)
    return array.array("h", (data[i % len(data)] for i in range(n)))


def mix(speech: bytes, noise: array.array, snr_db: float) -> bytes:
    s = array.array("h", speech)
    if not s:
        return speech
    ps = sum(x * x for x in s) / len(s)
    pn = sum(noise[i] ** 2 for i in range(len(s))) / len(s) or 1
    k = math.sqrt(ps / (pn * 10 ** (snr_db / 10)))
    out = array.array("h", (max(-32768, min(32767, int(s[i] + k * noise[i]))) for i in range(len(s))))
    return out.tobytes()


async def transcribe_stream(pcm: bytes, lang: str) -> tuple[str, float]:
    """Stream at real-time cadence through Prisma v3.0; returns (text, ms from audio end to transcript)."""
    texts, done = [], asyncio.Event()
    t_end = [0.0]

    async def on_event(m):
        if m.get("type") == "transcript":
            if m.get("text"):
                texts.append(m["text"])
            done.set()

    stt = SttStream(lang, on_event)
    await stt.start()
    pad = b"\x00" * (16000 * 2 // 2)  # 0.5 s lead-in / tail silence
    data = pad + pcm + pad * 2
    frame = config.STT_FRAME_BYTES
    t0 = time.monotonic()
    for i in range(0, len(data) - frame + 1, frame):
        await stt.send_audio(data[i:i + frame])
        target = t0 + (i / frame + 1) * 0.032
        await asyncio.sleep(max(0, target - time.monotonic()))
    t_end[0] = time.monotonic()
    try:
        await asyncio.wait_for(done.wait(), 8)
        await asyncio.sleep(1.0)  # collect any trailing segment
    except asyncio.TimeoutError:
        await stt.commit()
        await asyncio.sleep(2.0)
    await stt.close()
    return " ".join(texts), (time.monotonic() - t_end[0]) * 1000


# ---------------------------------------------------------------------------- scoring
def field_ok(exp, got, today: date) -> bool:
    if isinstance(exp, str) and exp.startswith("+"):
        want = (today + timedelta(days=int(exp[1:]))).isoformat()
        return got is not None and abs((date.fromisoformat(got) - date.fromisoformat(want)).days) <= 1
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        return got is not None and abs(float(got) - float(exp)) <= 0.05
    if isinstance(exp, list):
        return sorted(exp) == sorted(got or [])
    return exp == got


async def run_case(case: dict, mode: str, snr: float, noise_path: str | None, pace: float = 0.0) -> dict:
    sent = []

    async def send_json(m):
        sent.append(m)

    async def send_bytes(_):
        pass

    lang = case["lang"]
    s = Session(send_json, send_bytes, lang=lang, use_audio=False)
    await s.start(greet=False)
    heard, ttfa = [], []
    for utt in case["turns"]:
        text = utt
        if mode == "audio":
            pcm = await synth_16k(utt, lang)
            noise = load_noise(noise_path, len(pcm) // 2) if noise_path else make_noise(len(pcm) // 2)
            text, stt_ms = await transcribe_stream(mix(pcm, noise, snr), lang)
            heard.append({"said": utt, "heard": text, "stt_ms": round(stt_ms)})
            if not text:
                continue
        if mode == "text" and pace:
            await asyncio.sleep(pace)
        t0 = time.monotonic()
        await s.on_utterance(text, t0)
        await s.turn.task
        await s.turn.speaker
        if s.turn.first_audio_at:
            ttfa.append(int((s.turn.first_audio_at - t0) * 1000))
    rec = s.rec
    saved = next((m["record"] for m in sent if m["type"] == "saved"), None)
    final = saved or rec
    fields = {k: {"want": v, "got": final.get(k), "ok": field_ok(v, final.get(k), s.today)}
              for k, v in case["expect"].items()}
    got_danger = {d["id"] for d in final["danger_signs"]}
    want_danger = set(case["expect_danger"])
    return {
        "id": case["id"], "lang": lang, "fields": fields,
        "danger_expected": len(want_danger), "danger_missed": sorted(want_danger - got_danger), "danger_extra": sorted(got_danger - want_danger),
        "saved_ok": bool(saved) == case["expect_saved"], "heard": heard, "agent": s.spoken,
        "llm_first_reply_ms": ttfa,
    }


def summarize(results: list[dict]) -> dict:
    nf = sum(len(r["fields"]) for r in results)
    okf = sum(f["ok"] for r in results for f in r["fields"].values())
    expected = sum(r["danger_expected"] for r in results)
    missed = sum(len(r["danger_missed"]) for r in results)
    extra = sum(len(r["danger_extra"]) for r in results)
    return {"field_accuracy": round(okf / nf, 3) if nf else None, "fields": f"{okf}/{nf}",
            "danger_recall": round((expected - missed) / expected, 3) if expected else None,
            "danger_missed": missed, "danger_false_alarms": extra,
            "saves_correct": f"{sum(r['saved_ok'] for r in results)}/{len(results)}",
            # rubric-style penalty: each miss counts twice, each false alarm once
            "danger_penalty": 2 * missed + extra}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["text", "audio"], default="text")
    ap.add_argument("--snr", type=float, default=5.0, help="speech-to-noise ratio in dB (audio mode)")
    ap.add_argument("--noise", help="16 kHz mono 16-bit WAV of street/household noise")
    ap.add_argument("--case", help="run one case id")
    ap.add_argument("--pace", type=float, default=6.0,
                    help="seconds between turns in text mode (a real worker speaks a turn every ~5-10 s)")
    ap.add_argument("--out", default=str(HERE / "results.json"))
    args = ap.parse_args()
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    config.RECORDS_FILE = HERE / "eval_records.jsonl"  # keep eval saves out of the demo store
    results = []
    for c in cases:
        r = await run_case(c, args.mode, args.snr, args.noise, args.pace)
        results.append(r)
        bad = [k for k, f in r["fields"].items() if not f["ok"]]
        print(f"{r['id']:<32} fields {len(r['fields']) - len(bad)}/{len(r['fields'])}"
              f"{' (wrong: ' + ', '.join(bad) + ')' if bad else ''}  missed {r['danger_missed'] or '-'}"
              f"  extra {r['danger_extra'] or '-'}  save {'ok' if r['saved_ok'] else 'WRONG'}")
    summary = summarize(results)
    print(json.dumps(summary, indent=2))
    Path(args.out).write_text(json.dumps({"mode": args.mode, "snr": args.snr, "summary": summary,
                                          "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("details ->", args.out)


if __name__ == "__main__":
    asyncio.run(main())
