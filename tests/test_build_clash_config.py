import copy
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_clash_config.py"
SPEC = importlib.util.spec_from_file_location("build_clash_config", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
build_clash_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_clash_config)


class FakeYamlModule:
    @staticmethod
    def safe_load(text):
        return json.loads(text)

    @staticmethod
    def safe_dump(data, **_kwargs):
        return json.dumps(data, ensure_ascii=False, indent=2)


class SubscriptionDecisionTests(unittest.TestCase):
    def test_depleted_subscription_is_skipped_without_threshold(self):
        now = dt.datetime(2026, 3, 25, tzinfo=dt.timezone.utc)

        decision = build_clash_config.decide_subscription_handling(
            remaining=0,
            expire=int((now + dt.timedelta(days=3)).timestamp()),
            threshold=None,
            reset_at_dt=None,
            reset_days_hint=None,
            now=now,
        )

        self.assertEqual(decision["status"], "skipped(depleted)")
        self.assertTrue(decision["should_skip_proxies"])
        self.assertIsNone(decision["pause_until_utc"])
        self.assertFalse(decision["prune"])

    def test_expired_subscription_is_skipped_even_with_remaining_traffic(self):
        now = dt.datetime(2026, 3, 25, tzinfo=dt.timezone.utc)

        decision = build_clash_config.decide_subscription_handling(
            remaining=128,
            expire=int((now - dt.timedelta(minutes=1)).timestamp()),
            threshold=None,
            reset_at_dt=None,
            reset_days_hint=None,
            now=now,
        )

        self.assertEqual(decision["status"], "skipped(expired)")
        self.assertTrue(decision["should_skip_proxies"])
        self.assertIsNone(decision["pause_until_utc"])
        self.assertFalse(decision["prune"])


class BuildStateWriteBackTests(unittest.TestCase):
    def test_build_writes_subscription_state_without_pause_or_prune_flags(self):
        template = {
            "subscriptions": [{"name": "demo", "url": "https://example.com/sub"}],
            "port": 7890,
            "proxy-groups": [
                {
                    "name": "Proxy",
                    "type": "select",
                    "fallback-groups": ["DIRECT"],
                    "proxies": [],
                },
                {
                    "name": "AI",
                    "type": "fallback",
                    "fallback-groups": ["DIRECT"],
                    "proxies": [],
                    "url": "https://www.gstatic.com/generate_204",
                },
            ],
            "rules": ["MATCH,DIRECT"],
        }
        proxies = [{"name": "demo-node", "type": "ss", "server": "1.1.1.1", "port": 443}]
        updated_subscriptions = [
            {
                "name": "demo",
                "url": "https://example.com/sub",
                "last_remaining_bytes": 0,
                "last_expire": 1800000000,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            template_path = root / "test.yaml"
            provider_path = root / "providers" / "all.yaml"
            output_path = root / "dist" / "config.yaml"
            report_path = root / "dist" / "subscriptions_report.md"
            status_path = root / "dist" / "build_status.json"
            template_path.write_text(
                json.dumps(copy.deepcopy(template), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with (
                mock.patch.object(build_clash_config, "REPO_ROOT", root),
                mock.patch.object(build_clash_config, "TEMPLATE_PATH", template_path),
                mock.patch.object(build_clash_config, "PROVIDER_PATH", provider_path),
                mock.patch.object(build_clash_config, "OUTPUT_CONFIG", output_path),
                mock.patch.object(
                    build_clash_config, "SUBSCRIPTION_REPORT_PATH", report_path
                ),
                mock.patch.object(build_clash_config, "BUILD_STATUS_PATH", status_path),
                mock.patch.object(build_clash_config, "yaml", FakeYamlModule()),
                mock.patch.dict(build_clash_config.os.environ, {}, clear=True),
                mock.patch.object(
                    build_clash_config,
                    "fetch_proxies",
                    return_value=(
                        proxies,
                        [],
                        [],
                        updated_subscriptions,
                        {"demo-node": "demo"},
                    ),
                ),
            ):
                status = build_clash_config.build()

            saved_template = build_clash_config.load_yaml(
                template_path.read_text(encoding="utf-8")
            )
            saved_subscription = saved_template["subscriptions"][0]

            self.assertTrue(status["success"])
            self.assertTrue(status["subscriptions_state_written_back"])
            self.assertEqual(saved_subscription["last_remaining_bytes"], 0)
            self.assertEqual(saved_subscription["last_expire"], 1800000000)


class SubscriptionFetchTimeoutTests(unittest.TestCase):
    def test_fetch_proxies_uses_sixty_second_timeout(self):
        observed = {}

        class FakeHeaders(dict):
            def get_content_charset(self):
                return "utf-8"

        class FakeResponse:
            def __init__(self):
                self.headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "proxies": [
                            {
                                "name": "demo-node",
                                "type": "ss",
                                "server": "1.1.1.1",
                                "port": 443,
                                "cipher": "aes-128-gcm",
                                "password": "secret",
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(req, timeout, context):
            observed["timeout"] = timeout
            return FakeResponse()

        with mock.patch.object(
            build_clash_config.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            proxies, _, _, _, _ = build_clash_config.fetch_proxies(
                [{"name": "demo", "url": "https://example.com/sub"}]
            )

        self.assertEqual(observed["timeout"], 60)
        self.assertEqual(len(proxies), 1)


if __name__ == "__main__":
    unittest.main()
