"""TTFT correctness test for llm_battle._call_model_once.

Verifies TTFT is recorded on the first non-empty *content* delta, not the
first stream event (which may be an empty/role-only chunk). A fake Cerebras
client (SimpleNamespace-based) is injected via llm_battle._get_client so the
real cerebras SDK is never required.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import llm_battle


class DummyDelta:
    def __init__(self, content=None):
        self.content = content


class DummyChoice:
    def __init__(self, content=None, finish_reason=None):
        self.delta = DummyDelta(content)
        self.finish_reason = finish_reason


class DummyChunk:
    def __init__(self, choices=None, usage=None):
        self.choices = choices or []
        self.usage = usage


class DummyUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeCompletions:
    """Mimics client.chat.completions.create(..., stream=True) -> async-iterable."""

    def __init__(self, steps):
        self._steps = steps  # list of DummyChunk | float(seconds to sleep)

    async def create(self, **kwargs):
        return self._stream()

    async def _stream(self):
        for step in self._steps:
            if isinstance(step, (int, float)):
                await asyncio.sleep(step)
            else:
                yield step


def make_fake_client(steps):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(steps)))


class TTFTTests(unittest.IsolatedAsyncioTestCase):
    async def test_ttft_recorded_on_first_nonempty_content_delta_not_first_event(self):
        steps = [
            DummyChunk(choices=[DummyChoice(content=None)]),  # first stream event: no content
            0.05,  # measurable delay before real content arrives
            DummyChunk(choices=[DummyChoice(content='{"ok": true}')]),
            DummyChunk(choices=[DummyChoice(content=None, finish_reason="stop")], usage=DummyUsage(12, 4)),
        ]
        fake_client = make_fake_client(steps)

        with mock.patch.object(llm_battle, "_get_client", return_value=fake_client):
            result = await llm_battle._call_model_once("fake-model", "prompt")

        self.assertEqual(result["ok"], True)
        meta = result["_meta"]
        self.assertIsNotNone(meta["ttft_ms"])
        # TTFT should reflect the ~50ms delay before the first *content*
        # delta, not the near-zero time of the earlier empty-content event.
        self.assertGreaterEqual(meta["ttft_ms"], 30)
        self.assertEqual(meta["prompt_tokens"], 12)
        self.assertEqual(meta["completion_tokens"], 4)

    async def test_ttft_ignores_multiple_leading_empty_events(self):
        steps = [
            DummyChunk(choices=[DummyChoice(content=None)]),
            DummyChunk(choices=[]),  # no choices at all
            DummyChunk(choices=[DummyChoice(content="")]),  # empty-string content, falsy
            0.03,
            DummyChunk(choices=[DummyChoice(content='{"x": 1}')]),
            DummyChunk(choices=[DummyChoice(content=None, finish_reason="stop")]),
        ]
        fake_client = make_fake_client(steps)

        with mock.patch.object(llm_battle, "_get_client", return_value=fake_client):
            result = await llm_battle._call_model_once("fake-model", "prompt")

        self.assertEqual(result["x"], 1)
        self.assertGreaterEqual(result["_meta"]["ttft_ms"], 15)

    async def test_empty_response_raises_value_error(self):
        steps = [DummyChunk(choices=[DummyChoice(content=None, finish_reason="stop")])]
        fake_client = make_fake_client(steps)

        with mock.patch.object(llm_battle, "_get_client", return_value=fake_client):
            with self.assertRaises(ValueError):
                await llm_battle._call_model_once("fake-model", "prompt")

    async def test_cancelled_error_is_never_swallowed_by_retry_loop(self):
        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        with mock.patch.object(llm_battle, "_call_model_once", side_effect=_raise_cancelled):
            with self.assertRaises(asyncio.CancelledError):
                await llm_battle.call_model("fake-model", "prompt")


if __name__ == "__main__":
    unittest.main()
