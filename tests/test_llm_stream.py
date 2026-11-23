"""The streaming parser: update applied before speech; sentences flushed as they complete."""
import pytest

from server import config, llm


class FakeChunk:
    def __init__(self, text):
        class D: content = text
        class C: delta = D()
        self.choices = [C()]


class FakeOpenAI:
    """Mimics AsyncOpenAI().chat.completions.create(stream=True)."""
    def __init__(self, chunks):
        self.chat = self
        self.completions = self
        self.chunks = chunks
        self.kwargs = None

    async def create(self, **kw):
        self.kwargs = kw
        async def gen():
            for c in self.chunks:
                yield FakeChunk(c)
        return gen()


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def gen():
            for c in self.chunks:
                yield c
        return gen()

    async def get_final_message(self):
        class U:
            cache_read_input_tokens = 0
        class Msg:
            usage = U()
        return Msg()


class FakeClient:
    def __init__(self, chunks):
        self.messages = self
        self.chunks = chunks
        self.kwargs = None

    def stream(self, **kw):
        self.kwargs = kw
        return FakeStream(self.chunks)


@pytest.mark.asyncio
async def test_parser(monkeypatch):
    chunks = ['<upd', 'ate>{"intent": "dicta', 'tion", "set": {"weight_kg": 2.8}}</upd', 'ate>\n<sa', 'y>वज़न दो दशमलव',
              ' आठ किलो, नोट किया. अगला बताइए', ' दीदी.</say>']
    fc = FakeClient(chunks)
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "LLM_MODEL", "claude-opus-5-5")
    monkeypatch.setattr(llm, "client", lambda: fc)
    events = []

    async def on_update(u):
        events.append(("update", u))

    async def on_sentence(s):
        events.append(("say", s))

    raw, timing = await llm.run_turn([], "ctx", on_update, on_sentence)
    assert events[0] == ("update", {"intent": "dictation", "set": {"weight_kg": 2.8}, "danger_signs": []})
    assert events[1] == ("say", "वज़न दो दशमलव आठ किलो, नोट किया.")
    assert events[2] == ("say", "अगला बताइए दीदी.")
    assert fc.kwargs["model"] == "claude-opus-5-5" and fc.kwargs["output_config"]["effort"] == "low"


@pytest.mark.asyncio
async def test_parser_no_format(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(llm, "client", lambda: FakeClient(["Sorry, I did not get that."]))
    events = []

    async def on_update(u):
        events.append(("update", u))

    async def on_sentence(s):
        events.append(("say", s))

    await llm.run_turn([], "ctx", on_update, on_sentence)
    assert events == [("update", {}), ("say", "Sorry, I did not get that.")]


@pytest.mark.asyncio
async def test_parser_openai_compatible(monkeypatch):
    chunks = ['<update>{"intent": "dictation", "set": {"weight_kg": 58}}</update>', '<say>वज़न अट्ठावन किलो,',
              ' नोट किया.</say>']
    fo = FakeOpenAI(chunks)
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(llm, "client", lambda: fo)
    events = []

    async def on_update(u):
        events.append(("update", u))

    async def on_sentence(s):
        events.append(("say", s))

    await llm.run_turn([], "ctx", on_update, on_sentence)
    assert events == [("update", {"intent": "dictation", "set": {"weight_kg": 58}, "danger_signs": []}),
                      ("say", "वज़न अट्ठावन किलो, नोट किया.")]
    assert fo.kwargs["messages"][0]["role"] == "system" and fo.kwargs["stream"] is True


@pytest.mark.parametrize("raw,want", [
    ('<update>{"intent":"other","set":{}}></update>', {"intent": "other"}),
    ('<update>{"intent":"other","set":{}}"</update>', {"intent": "other"}),
    ('<update>{"intent":"dictation","danger_signs":["MAT_HEADAGE_VISION"]}</update>',
     {"intent": "dictation", "danger_signs": ["MAT_HEADACHE_VISION"]}),
])
def test_parse_update_tolerant(raw, want):
    got = llm.parse_update(raw)
    for k, v in want.items():
        assert got[k] == v


@pytest.mark.asyncio
async def test_all_models_rate_limited_waits_and_retries(monkeypatch):
    class RL(Exception):
        status_code = 429

        def __str__(self):
            return "Rate limit reached. Please try again in 0.01s."

    calls = []

    class Flaky(FakeOpenAI):
        async def create(self, **kw):
            calls.append(kw["model"])
            if len(calls) <= len(config.LLM_MODELS):
                raise RL()
            return await super().create(**kw)

    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "LLM_MODELS", ["a", "b"])
    monkeypatch.setattr(llm, "client", lambda: Flaky(['<update>{"intent":"other"}</update><say>ok.</say>']))
    said = []

    async def on_update(u):
        pass

    async def on_sentence(s):
        said.append(s)

    _, timing = await llm.run_turn([], "ctx", on_update, on_sentence)
    assert calls == ["a", "b", "a"] and said == ["ok."] and timing["model"] == "a"


def test_backup_provider_joins_chain(monkeypatch):
    from server import config, llm
    monkeypatch.setattr(config, "LLM_BACKUPS", [("gemini:gemini-2.5-flash", "https://example.invalid/v1", "k", "gemini-2.5-flash")])
    monkeypatch.setattr(llm, "_backup_clients", {})
    assert llm._chain()[-1] == "gemini:gemini-2.5-flash"
    cl, name = llm._client_for("gemini:gemini-2.5-flash")
    assert name == "gemini-2.5-flash" and cl is not llm.client()
    assert llm._client_for(config.LLM_MODELS[0])[1] == config.LLM_MODELS[0]
