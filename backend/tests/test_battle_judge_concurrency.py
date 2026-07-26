"""Tests for llm_battle.run_battle / run_judge concurrency + error-handling
correctness, and llm_client.call_with_limit's shared semaphore/timeout/
cancellation semantics.

Covers:
  - A single model/judge failure surfaces as an explicit per-model error
    payload while the other model's successful result is preserved (never a
    silently-dropped result or a crash of the whole battle/judge call).
  - asyncio.CancelledError raised by any underlying call is re-raised, never
    downgraded to an ordinary "model failed" error payload.
  - call_with_limit bounds concurrency via the shared LLM_SEMAPHORE and
    converts a genuine timeout into asyncio.TimeoutError without ever
    swallowing cancellation.
"""
import asyncio
import unittest
from unittest import mock

import llm_battle
import llm_client
from models import BattleResultsPayload

DNA = {
    "reader_archetype": "Test Archetype",
    "taste_summary": "summary",
    "top_themes": ["theme"],
    "avoid_themes": [],
    "taste_dimensions": {"prose_density": 5, "pacing_preference": 5, "intellectual_depth": 5, "fiction_ratio": 50},
}


def _battle_results_with(gpt_recs, glm_recs):
    return {
        "models": {
            "GPT-OSS 120B · Cerebras": {"recommendations": gpt_recs, "meta": {}, "info": {}},
            "GLM 4.7 · Cerebras": {"recommendations": glm_recs, "meta": {}, "info": {}},
        }
    }


class RunBattleErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # run_battle now selects competitors by which provider keys are
        # configured; the test environment has none. Pin the roster to the
        # two default Cerebras models so these tests exercise run_battle's
        # per-model aggregation/error handling deterministically.
        patcher = mock.patch.object(
            llm_battle,
            "available_battle_models",
            return_value=["gpt-oss-120b", "zai-glm-4.7"],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    async def _fake_lookup_no_match(*, isbn=None, title=None, author=None, timeout=None):
        # ISBN enrichment runs on every non-empty rec set produced by
        # run_battle; stub it out here so these error-handling/warning tests
        # stay network-free and deterministic (a real Open Library call
        # would be slow, flaky, and unrelated to what's under test).
        return None, None

    async def test_one_model_failure_is_isolated_other_model_result_preserved(self):
        async def fake_call_model(model, prompt, retries=3):
            if model == "gpt-oss-120b":
                raise ValueError("simulated model failure")
            return {
                "recommendations": [{"title": "Good Book", "author": "Author"}],
                "_meta": {"latency_ms": 1},
            }

        with mock.patch.object(llm_battle, "call_model", side_effect=fake_call_model), \
             mock.patch.object(llm_battle, "lookup_open_library", side_effect=self._fake_lookup_no_match):
            result = await llm_battle.run_battle(DNA, books=[])

        self.assertIn("error", result["models"]["GPT-OSS 120B · Cerebras"])
        self.assertNotIn("error", result["models"]["GLM 4.7 · Cerebras"])
        self.assertEqual(len(result["models"]["GLM 4.7 · Cerebras"]["recommendations"]), 1)

    async def test_cancelled_error_from_one_model_propagates_not_swallowed(self):
        async def fake_call_model(model, prompt, retries=3):
            if model == "gpt-oss-120b":
                raise asyncio.CancelledError()
            return {"recommendations": [], "_meta": {}}

        with mock.patch.object(llm_battle, "call_model", side_effect=fake_call_model):
            with self.assertRaises(asyncio.CancelledError):
                await llm_battle.run_battle(DNA, books=[])

    async def test_per_model_warnings_aggregate_into_top_level_warnings(self):
        async def fake_call_model(model, prompt, retries=3):
            # Both models return fewer than TARGET_RECS -> each should warn,
            # and those warnings should surface in the top-level list.
            return {"recommendations": [{"title": f"Book {model}", "author": "A"}], "_meta": {}}

        with mock.patch.object(llm_battle, "call_model", side_effect=fake_call_model), \
             mock.patch.object(llm_battle, "lookup_open_library", side_effect=self._fake_lookup_no_match):
            result = await llm_battle.run_battle(DNA, books=[])

        self.assertTrue(len(result["warnings"]) >= 2)
        self.assertTrue(any("GPT-OSS 120B · Cerebras" in w for w in result["warnings"]))
        self.assertTrue(any("GLM 4.7 · Cerebras" in w for w in result["warnings"]))

    async def test_malformed_recommendations_container_is_isolated(self):
        async def fake_call_model(model, prompt, retries=3):
            if model == "gpt-oss-120b":
                return {"recommendations": None, "_meta": {}}
            return {
                "recommendations": [{"title": "Good Book", "author": "Author"}],
                "_meta": {},
            }

        with mock.patch.object(llm_battle, "call_model", side_effect=fake_call_model), \
             mock.patch.object(llm_battle, "lookup_open_library", side_effect=self._fake_lookup_no_match):
            result = await llm_battle.run_battle(DNA, books=[])

        self.assertIn("error", result["models"]["GPT-OSS 120B · Cerebras"])
        self.assertEqual(len(result["models"]["GLM 4.7 · Cerebras"]["recommendations"]), 1)

    async def test_battle_errors_are_bounded_to_judge_schema(self):
        async def fake_call_model(model, prompt, retries=3):
            if model == "gpt-oss-120b":
                raise RuntimeError("x" * 1200)
            return {
                "recommendations": [{"title": "Good Book", "author": "Author"}],
                "_meta": {},
            }

        with mock.patch.object(llm_battle, "call_model", side_effect=fake_call_model), \
             mock.patch.object(llm_battle, "lookup_open_library", side_effect=self._fake_lookup_no_match):
            result = await llm_battle.run_battle(DNA, books=[])

        self.assertLessEqual(
            len(result["models"]["GPT-OSS 120B · Cerebras"]["error"]),
            llm_battle.MODEL_ERROR_MAX_CHARS,
        )
        BattleResultsPayload.model_validate(result)


class RunJudgeErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    def _valid_verdict(self):
        return {
            "scores": {k: 7 for k in llm_battle.RUBRIC},
            "verdict": "Solid picks overall.",
            "_judge_latency_ms": 10,
            "_judge_model": "qwen2.5:7b",
        }

    async def test_one_judge_failure_is_visible_inside_result_judge_other_preserved(self):
        battle_results = _battle_results_with(
            [{"title": "A", "author": "AA", "why": "w"}],
            [{"title": "B", "author": "BB", "why": "w"}],
        )
        call_count = {"n": 0}

        async def fake_judge(prompt, model="qwen2.5:7b", timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("ollama down")
            return self._valid_verdict()

        with mock.patch.object(llm_battle, "call_ollama_judge", side_effect=fake_judge):
            result = await llm_battle.run_judge(DNA, battle_results)

        judge = result["judge"]
        errored = [v for v in judge.values() if "error" in v]
        succeeded = [v for v in judge.values() if "error" not in v]
        self.assertEqual(len(errored), 1)
        self.assertEqual(len(succeeded), 1)
        self.assertIn("scores", succeeded[0])
        self.assertIsNone(result["winner"])
        self.assertFalse(result["tie"])

    async def test_failed_recommender_is_not_judged_or_declared_winner(self):
        battle_results = _battle_results_with(
            [],
            [{"title": "B", "author": "BB", "why": "w"}],
        )
        battle_results["models"]["GPT-OSS 120B · Cerebras"]["error"] = "upstream model unavailable"
        calls = {"count": 0}

        async def fake_judge(prompt, model="qwen2.5:7b", timeout=None):
            calls["count"] += 1
            return self._valid_verdict()

        with mock.patch.object(llm_battle, "call_ollama_judge", side_effect=fake_judge):
            result = await llm_battle.run_judge(DNA, battle_results)

        self.assertEqual(calls["count"], 1)
        self.assertIn("error", result["judge"]["GPT-OSS 120B · Cerebras"])
        self.assertNotIn("error", result["judge"]["GLM 4.7 · Cerebras"])
        self.assertIsNone(result["winner"])
        self.assertFalse(result["tie"])

    async def test_cancelled_error_from_one_judge_call_propagates(self):
        battle_results = _battle_results_with(
            [{"title": "A", "author": "AA", "why": "w"}],
            [{"title": "B", "author": "BB", "why": "w"}],
        )
        call_count = {"n": 0}

        async def fake_judge(prompt, model="qwen2.5:7b", timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise asyncio.CancelledError()
            return self._valid_verdict()

        with mock.patch.object(llm_battle, "call_ollama_judge", side_effect=fake_judge):
            with self.assertRaises(asyncio.CancelledError):
                await llm_battle.run_judge(DNA, battle_results)

    async def test_both_judge_failures_raise_explicit_runtime_error(self):
        battle_results = _battle_results_with(
            [{"title": "A", "author": "AA", "why": "w"}],
            [{"title": "B", "author": "BB", "why": "w"}],
        )

        async def fake_judge(prompt, model="qwen2.5:7b", timeout=None):
            raise RuntimeError("ollama unreachable")

        with mock.patch.object(llm_battle, "call_ollama_judge", side_effect=fake_judge):
            with self.assertRaises(RuntimeError):
                await llm_battle.run_judge(DNA, battle_results)

    async def test_invalid_judge_json_shape_is_a_per_model_error_not_a_crash(self):
        battle_results = _battle_results_with(
            [{"title": "A", "author": "AA", "why": "w"}],
            [{"title": "B", "author": "BB", "why": "w"}],
        )
        call_count = {"n": 0}

        async def fake_judge(prompt, model="qwen2.5:7b", timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Missing required rubric dimensions -> fails validation,
                # but is still a "successful" transport-level response.
                return {
                    "scores": {"relevance": 7},
                    "verdict": "ok",
                    "_judge_latency_ms": 5,
                    "_judge_model": "qwen2.5:7b",
                }
            return self._valid_verdict()

        with mock.patch.object(llm_battle, "call_ollama_judge", side_effect=fake_judge):
            result = await llm_battle.run_judge(DNA, battle_results)

        judge = result["judge"]
        errored = [v for v in judge.values() if "error" in v]
        succeeded = [v for v in judge.values() if "error" not in v]
        self.assertEqual(len(errored), 1)
        self.assertEqual(len(succeeded), 1)

    async def test_extra_rubric_key_is_rejected(self):
        battle_results = _battle_results_with(
            [{"title": "A", "author": "AA", "why": "w"}],
            [{"title": "B", "author": "BB", "why": "w"}],
        )
        call_count = {"n": 0}

        async def fake_judge(prompt, model="qwen2.5:7b", timeout=None):
            call_count["n"] += 1
            verdict = self._valid_verdict()
            if call_count["n"] == 1:
                verdict["scores"]["unrequested_dimension"] = 9
            return verdict

        with mock.patch.object(llm_battle, "call_ollama_judge", side_effect=fake_judge):
            result = await llm_battle.run_judge(DNA, battle_results)

        self.assertEqual(sum("error" in verdict for verdict in result["judge"].values()), 1)
        self.assertIsNone(result["winner"])

    async def test_numeric_string_scores_are_rejected(self):
        battle_results = _battle_results_with(
            [{"title": "A", "author": "AA", "why": "w"}],
            [{"title": "B", "author": "BB", "why": "w"}],
        )
        call_count = {"n": 0}

        async def fake_judge(prompt, model="qwen2.5:7b", timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "scores": {key: "7" for key in llm_battle.RUBRIC},
                    "verdict": "Looks numeric but has the wrong types.",
                    "_judge_latency_ms": 1,
                    "_judge_model": model,
                }
            return self._valid_verdict()

        with mock.patch.object(llm_battle, "call_ollama_judge", side_effect=fake_judge):
            result = await llm_battle.run_judge(DNA, battle_results)

        self.assertEqual(sum("error" in verdict for verdict in result["judge"].values()), 1)

    async def test_near_equal_scores_are_reported_as_tie(self):
        battle_results = _battle_results_with(
            [{"title": "A", "author": "AA", "why": "w"}],
            [{"title": "B", "author": "BB", "why": "w"}],
        )
        scores = iter((7.0, 7.04))

        async def fake_judge(prompt, model="qwen2.5:7b", timeout=None):
            score = next(scores)
            return {
                "scores": {key: score for key in llm_battle.RUBRIC},
                "verdict": "Close result.",
                "_judge_latency_ms": 1,
                "_judge_model": model,
            }

        with mock.patch.object(llm_battle, "call_ollama_judge", side_effect=fake_judge):
            result = await llm_battle.run_judge(DNA, battle_results)

        self.assertTrue(result["tie"])
        self.assertIsNone(result["winner"])


class CallWithLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounds_concurrency_via_shared_semaphore(self):
        limit = 2
        in_flight = {"current": 0, "max": 0}
        lock = asyncio.Lock()

        async def work():
            async with lock:
                in_flight["current"] += 1
                in_flight["max"] = max(in_flight["max"], in_flight["current"])
            try:
                await asyncio.sleep(0.03)
            finally:
                async with lock:
                    in_flight["current"] -= 1

        with mock.patch.object(llm_client, "LLM_SEMAPHORE", asyncio.Semaphore(limit)):
            await asyncio.gather(*(llm_client.call_with_limit(work(), timeout=5) for _ in range(6)))

        self.assertLessEqual(in_flight["max"], limit)

    async def test_timeout_raises_asyncio_timeout_error(self):
        async def slow():
            await asyncio.sleep(1)

        with self.assertRaises(asyncio.TimeoutError):
            await llm_client.call_with_limit(slow(), timeout=0.01)

    async def test_cancelled_error_is_never_converted_to_timeout(self):
        async def cancels_itself():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await llm_client.call_with_limit(cancels_itself(), timeout=5)

    async def test_queue_wait_is_bounded_and_unstarted_coroutine_is_closed(self):
        started = {"value": False}

        async def work():
            started["value"] = True

        with mock.patch.object(llm_client, "LLM_SEMAPHORE", asyncio.Semaphore(0)):
            with self.assertRaises(asyncio.TimeoutError):
                await llm_client.call_with_limit(work(), timeout=5, queue_timeout=0.01)

        self.assertFalse(started["value"])

    async def test_cancellation_while_queued_propagates(self):
        started = {"value": False}

        async def work():
            started["value"] = True

        with mock.patch.object(llm_client, "LLM_SEMAPHORE", asyncio.Semaphore(0)):
            task = asyncio.create_task(
                llm_client.call_with_limit(work(), timeout=5, queue_timeout=5)
            )
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertFalse(started["value"])


if __name__ == "__main__":
    unittest.main()
