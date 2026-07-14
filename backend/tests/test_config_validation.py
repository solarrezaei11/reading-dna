"""Tests for config.py's fail-fast environment-variable validation (item 6).

Policy under test: an *absent* env var silently uses the documented default
(app still runs out of the box); an env var that IS SET but malformed (not a
parseable int/float/bool, or violates a required positive bound) raises
ConfigError — a ValueError subclass — at import/reload time, never silently
falling back to a default.

These tests exercise the private parsing helpers directly (pure functions,
no reload needed) and also reload the config module under patched
os.environ to prove the whole-module import-time fail-fast behavior, always
restoring the original module state afterward so other tests in the same
process see an unaffected config module.
"""
import importlib
import os
import unittest
from unittest import mock

import config


class EnvParsingHelperTests(unittest.TestCase):
    def test_env_int_absent_uses_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOME_UNUSED_INT_VAR", None)
            self.assertEqual(config._env_int("SOME_UNUSED_INT_VAR", 7), 7)

    def test_env_int_malformed_raises_configerror(self):
        with mock.patch.dict(os.environ, {"SOME_INT_VAR": "not-an-int"}):
            with self.assertRaises(config.ConfigError):
                config._env_int("SOME_INT_VAR", 7)

    def test_env_float_malformed_raises_configerror(self):
        with mock.patch.dict(os.environ, {"SOME_FLOAT_VAR": "abc"}):
            with self.assertRaises(config.ConfigError):
                config._env_float("SOME_FLOAT_VAR", 1.0)

    def test_env_float_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"SOME_FLOAT_VAR": value}):
                with self.assertRaises(config.ConfigError):
                    config._env_float("SOME_FLOAT_VAR", 1.0)

    def test_env_bool_accepts_common_true_false_spellings(self):
        for truthy in ("1", "true", "True", "yes", "on"):
            with mock.patch.dict(os.environ, {"SOME_BOOL_VAR": truthy}):
                self.assertTrue(config._env_bool("SOME_BOOL_VAR", False))
        for falsy in ("0", "false", "False", "no", "off"):
            with mock.patch.dict(os.environ, {"SOME_BOOL_VAR": falsy}):
                self.assertFalse(config._env_bool("SOME_BOOL_VAR", True))

    def test_env_bool_malformed_raises_configerror(self):
        with mock.patch.dict(os.environ, {"SOME_BOOL_VAR": "maybe"}):
            with self.assertRaises(config.ConfigError):
                config._env_bool("SOME_BOOL_VAR", False)

    def test_require_positive_int_rejects_zero_and_negative(self):
        with self.assertRaises(config.ConfigError):
            config._require_positive_int("SOME_VAR", 0)
        with self.assertRaises(config.ConfigError):
            config._require_positive_int("SOME_VAR", -1)
        self.assertEqual(config._require_positive_int("SOME_VAR", 5), 5)

    def test_require_positive_float_rejects_zero_and_negative(self):
        with self.assertRaises(config.ConfigError):
            config._require_positive_float("SOME_VAR", 0.0)
        with self.assertRaises(config.ConfigError):
            config._require_positive_float("SOME_VAR", -0.5)
        with self.assertRaises(config.ConfigError):
            config._require_positive_float("SOME_VAR", float("nan"))
        with self.assertRaises(config.ConfigError):
            config._require_positive_float("SOME_VAR", float("inf"))
        self.assertEqual(config._require_positive_float("SOME_VAR", 2.5), 2.5)


class ConfigModuleReloadFailFastTests(unittest.TestCase):
    """Reloads the config module under patched env vars to prove malformed
    values fail the whole import, not just an isolated helper call."""

    def tearDown(self):
        # Always restore the module to its normal (env-unpatched) state so
        # later tests in this process see an unaffected config module.
        importlib.reload(config)

    def test_absent_env_vars_reload_cleanly_with_defaults(self):
        # Sanity check: a plain reload with no malformed env vars must not
        # raise, and should reproduce the documented default.
        importlib.reload(config)
        self.assertEqual(config.MAX_COLLECTION_SIZE, 5000)

    def test_malformed_int_env_var_fails_import_not_silently_defaulted(self):
        # Note: assert on ValueError (not config.ConfigError) — reload()
        # re-executes the class statement, rebinding config.ConfigError to a
        # new class object; ValueError's identity is stable across reloads.
        with mock.patch.dict(os.environ, {"MAX_JSON_BODY_BYTES": "not-a-number"}):
            with self.assertRaises(ValueError):
                importlib.reload(config)

    def test_negative_bound_env_var_fails_import(self):
        with mock.patch.dict(os.environ, {"MAX_COLLECTION_SIZE": "-10"}):
            with self.assertRaises(ValueError):
                importlib.reload(config)

    def test_non_finite_float_env_var_fails_import(self):
        with mock.patch.dict(os.environ, {"RATE_LIMIT_WINDOW_SECONDS": "nan"}):
            with self.assertRaises(ValueError):
                importlib.reload(config)

    def test_malformed_bool_env_var_fails_import(self):
        with mock.patch.dict(os.environ, {"ENABLE_PREDICTION_LOG": "maybe"}):
            with self.assertRaises(ValueError):
                importlib.reload(config)

    def test_valid_env_override_is_applied(self):
        with mock.patch.dict(os.environ, {"MAX_COLLECTION_SIZE": "42"}):
            importlib.reload(config)
            self.assertEqual(config.MAX_COLLECTION_SIZE, 42)

    def test_backend_access_token_defaults_to_none_and_trims_whitespace(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BACKEND_ACCESS_TOKEN", None)
            importlib.reload(config)
            self.assertIsNone(config.BACKEND_ACCESS_TOKEN)
        with mock.patch.dict(os.environ, {"BACKEND_ACCESS_TOKEN": "  s3cret  "}):
            importlib.reload(config)
            self.assertEqual(config.BACKEND_ACCESS_TOKEN, "s3cret")


if __name__ == "__main__":
    unittest.main()
