import os
from pathlib import Path
import tempfile
import unittest

from news_scanner_v2.auth_config import (
    load_brave_api_key,
    load_fmp_api_key,
    load_openai_api_key,
    load_polygon_api_key,
    load_telegram_bot_token,
)


class AuthConfigTests(unittest.TestCase):
    def test_load_brave_api_key_from_env_first(self) -> None:
        old = os.environ.get("BRAVE_SEARCH_API_KEY")
        os.environ["BRAVE_SEARCH_API_KEY"] = "env-token"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(
                    load_brave_api_key(legacy_root=Path(tmp)),
                    "env-token",
                )
        finally:
            if old is None:
                os.environ.pop("BRAVE_SEARCH_API_KEY", None)
            else:
                os.environ["BRAVE_SEARCH_API_KEY"] = old

    def test_load_brave_api_key_missing_returns_none(self) -> None:
        old = {
            name: os.environ.get(name)
            for name in (
                "BRAVE_SEARCH_API_KEY",
                "BRAVE_API_KEY",
                "BRAVE_SUBSCRIPTION_TOKEN",
            )
        }
        for name in old:
            os.environ.pop(name, None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(load_brave_api_key(legacy_root=Path(tmp)))
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_load_telegram_bot_token_from_env_first(self) -> None:
        old = os.environ.get("TELEGRAM_BOT_TOKEN")
        os.environ["TELEGRAM_BOT_TOKEN"] = "env-telegram-token"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(
                    load_telegram_bot_token(legacy_root=Path(tmp)),
                    "env-telegram-token",
                )
        finally:
            if old is None:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            else:
                os.environ["TELEGRAM_BOT_TOKEN"] = old

    def test_load_telegram_bot_token_missing_returns_none(self) -> None:
        old = os.environ.get("TELEGRAM_BOT_TOKEN")
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(load_telegram_bot_token(legacy_root=Path(tmp)))
        finally:
            if old is not None:
                os.environ["TELEGRAM_BOT_TOKEN"] = old

    def test_load_openai_api_key_from_env_only(self) -> None:
        old = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "env-openai-token"
        try:
            self.assertEqual(load_openai_api_key(), "env-openai-token")
        finally:
            if old is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old

    def test_load_openai_api_key_missing_returns_none(self) -> None:
        old = os.environ.get("OPENAI_API_KEY")
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            self.assertIsNone(load_openai_api_key())
        finally:
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old

    def test_load_polygon_api_key_from_env_first(self) -> None:
        old = os.environ.get("POLYGON_API_KEY")
        os.environ["POLYGON_API_KEY"] = "env-polygon-token"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(
                    load_polygon_api_key(legacy_root=Path(tmp)),
                    "env-polygon-token",
                )
        finally:
            if old is None:
                os.environ.pop("POLYGON_API_KEY", None)
            else:
                os.environ["POLYGON_API_KEY"] = old

    def test_load_polygon_api_key_missing_returns_none(self) -> None:
        old = os.environ.get("POLYGON_API_KEY")
        os.environ.pop("POLYGON_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(load_polygon_api_key(legacy_root=Path(tmp)))
        finally:
            if old is not None:
                os.environ["POLYGON_API_KEY"] = old

    def test_load_fmp_api_key_from_env_first(self) -> None:
        old = os.environ.get("FMP_API_KEY")
        os.environ["FMP_API_KEY"] = "env-fmp-token"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(
                    load_fmp_api_key(legacy_root=Path(tmp)),
                    "env-fmp-token",
                )
        finally:
            if old is None:
                os.environ.pop("FMP_API_KEY", None)
            else:
                os.environ["FMP_API_KEY"] = old

    def test_load_fmp_api_key_missing_returns_none(self) -> None:
        old = os.environ.get("FMP_API_KEY")
        os.environ.pop("FMP_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(load_fmp_api_key(legacy_root=Path(tmp)))
        finally:
            if old is not None:
                os.environ["FMP_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
