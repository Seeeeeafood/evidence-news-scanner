import os
import unittest

from news_scanner_v2.config import build_config, llm_model_roles


ROLE_ENV_VARS = (
    "NEWS_SCANNER_V2_DISCOVERY_LLM_MODEL",
    "NEWS_SCANNER_V2_EDITORIAL_LLM_MODEL",
    "NEWS_SCANNER_V2_THEME_EDITOR_LLM_MODEL",
    "NEWS_SCANNER_V2_SUMMARY_LLM_MODEL",
    "NEWS_SCANNER_V2_CRITIC_LLM_MODEL",
)


class ConfigTests(unittest.TestCase):
    def test_role_models_inherit_base_llm_model(self) -> None:
        old = {key: os.environ.get(key) for key in ROLE_ENV_VARS}
        for key in ROLE_ENV_VARS:
            os.environ.pop(key, None)
        try:
            config = build_config(llm_model="gpt-5.5")
            self.assertEqual(
                llm_model_roles(config),
                {
                    "base": "gpt-5.5",
                    "discovery": "gpt-5.5",
                    "editorial": "gpt-5.5",
                    "theme_editor": "gpt-5.5",
                    "summary": "gpt-5.5",
                    "critic": "gpt-5.5",
                },
            )
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_role_models_can_be_overridden_by_env(self) -> None:
        old = {key: os.environ.get(key) for key in ROLE_ENV_VARS}
        try:
            os.environ["NEWS_SCANNER_V2_EDITORIAL_LLM_MODEL"] = "gpt-5.3-codex-spark"
            os.environ["NEWS_SCANNER_V2_SUMMARY_LLM_MODEL"] = "gpt-5.3-codex-spark"
            config = build_config(llm_model="gpt-5.5")
            roles = llm_model_roles(config)
            self.assertEqual(roles["base"], "gpt-5.5")
            self.assertEqual(roles["discovery"], "gpt-5.5")
            self.assertEqual(roles["editorial"], "gpt-5.3-codex-spark")
            self.assertEqual(roles["theme_editor"], "gpt-5.5")
            self.assertEqual(roles["summary"], "gpt-5.3-codex-spark")
            self.assertEqual(roles["critic"], "gpt-5.5")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_explicit_role_override_wins_over_env(self) -> None:
        old = os.environ.get("NEWS_SCANNER_V2_EDITORIAL_LLM_MODEL")
        os.environ["NEWS_SCANNER_V2_EDITORIAL_LLM_MODEL"] = "env-model"
        try:
            config = build_config(
                llm_model="gpt-5.5",
                editorial_llm_model="cli-model",
            )
            self.assertEqual(config.editorial_llm_model, "cli-model")
        finally:
            if old is None:
                os.environ.pop("NEWS_SCANNER_V2_EDITORIAL_LLM_MODEL", None)
            else:
                os.environ["NEWS_SCANNER_V2_EDITORIAL_LLM_MODEL"] = old


if __name__ == "__main__":
    unittest.main()
