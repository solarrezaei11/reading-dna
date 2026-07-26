"""Provider registry + streaming-routing tests for the N-way battle.

These verify the multi-provider battle machinery without needing any real
SDK, network, or API keys:

  - `available_battle_models()` includes a provider's model only when that
    provider's API key is configured, and preserves the deterministic
    Cerebras-first order (so a Cerebras-only deployment is byte-for-byte the
    original two-model battle).
  - `_stream_completion` routes Groq/OpenRouter models to the OpenAI-compatible
    httpx SSE path (not the Cerebras SDK), and folds SSE deltas into the same
    TTFT/token state the Cerebras path uses.
"""
import unittest
from unittest import mock

import providers


class AvailableBattleModelsTests(unittest.TestCase):
    def test_only_configured_providers_participate(self):
        with mock.patch.object(
            providers, "provider_configured", side_effect=lambda p: p == "cerebras"
        ):
            # Cerebras-only: all Cerebras roster entries, no cross-provider opponents.
            models = providers.available_battle_models()
            self.assertEqual(models[:2], ["gpt-oss-120b", "zai-glm-4.7"])
            self.assertTrue(all(providers.provider_for_model(m) == "cerebras" for m in models))

    def test_groq_and_openrouter_join_when_keyed(self):
        with mock.patch.object(providers, "provider_configured", return_value=True):
            models = providers.available_battle_models()
        # Cerebras models come first (original order), then the opt-in providers.
        self.assertEqual(models[:2], ["gpt-oss-120b", "zai-glm-4.7"])
        provider_set = {providers.provider_for_model(m) for m in models}
        self.assertEqual(provider_set, {"cerebras", "groq", "openrouter"})

    def test_equivalent_family_runs_on_multiple_providers(self):
        # The whole point of the refactor: at least one model family is served
        # by more than one provider so the battle compares providers head-to-head
        # on the SAME model. Group entry keys by family and assert a shared one.
        with mock.patch.object(providers, "provider_configured", return_value=True):
            models = providers.available_battle_models()
        families: dict[str, set[str]] = {}
        for key in models:
            info = providers.MODEL_INFO[key]
            families.setdefault(info["family"], set()).add(info["provider"])
        multi = {fam: provs for fam, provs in families.items() if len(provs) > 1}
        self.assertTrue(multi, "expected at least one family served by multiple providers")

    def test_no_models_when_nothing_configured(self):
        with mock.patch.object(providers, "provider_configured", return_value=False):
            self.assertEqual(providers.available_battle_models(), [])

    def test_unknown_model_defaults_to_cerebras_provider(self):
        self.assertEqual(providers.provider_for_model("fake-model"), "cerebras")

    def test_api_model_falls_back_to_key_for_unknown(self):
        # predict.py / ad-hoc callers pass a bare model id that is its own
        # api model; unknown keys must address themselves on the wire.
        self.assertEqual(providers.api_model_for("fake-model"), "fake-model")
        # A namespaced Groq entry maps to its provider-specific api model.
        self.assertEqual(
            providers.api_model_for("groq:openai/gpt-oss-120b"), "openai/gpt-oss-120b"
        )


class StreamRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_http_model_uses_http_path_not_sdk(self):
        import llm_battle

        # Pick a registered non-Cerebras model id.
        http_model = next(
            m for m in providers.MODEL_REGISTRY
            if providers.provider_for_model(m) != "cerebras"
        )

        called = {}

        async def fake_http(provider, model, messages, state, max_tokens):
            called["provider"] = provider
            called["model"] = model
            called["max_tokens"] = max_tokens
            state["chunks"].append('{"ok": true}')

        async def fail_sdk(*a, **k):  # pragma: no cover - must not be called
            raise AssertionError("Cerebras SDK path must not run for an HTTP provider model")

        with mock.patch.object(llm_battle, "_stream_via_openai_http", side_effect=fake_http), \
             mock.patch.object(llm_battle, "_stream_via_cerebras", side_effect=fail_sdk):
            state = {"ttft": None, "chunks": [], "finish_reason": None,
                     "prompt_tokens": None, "completion_tokens": None}
            await llm_battle._stream_completion(http_model, "prompt", state)

        # The HTTP path receives the provider-specific api model, not the
        # internal entry key.
        self.assertEqual(called["model"], providers.api_model_for(http_model))
        self.assertNotEqual(called["provider"], "cerebras")
        self.assertEqual("".join(state["chunks"]), '{"ok": true}')

    async def test_apply_delta_records_ttft_and_tokens(self):
        import llm_battle

        state = {"ttft": None, "chunks": [], "finish_reason": None,
                 "prompt_tokens": None, "completion_tokens": None}
        llm_battle._apply_delta(state, None, None, None, None)  # empty leading event
        self.assertIsNone(state["ttft"])
        llm_battle._apply_delta(state, '{"x":1}', None, None, None)  # first content
        self.assertIsNotNone(state["ttft"])
        llm_battle._apply_delta(state, None, "stop", 12, 4)  # usage-only final
        self.assertEqual(state["finish_reason"], "stop")
        self.assertEqual(state["prompt_tokens"], 12)
        self.assertEqual(state["completion_tokens"], 4)
        self.assertEqual("".join(state["chunks"]), '{"x":1}')


if __name__ == "__main__":
    unittest.main()
