"""Gnani Prisma v3.0 realtime STT client (WebSocket, 16 kHz PCM16 mono, 1024-byte frames)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from . import config

log = logging.getLogger("stt")

# Local health words + register names boost recognition (Prisma v3.0 "keyterms").
HEALTH_KEYTERMS = [
    "ASHA", "ANC checkup", "PNC", "HBNC", "TT injection", "Td injection", "IFA goli", "IFA tablet", "calcium goli",
    "BP", "Hb", "hemoglobin", "MCP card", "PHC", "VHND", "BCG", "OPV", "pentavalent", "penta", "rota", "PCV", "IPV",
    "MR vaccine", "vitamin A", "kilo", "gram", "temperature", "bukhar", "khoon", "bleeding", "jhatke", "sujan",
    "doodh nahi pee raha", "pasli chalna", "peeliya", "naal", "nabhi", "garbhvati", "prasav", "navjaat", "teeka",
    "टीका", "गर्भवती", "प्रसव", "नवजात", "आयरन की गोली", "वज़न", "बुखार", "अगली विज़िट",
]

EventCb = Callable[[dict], Awaitable[None]]


class SttStream:
    def __init__(self, language: str, on_event: EventCb, extra_keyterms: list[str] | None = None):
        self.language = language
        self.on_event = on_event
        self.keyterms = (HEALTH_KEYTERMS + (extra_keyterms or []))[:200]
        self._ws = None
        self._buf = bytearray()
        self._reader: asyncio.Task | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        self._fails = 0          # consecutive failed reconnects (network down)
        self._retry_at = 0.0     # monotonic time of the next allowed reconnect attempt

    def _headers(self) -> dict:
        # The live Prisma v3.0 stream accepts exactly one locale (a list closes with 1008, despite the docs).
        # Indic locales still transcribe embedded English health words; keyterms help with those.
        langs = self.language
        h = {
            "x-api-key-id": config.GNANI_API_KEY,
            "model": config.STT_MODEL,
            "language_code": langs,
            "format": config.STT_FORMAT,
            "x-sample-rate": str(config.STT_SAMPLE_RATE),
            "x-idle-timeout-sec": "900",
            "x-api-request-id": f"asha-{uuid.uuid4().hex[:12]}",
            "keyterms": json.dumps(self.keyterms, ensure_ascii=True),
            "topic": "ASHA worker dictating home visit notes: pregnancy ANC, postnatal, newborn care, "
                     "immunisation, weight in kilo, blood pressure, danger signs, next visit date.",
        }
        h.update(config.VAD)
        return h

    async def start(self) -> None:
        async with self._lock:
            if self._ws is not None:
                return
            self._ws = await connect(config.STT_URL, additional_headers=self._headers(), max_size=2**22,
                                     open_timeout=10, ping_interval=20)
            self._reader = asyncio.create_task(self._read(self._ws))

    async def _read(self, ws) -> None:
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg["_recv"] = time.monotonic()
                await self.on_event(msg)
        except ConnectionClosed as e:
            log.info("STT closed: %s", e)
        except Exception:  # never let the reader kill the session
            log.exception("STT reader error")
        finally:
            if self._ws is ws:
                self._ws = None
            if not self._closed:
                await self.on_event({"type": "stt_disconnected"})

    async def send_audio(self, pcm: bytes) -> None:
        """Accepts any-length PCM16 16k chunks; forwards exact 1024-byte frames."""
        self._buf.extend(pcm)
        if self._ws is None:
            if self._closed:
                return
            now = time.monotonic()
            if now < self._retry_at:  # backing off after a failed reconnect: drop audio, don't hammer the network
                self._buf.clear()
                return
            try:
                await self.start()  # auto-reconnect (15-minute session cap, network blips)
                if self._fails:
                    log.info("STT reconnected after %d failed attempts", self._fails)
                    await self.on_event({"type": "stt_reconnected"})
                self._fails = 0
            except Exception as e:
                self._fails += 1
                delay = min(1.0 * 2 ** (self._fails - 1), 15.0)  # 1, 2, 4, 8, 15 s …
                self._retry_at = now + delay
                if self._fails == 1:
                    log.warning("STT reconnect failed (%s); retrying with backoff", type(e).__name__)
                    await self.on_event({"type": "stt_offline", "message": "No connection to speech service"})
                else:
                    log.warning("STT reconnect attempt %d failed (%s); next in %.0f s", self._fails, type(e).__name__, delay)
                self._buf.clear()
                return
        while len(self._buf) >= config.STT_FRAME_BYTES:
            frame = bytes(self._buf[:config.STT_FRAME_BYTES])
            del self._buf[:config.STT_FRAME_BYTES]
            try:
                await self._ws.send(frame)
            except (ConnectionClosed, AttributeError):
                self._buf.clear()
                return

    async def commit(self) -> None:
        """Force end-of-utterance (tap 'done' button) instead of waiting for silence."""
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "commit"}))
            except ConnectionClosed:
                pass

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            await self._ws.close()
        if self._reader:
            self._reader.cancel()
