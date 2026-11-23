"""FastAPI app: serves the phone web client and proxies the voice session over one WebSocket.

Browser <-> /ws (PCM16 16 kHz up, PCM16 24 kHz + JSON down) <-> Gnani STT/TTS WebSockets + Claude.
The Gnani API key never reaches the browser (browsers cannot set WebSocket headers anyway).
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
from datetime import date

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, danger, record, report, store
from .agent import Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="ASHA Voice Register")
WEB = config.ROOT / "web"


@app.get("/")
async def landing():
    return FileResponse(WEB / "landing.html")


@app.get("/app")
async def app_page():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/api/config")
async def api_config():
    return {"languages": [{"code": c, "native": n, "english": e} for c, n, e in config.LANGUAGES],
            "ready": bool(config.GNANI_API_KEY) and bool(config.LLM_API_KEY or config.LLM_PROVIDER in ("anthropic", "ollama")),
            "llm": f"{config.LLM_PROVIDER}:{config.LLM_MODEL}"}


@app.get("/api/records")
async def api_records():
    return store.all_records()[::-1]


@app.get("/api/followups")
async def api_followups():
    return store.followup_list()


@app.get("/api/beneficiaries")
async def api_beneficiaries():
    """The synthetic register, with what is due for each beneficiary today."""
    today = date.today()
    last = {}
    for r in store.all_records():
        last[re.sub(r"\D", "", str(r.get("beneficiary_id") or ""))] = r
    out = []
    for b in record.REGISTRY:
        rec = record.new_record(today)
        record.apply_updates(rec, {"beneficiary_id": b["id"]})
        record.recompute(rec, today)
        lv = last.get(b["id"])
        out.append({**{k: b.get(k) for k in ("id", "name", "name_local", "type", "village")},
                    "context": rec.get("context"), "visit_type": rec.get("visit_type"),
                    "vaccinations_due": rec.get("vaccinations_due"),
                    "next_visit_suggested": rec.get("next_visit_suggested"),
                    "last_visit": lv and lv.get("saved_at"), "flagged": bool(lv and lv.get("referral_required"))})
    return out


@app.get("/api/stats")
async def api_stats():
    recs = store.all_records()
    today = date.today().isoformat()
    todays = [r for r in recs if (r.get("saved_at") or "").startswith(today)]
    fu = store.followup_list()
    return {"visits_total": len(recs), "visits_today": len(todays),
            "referrals_today": sum(bool(r.get("referral_required")) for r in todays),
            "referrals_total": sum(bool(r.get("referral_required")) for r in recs),
            "urgent_followups": sum(f["priority"] == "urgent" for f in fu),
            "vaccines_due": sum(f["action"].startswith("Vaccines due") for f in fu),
            "register_size": len(record.REGISTRY)}


CSV_FIELDS = ["saved_at", "record_id", "asha_id", "beneficiary_id", "beneficiary_name", "beneficiary_type", "visit_type",
              "visit_date", "weight_kg", "bp_systolic", "bp_diastolic", "hb_g_dl", "temperature_c", "temperature_f",
              "respiratory_rate", "ifa_tablets_given", "vaccinations_given_today", "vaccinations_due",
              "next_visit_date", "next_visit_suggested", "referral_required", "danger_signs", "notes"]


@app.get("/api/records.csv")
async def api_records_csv():
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in store.all_records():
        row = dict(r)
        row["vaccinations_given_today"] = "; ".join(r.get("vaccinations_given_today") or [])
        row["vaccinations_due"] = "; ".join(r.get("vaccinations_due") or [])
        row["danger_signs"] = "; ".join(d.get("label_en", d.get("id", "")) for d in r.get("danger_signs") or [])
        w.writerow(row)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=asha_visits.csv"})


@app.get("/report/{record_id}", response_class=HTMLResponse)
async def visit_report(record_id: str, print: int = 0):
    """End-of-visit report for one saved record, laid out for A4; the browser saves it as PDF."""
    rec = next((r for r in store.all_records() if r.get("record_id") == record_id), None)
    if rec is None:
        raise HTTPException(404, "record not found")
    return HTMLResponse(report.render(rec, auto_print=bool(print)))


VOICELOGS = config.DATA_DIR / "voicelogs"


@app.get("/api/voicelogs")
async def api_voicelogs():
    if not VOICELOGS.exists():
        return []
    out = []
    for p in sorted(VOICELOGS.glob("*.jsonl"), reverse=True)[:50]:
        lines = p.read_text(encoding="utf-8").splitlines()
        turns = sum('"type": "user_turn"' in l for l in lines)
        if turns:
            out.append({"id": p.stem, "turns": turns, "saves": sum('"type": "saved"' in l for l in lines),
                        "dangers": sum('"type": "danger"' in l for l in lines)})
    return out


@app.get("/api/voicelogs/{sid}")
async def api_voicelog(sid: str):
    if not re.fullmatch(r"[\w-]{1,40}", sid) or not (VOICELOGS / f"{sid}.jsonl").exists():
        raise HTTPException(404)
    return [json.loads(l) for l in (VOICELOGS / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


@app.post("/api/feedback")
async def api_feedback(body: dict):
    """A problem reported from the app on one turn of a session (stored in data/feedback.jsonl)."""
    from . import dataset
    try:
        return dataset.add_feedback(body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/feedback")
async def api_feedback_list():
    from . import dataset
    return dataset.feedback()


@app.get("/api/dataset/summary")
async def api_dataset_summary():
    from . import dataset
    return dataset.summary()


@app.get("/api/dataset.jsonl")
async def api_dataset():
    """Every turn of every saved session, one JSON object per line (text only, synthetic data)."""
    from . import dataset
    body = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in dataset.build())
    return Response(body, media_type="application/x-ndjson",
                    headers={"Content-Disposition": "attachment; filename=sakhi_sessions_dataset.jsonl"})


@app.post("/api/understand")
async def api_understand(body: dict):
    """Landing-page demo: how the LOCAL model reads one sentence (never calls the LLM)."""
    from datetime import date as _date
    from . import fastpath, numwords
    text = str(body.get("text") or "")[:300]
    lang = body.get("lang") if body.get("lang") in fastpath.LANGS else "hi-IN"
    rec = record.new_record(_date.today())
    hits = [h for h in danger.detect_text(numwords.normalize(text), None) if not h.negated]
    p = fastpath.parse(text, rec, "collecting", lang) if text.strip() else None
    if p is not None:
        record.apply_updates(rec, p.set)
        record.recompute(rec, _date.today())
    return {"local": p is not None, "intent": p.intent if p else None, "set": p.set if p else {},
            "say": (p.say or ([fastpath.ack(rec, list(p.set), lang)] if p.set else [])) if p else [],
            "danger": [{"id": h.sign_id, "label_en": danger.SIGNS[h.sign_id].en, "label_hi": danger.SIGNS[h.sign_id].hi}
                       for h in hits],
            "why": None if p else ("danger sign: the AI double-checks it, and the PHC referral is spoken instantly" if hits
                                   else "question or unusual phrasing: sent to the AI")}


@app.get("/api/learned")
async def api_learned():
    from . import learned
    return learned.stats()


@app.get("/api/danger-signs")
async def api_danger():
    return [s.__dict__ for s in danger.SIGNS.values()]


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    send_lock = asyncio.Lock()

    async def send_json(obj: dict):
        async with send_lock:
            try:
                await ws.send_text(json.dumps(obj, ensure_ascii=False, default=str))
            except Exception:
                pass

    async def send_bytes(b: bytes):
        async with send_lock:
            try:
                await ws.send_bytes(b)
            except Exception:
                pass

    session: Session | None = None
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if session:
                    await session.on_audio(msg["bytes"])
                continue
            data = json.loads(msg.get("text") or "{}")
            kind = data.get("type")
            if kind == "start":
                if session:
                    await session.close()
                if not config.GNANI_API_KEY:
                    await send_json({"type": "error", "where": "config", "message": "GNANI_API_KEY is not set"})
                    continue
                if not config.LLM_API_KEY and config.LLM_PROVIDER not in ("anthropic", "ollama"):
                    await send_json({"type": "error", "where": "config",
                                     "message": f"No API key for LLM provider '{config.LLM_PROVIDER}' (set GROQ_API_KEY)"})
                    continue
                session = Session(send_json, send_bytes, lang=data.get("lang", "hi-IN"),
                                  asha_id=data.get("asha_id") or "ASHA-DEMO")
                try:
                    await session.start(greet=data.get("greet", True))
                except Exception as e:
                    log.exception("session start failed")
                    await send_json({"type": "error", "where": "start", "message": str(e)[:200]})
                    session = None
            elif session is None:
                continue
            elif kind == "commit":
                await session.on_commit()
            elif kind == "text":  # typed fallback (and handy for testing without a mic)
                if (data.get("text") or "").strip():
                    await session.on_utterance(data["text"].strip(), source=data.get("source", "typed"))
            elif kind == "unflag":
                await session.on_unflag(str(data.get("sign") or ""), str(data.get("reason") or "")[:200])
            elif kind == "edit":
                await session.on_edit(data.get("field", ""), data.get("value"))
            elif kind == "new_visit":
                await session.new_visit()
            elif kind == "stop":
                await session.close()
                session = None
    except WebSocketDisconnect:
        pass
    finally:
        if session:
            await session.close()
