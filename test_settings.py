import unittest
from types import SimpleNamespace
from unittest.mock import patch

import settings


class SecretConfigurationTests(unittest.TestCase):
    def test_environment_secret_takes_precedence(self):
        with patch.object(settings.os, "getenv", return_value="env-secret"):
            with patch.object(
                settings,
                "st",
                SimpleNamespace(secrets={"GROQ_API_KEY": "cloud-secret"}),
            ):
                self.assertEqual(
                    settings.get_secret("GROQ_API_KEY"),
                    "env-secret",
                )

    def test_streamlit_secret_is_used_when_environment_is_empty(self):
        with patch.object(settings.os, "getenv", return_value=None):
            with patch.object(
                settings,
                "st",
                SimpleNamespace(secrets={"GROQ_API_KEY": "cloud-secret"}),
            ):
                self.assertEqual(
                    settings.get_secret("GROQ_API_KEY"),
                    "cloud-secret",
                )

    def test_missing_secret_has_configuration_guidance(self):
        with patch.object(settings.os, "getenv", return_value=None):
            with patch.object(
                settings,
                "st",
                SimpleNamespace(secrets={}),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "local .env file or in Streamlit Community Cloud app Secrets",
                ):
                    settings.get_secret("GROQ_API_KEY")

    def test_require_secrets_lists_all_missing_values(self):
        with patch.object(settings.os, "getenv", return_value=None):
            with patch.object(
                settings,
                "st",
                SimpleNamespace(secrets={}),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "GROQ_API_KEY, DEEPGRAM_API_KEY",
                ):
                    settings.require_secrets(
                        "GROQ_API_KEY",
                        "DEEPGRAM_API_KEY",
                    )


if __name__ == "__main__":
    unittest.main()
