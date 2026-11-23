"""Gnani Timbre v3.0 realtime TTS client. Streams raw PCM16 mono 24 kHz chunks."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import AsyncIterator

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

from . import config

log = logging.getLogger("tts")


class TtsClient:
    """Small socket pool per session. Each socket carries one synthesis request at a time, so the next
    sentence can be synthesized on a second socket while the current one is still streaming.

    Barge-in cancels by closing the busy sockets (the server cannot abort a request); idle ones are kept,
    and a generation counter stops a cancelled request from retrying, so stale audio never leaks.
    """

    def __init__(self, language: str):
        self.language = language
        self._idle: list = []
        self._busy: set = set()
        self._gen = 0

    async def _connect(self):
        return await connect(
            config.TTS_URL,
            additional_headers={"X-API-Key-ID": config.GNANI_API_KEY, "Content-Type": "application/json"},
            max_size=2**24, open_timeout=10, ping_interval=20,
        )

    async def _acquire(self):
        while self._idle:
            ws = self._idle.pop()
            if ws.state is State.OPEN:
                return ws
        return await self._connect()

    async def warm(self) -> None:
        try:
            self._idle.append(await self._connect())
        except Exception:
            log.exception("TTS warm-up failed")

    async def synth(self, text: str) -> AsyncIterator[bytes]:
        text = text.strip()
        if not text:
            return
        req = {
            "text": text,
            "voice": config.TTS_VOICE,
            "model": config.TTS_MODEL,
            "language": self.language,
            "speed": config.TTS_SPEED,
            "audio_config": {"sample_rate": config.TTS_SAMPLE_RATE, "num_channels": 1, "sample_width": 2,
                             "encoding": "linear_pcm", "container": "raw"},
        }
        gen = self._gen
        yielded = False
        for attempt in range(2):
            if gen != self._gen:
                return  # cancelled (barge-in) before we got going
            ws = await self._acquire()
            self._busy.add(ws)
            done = False
            try:
                await ws.send(json.dumps(req, ensure_ascii=False))
                started = False
                async for raw in ws:
                    if gen != self._gen:
                        return
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind == "start":
                        started = True
                        continue
                    if not started and kind != "error":
                        continue  # leftovers of the previous request on this socket
                    data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                    if kind in ("audio", "complete") and data.get("audio"):
                        yielded = True
                        yield base64.b64decode(data["audio"])
                    if kind == "complete":
                        # Timbre sends two "complete"s per request (the is_final chunk, then a bare "Streaming
                        # completed"). We stop at the first; the next request skips the leftover until its own
                        # "start", so it can no longer end that request early (every 2nd sentence was silent).
                        done = True
                        return
                    elif kind == "error":
                        log.error("TTS error: %s", msg.get("message"))
                        done = True
                        return
                return
            except ConnectionClosed:
                if attempt == 1 or yielded or gen != self._gen:
                    if gen != self._gen:
                        return
                    raise
                # stale idle socket: retry once on a fresh one
            finally:
                self._busy.discard(ws)
                if done and gen == self._gen and ws.state is State.OPEN and len(self._idle) < 2:
                    self._idle.append(ws)
                elif not done:
                    _close_bg(ws)

    async def cancel(self) -> None:
        """Stop all in-flight synthesis now. Busy sockets are closed in the background: awaiting close()
        mid-stream waits for a close handshake the server does not send until synthesis ends."""
        self._gen += 1
        busy, self._busy = self._busy, set()
        for ws in busy:
            _close_bg(ws)

    async def close(self) -> None:
        await self.cancel()
        idle, self._idle = self._idle, []
        for ws in idle:
            _close_bg(ws)


def _close_bg(ws) -> None:
    async def _close():
        try:
            await asyncio.wait_for(ws.close(), 2)
        except Exception:
            if getattr(ws, "transport", None):
                ws.transport.abort()
    asyncio.create_task(_close())
