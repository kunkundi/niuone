#!/usr/bin/env python3
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
    sys.path.insert(0, str(COMPAT))

import niuone_dashboard as dashboard  # noqa: E402
import notifications  # noqa: E402

ADMIN_VUE = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (
        ROOT / "web" / "src" / "components" / "AdminEnvInput.vue",
        ROOT / "web" / "src" / "components" / "AdminNotificationSettings.vue",
        ROOT / "web" / "src" / "components" / "AdminSettingsGroup.vue",
    )
)


NOTIFICATION_ENV_NAMES = [
    "DASHBOARD_NOTIFICATION_ENABLED",
    "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS",
    "DASHBOARD_FEISHU_NOTIFICATION_ENABLED",
    "DASHBOARD_FEISHU_WEBHOOK_URL",
    "DASHBOARD_FEISHU_SIGNING_SECRET",
    "DASHBOARD_DINGTALK_NOTIFICATION_ENABLED",
    "DASHBOARD_DINGTALK_WEBHOOK_URL",
    "DASHBOARD_DINGTALK_SIGNING_SECRET",
    "DASHBOARD_WECOM_NOTIFICATION_ENABLED",
    "DASHBOARD_WECOM_WEBHOOK_URL",
    "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED",
    "DASHBOARD_TELEGRAM_BOT_TOKEN",
    "DASHBOARD_TELEGRAM_CHAT_ID",
]


class TradeNotificationSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_path = Path(self.tmp.name) / "dashboard.env"
        self.original_path = dashboard.DASHBOARD_ENV_FILE
        dashboard.DASHBOARD_ENV_FILE = self.env_path
        self.original_env = {name: os.environ.pop(name, None) for name in NOTIFICATION_ENV_NAMES}

    def tearDown(self):
        dashboard.DASHBOARD_ENV_FILE = self.original_path
        for name, value in self.original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()

    def test_all_notification_settings_are_visible_in_one_group(self):
        self.assertTrue(set(NOTIFICATION_ENV_NAMES).issubset(dashboard.ADMIN_VISIBLE_ENV_NAMES))
        schema = [dashboard.ENV_CONFIG_BY_NAME[name] for name in NOTIFICATION_ENV_NAMES]
        self.assertTrue(all(item["group"] == "交易通知" for item in schema))

        payload = dashboard.build_admin_config_payload()
        items = {item["name"]: item for item in payload["items"]}
        for label in (
            "启用模拟成交通知",
            "飞书机器人 Webhook",
            "钉钉机器人 Webhook",
            "企业微信机器人 Webhook",
            "Telegram Bot Token",
            "Telegram Chat ID",
        ):
            self.assertIn(label, {item["label"] for item in items.values()})

        self.assertIn("data-notification-channels", ADMIN_VUE)
        self.assertIn("data-notification-channel-picker", ADMIN_VUE)
        self.assertIn("data-notification-channel-add", ADMIN_VUE)
        self.assertEqual(
            {channel["id"] for channel in payload["notification_channels"]},
            {"feishu", "dingtalk", "wecom", "telegram"},
        )
        for channel in ("feishu", "dingtalk", "wecom", "telegram"):
            self.assertIn(channel, {entry["id"] for entry in payload["notification_channels"]})

    def test_unselected_channels_are_hidden_and_excluded_from_form_config(self):
        payload = dashboard.build_admin_config_payload()
        items = {item["name"]: item for item in payload["items"]}
        for channel in payload["notification_channels"]:
            self.assertNotIn(
                str(items[channel["enabled_name"]]["effective"]).strip().lower(),
                {"1", "true", "yes", "on"},
            )
        for name in NOTIFICATION_ENV_NAMES:
            self.assertIn(name, items)
        self.assertIn('v-show="added[channel.id]"', ADMIN_VUE)
        self.assertIn(':hidden="added[channel.id]"', ADMIN_VUE)
        self.assertIn(':disabled="added[channel.id]"', ADMIN_VUE)

    def test_enabled_channel_is_rendered_as_an_active_configuration_card(self):
        dashboard.write_env_file_values(
            {
                "DASHBOARD_FEISHU_NOTIFICATION_ENABLED": "1",
                "DASHBOARD_FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/private-feishu",
            },
            self.env_path,
        )

        payload = dashboard.build_admin_config_payload()
        items = {item["name"]: item for item in payload["items"]}
        self.assertEqual(items["DASHBOARD_FEISHU_NOTIFICATION_ENABLED"]["effective"], "1")
        self.assertEqual(items["DASHBOARD_FEISHU_WEBHOOK_URL"]["current_state"], "已设置")

    def test_process_enabled_channel_is_visible_even_without_file_override(self):
        os.environ["DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED"] = "1"
        payload = dashboard.build_admin_config_payload()
        item = next(item for item in payload["items"] if item["name"] == "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED")
        self.assertEqual(item["effective"], "1")
        self.assertEqual(item["source"], "process env")

    def test_add_remove_interaction_hooks_are_owned_by_vue_components(self):
        page = ADMIN_VUE
        self.assertIn('function channelConfigured(channel)', page)
        self.assertIn('async function addChannel()', page)
        self.assertIn('async function toggleChannel(channelId)', page)
        self.assertIn('async function removeChannel(channelId)', page)
        self.assertIn('function applySavedConfig(updatedConfig)', page)
        self.assertIn('defineExpose({ applySavedConfig })', page)
        self.assertIn('@click.stop="addChannel"', page)
        self.assertIn('@click.stop="toggleChannel(channel.id)"', page)
        self.assertIn('@click.stop="removeChannel(channel.id)"', page)
        self.assertIn("@click.stop=\"emit('test-channel', channel.id)\"", page)
        self.assertIn('async function runNotificationTest(channelId)', page)
        self.assertIn("fetch('/api/admin/notifications/test'", page)
        self.assertIn('body.set(`env__${name}`', page)
        self.assertIn('data-notification-channel-activation', page)
        self.assertIn('role="switch"', page)
        self.assertIn(':aria-checked="String(Boolean(active[channel.id]))"', page)
        self.assertIn("active[channel.id] ? '已启用' : '已关闭'", page)
        self.assertIn('测试通知不受渠道开关影响', page)
        self.assertIn('notification_remove__${channel.id}', page)

    def test_notification_test_uses_unsaved_values_and_saved_secret_fallbacks(self):
        saved_webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/saved-feishu-hook"
        saved_secret = "saved-feishu-signing-secret"
        unsaved_webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/unsaved-feishu-hook"
        dashboard.write_env_file_values(
            {
                "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS": "9",
                "DASHBOARD_FEISHU_WEBHOOK_URL": saved_webhook,
                "DASHBOARD_FEISHU_SIGNING_SECRET": saved_secret,
                "DASHBOARD_TELEGRAM_BOT_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi",
            },
            self.env_path,
        )
        before_file = self.env_path.read_text(encoding="utf-8")
        captured = {}
        original_dispatch = notifications.dispatch_to_channel
        try:
            def fake_dispatch(notification, channel_name, env, **_kwargs):
                captured.update({"notification": notification, "channel": channel_name, "env": dict(env)})
                return notifications.DeliveryResult(channel_name, True, "")

            notifications.dispatch_to_channel = fake_dispatch
            result = dashboard.send_notification_test(
                "feishu",
                {
                    "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS": "3",
                    "DASHBOARD_FEISHU_WEBHOOK_URL": unsaved_webhook,
                    "DASHBOARD_FEISHU_SIGNING_SECRET": "   ",
                    "DASHBOARD_TELEGRAM_BOT_TOKEN": "must-be-ignored",
                    "DASHBOARD_NOTIFICATION_ENABLED": "1",
                },
            )
        finally:
            notifications.dispatch_to_channel = original_dispatch

        self.assertTrue(result["ok"])
        self.assertEqual(result["channel"], "feishu")
        self.assertEqual(captured["channel"], "feishu")
        self.assertEqual(
            set(captured["env"]),
            {
                "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS",
                "DASHBOARD_FEISHU_WEBHOOK_URL",
                "DASHBOARD_FEISHU_SIGNING_SECRET",
            },
        )
        self.assertEqual(captured["env"]["DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS"], "3")
        self.assertEqual(captured["env"]["DASHBOARD_FEISHU_WEBHOOK_URL"], unsaved_webhook)
        self.assertEqual(captured["env"]["DASHBOARD_FEISHU_SIGNING_SECRET"], saved_secret)
        self.assertIn("模拟成交，非实盘", captured["notification"].plain_text())
        self.assertIn("不代表真实买卖或成交", captured["notification"].plain_text())
        self.assertEqual(self.env_path.read_text(encoding="utf-8"), before_file)
        self.assertNotIn("DASHBOARD_FEISHU_WEBHOOK_URL", os.environ)

    def test_notification_test_does_not_reuse_a_cleared_telegram_chat_id(self):
        token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
        dashboard.write_env_file_values(
            {
                "DASHBOARD_TELEGRAM_BOT_TOKEN": token,
                "DASHBOARD_TELEGRAM_CHAT_ID": "-1001234567890",
            },
            self.env_path,
        )
        calls = []

        result = dashboard.send_notification_test(
            "telegram",
            {
                "DASHBOARD_TELEGRAM_BOT_TOKEN": "",
                "DASHBOARD_TELEGRAM_CHAT_ID": "",
                "DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS": "5",
            },
            transport=lambda *_args: calls.append(True) or {"ok": True},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["channel"], "telegram")
        self.assertIn("DASHBOARD_TELEGRAM_CHAT_ID", result["error"])
        self.assertNotIn(token, repr(result))
        self.assertEqual(calls, [])

    def test_notification_test_rejects_unknown_channel_and_blank_timeout(self):
        unknown = dashboard.send_notification_test("not-a-channel", {})
        blank_timeout = dashboard.send_notification_test(
            "wecom",
            {"DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS": ""},
        )

        self.assertEqual(unknown, {"ok": False, "channel": "", "error": "不支持的通知渠道"})
        self.assertFalse(blank_timeout["ok"])
        self.assertEqual(blank_timeout["channel"], "wecom")
        self.assertIn("不能为空", blank_timeout["error"])

    def test_removed_channels_clear_exactly_their_own_fields(self):
        expected_by_channel = {
            str(channel["id"]): {
                str(channel["enabled_name"]),
                *(str(name) for name in channel["field_names"]),
            }
            for channel in dashboard.NOTIFICATION_CHANNEL_SETTINGS
        }
        for channel in dashboard.NOTIFICATION_CHANNEL_SETTINGS:
            channel_id = str(channel["id"])
            with self.subTest(channel=channel_id):
                self.assertEqual(
                    dashboard.removed_notification_config_names({channel_id}),
                    expected_by_channel[channel_id],
                )
                self.assertEqual(
                    dashboard.removed_notification_config_names(set()),
                    set(),
                )

        telegram_fields = expected_by_channel["telegram"]
        self.assertEqual(
            telegram_fields,
            {
                "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED",
                "DASHBOARD_TELEGRAM_BOT_TOKEN",
                "DASHBOARD_TELEGRAM_CHAT_ID",
            },
        )
        self.assertEqual(
            dashboard.removed_notification_config_names(set(expected_by_channel)),
            set().union(*expected_by_channel.values()),
        )
        self.assertEqual(
            dashboard.removed_notification_config_names({"not-a-channel"}),
            set(),
        )

    def test_removing_a_never_configured_channel_is_unchanged(self):
        disabled_updates = {"DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED": "0"}
        result = dashboard.write_env_file_values(
            disabled_updates,
            self.env_path,
            clear_names=dashboard.removed_notification_config_names({"telegram"}),
        )

        self.assertFalse(result["changed"])
        self.assertEqual(result["changed_names"], [])
        self.assertFalse(self.env_path.exists())

    def test_deactivating_channels_preserves_saved_credentials(self):
        credentials = {
            "DASHBOARD_FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/private-feishu",
            "DASHBOARD_FEISHU_SIGNING_SECRET": "private-feishu-signing",
            "DASHBOARD_DINGTALK_WEBHOOK_URL": "https://oapi.dingtalk.com/robot/send?access_token=private-ding",
            "DASHBOARD_DINGTALK_SIGNING_SECRET": "private-ding-signing",
            "DASHBOARD_WECOM_WEBHOOK_URL": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=private-wecom",
            "DASHBOARD_TELEGRAM_BOT_TOKEN": "123456:private-telegram-token-value",
            "DASHBOARD_TELEGRAM_CHAT_ID": "-1001234567890",
        }
        enabled_updates = {
            str(channel["enabled_name"]): "1"
            for channel in dashboard.NOTIFICATION_CHANNEL_SETTINGS
        }
        initial = dashboard.write_env_file_values(
            {**enabled_updates, **credentials},
            self.env_path,
        )
        dashboard.sync_business_runtime_settings(initial["changed_names"])
        for name, value in credentials.items():
            self.assertEqual(os.environ.get(name), value, name)

        disabled_updates = {name: "0" for name in enabled_updates}
        result = dashboard.write_env_file_values(
            disabled_updates,
            self.env_path,
            clear_names=dashboard.removed_notification_config_names(set()),
        )
        dashboard.sync_business_runtime_settings(result["changed_names"])

        values = dashboard.parse_env_file(self.env_path, include_container_overrides=False)
        for name in enabled_updates:
            self.assertEqual(values[name], "0")
            self.assertEqual(os.environ.get(name), "0")
        for name, value in credentials.items():
            self.assertEqual(values[name], value)
            self.assertEqual(os.environ.get(name), value)

    def test_removed_telegram_rerenders_with_unset_state_for_the_next_add(self):
        saved = dashboard.write_env_file_values(
            {
                "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED": "1",
                "DASHBOARD_TELEGRAM_BOT_TOKEN": "123456:private-telegram-token-value",
                "DASHBOARD_TELEGRAM_CHAT_ID": "-1001234567890",
            },
            self.env_path,
        )
        dashboard.sync_business_runtime_settings(saved["changed_names"])
        disabled_updates = {"DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED": "0"}
        removed = dashboard.write_env_file_values(
            disabled_updates,
            self.env_path,
            clear_names=dashboard.removed_notification_config_names({"telegram"}),
        )
        dashboard.sync_business_runtime_settings(removed["changed_names"])

        payload = dashboard.build_admin_config_payload()
        items = {item["name"]: item for item in payload["items"]}
        self.assertEqual(items["DASHBOARD_TELEGRAM_BOT_TOKEN"]["current_state"], "未设置")
        self.assertEqual(items["DASHBOARD_TELEGRAM_CHAT_ID"]["current_state"], "未设置")
        self.assertEqual(items["DASHBOARD_TELEGRAM_BOT_TOKEN"]["file_value"], "")
        self.assertEqual(items["DASHBOARD_TELEGRAM_CHAT_ID"]["file_value"], "")
        self.assertIn(':placeholder="item.file_state', ADMIN_VUE)
        self.assertIn('v-show="added[channel.id]"', ADMIN_VUE)

    def test_configured_telegram_chat_id_uses_presence_state_in_payload_and_page(self):
        chat_id = "-1001234567890"
        dashboard.write_env_file_values(
            {
                "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED": "1",
                "DASHBOARD_TELEGRAM_CHAT_ID": chat_id,
            },
            self.env_path,
        )

        payload = dashboard.build_admin_config_payload()
        item = {
            entry["name"]: entry for entry in payload["items"]
        }["DASHBOARD_TELEGRAM_CHAT_ID"]
        self.assertFalse(item["secret"])
        self.assertEqual(item["file_value"], chat_id)
        self.assertEqual(item["current_state"], "已设置")
        self.assertNotEqual(item["current_state"], chat_id)

        self.assertIn('<AdminEnvInput :item="fieldItem(name)"', ADMIN_VUE)
        self.assertIn("currentState(name) || '未设置'", ADMIN_VUE)

    def test_notification_secrets_are_never_returned_or_rendered(self):
        secrets = {
            "DASHBOARD_FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/private-feishu",
            "DASHBOARD_FEISHU_SIGNING_SECRET": "private-feishu-signing",
            "DASHBOARD_DINGTALK_WEBHOOK_URL": "https://oapi.dingtalk.com/robot/send?access_token=private-ding",
            "DASHBOARD_DINGTALK_SIGNING_SECRET": "private-ding-signing",
            "DASHBOARD_WECOM_WEBHOOK_URL": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=private-wecom",
            "DASHBOARD_TELEGRAM_BOT_TOKEN": "123456:private-telegram-token-value",
        }
        dashboard.write_env_file_values(secrets, self.env_path)

        payload = dashboard.build_admin_config_payload()
        page = ADMIN_VUE
        items = {item["name"]: item for item in payload["items"]}
        for name, secret in secrets.items():
            self.assertTrue(items[name]["secret"])
            self.assertEqual(items[name]["file_value"], "")
            self.assertEqual(items[name]["effective"], "已设置，留空保持不变")
            self.assertEqual(items[name]["current_state"], "已设置")
            self.assertNotIn(secret, repr(payload))
            self.assertNotIn(secret, page)
        self.assertIn('type="password"', page)
        self.assertIn(':name="fieldName"', page)
        self.assertIn(':data-env-current="name"', page)
        self.assertIn('currentState(name)', page)

    def test_blank_or_whitespace_secret_preserves_existing_value(self):
        secret_name = "DASHBOARD_FEISHU_WEBHOOK_URL"
        original = "https://open.feishu.cn/open-apis/bot/v2/hook/private-feishu"
        first = dashboard.write_env_file_values({secret_name: original}, self.env_path)
        self.assertTrue(first["changed"])

        for blank in ("", "   ", "\t"):
            result = dashboard.write_env_file_values({secret_name: blank}, self.env_path)
            self.assertFalse(result["changed"])
            self.assertEqual(
                dashboard.parse_env_file(self.env_path, include_container_overrides=False)[secret_name],
                original,
            )

    def test_enabled_flags_and_chat_id_are_persisted_with_private_permissions(self):
        result = dashboard.write_env_file_values(
            {
                "DASHBOARD_NOTIFICATION_ENABLED": "1",
                "DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED": "1",
                "DASHBOARD_TELEGRAM_CHAT_ID": "-1001234567890",
            },
            self.env_path,
        )
        self.assertTrue(result["changed"])
        values = dashboard.parse_env_file(self.env_path, include_container_overrides=False)
        self.assertEqual(values["DASHBOARD_NOTIFICATION_ENABLED"], "1")
        self.assertEqual(values["DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED"], "1")
        self.assertEqual(values["DASHBOARD_TELEGRAM_CHAT_ID"], "-1001234567890")
        self.assertEqual(stat.S_IMODE(self.env_path.stat().st_mode), 0o600)

    def test_notification_timeout_is_limited(self):
        for value in ("1", "5", "30"):
            dashboard.validate_business_updates({"DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS": value})
        for value in ("0", "31", "-1"):
            with self.assertRaisesRegex(ValueError, "1 到 30"):
                dashboard.validate_business_updates({"DASHBOARD_NOTIFICATION_TIMEOUT_SECONDS": value})

    def test_saved_notification_settings_are_hot_applied_to_process_env(self):
        webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/private-feishu"
        updates = {
            "DASHBOARD_NOTIFICATION_ENABLED": "1",
            "DASHBOARD_FEISHU_NOTIFICATION_ENABLED": "1",
            "DASHBOARD_FEISHU_WEBHOOK_URL": webhook,
        }
        result = dashboard.write_env_file_values(updates, self.env_path)
        runtime = dashboard.sync_business_runtime_settings(result["changed_names"])

        self.assertIn("env", runtime["applied"])
        self.assertEqual(os.environ["DASHBOARD_NOTIFICATION_ENABLED"], "1")
        self.assertEqual(os.environ["DASHBOARD_FEISHU_NOTIFICATION_ENABLED"], "1")
        self.assertEqual(os.environ["DASHBOARD_FEISHU_WEBHOOK_URL"], webhook)

    def test_readme_documents_every_notification_channel_and_setting(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        for heading in (
            "### 交易通知配置",
            "#### 飞书",
            "#### 钉钉",
            "#### 企业微信",
            "#### Telegram",
            "#### 配置项与环境变量",
            "#### 常见问题",
        ):
            self.assertIn(heading, readme)
        for name in NOTIFICATION_ENV_NAMES:
            self.assertIn(f"`{name}`", readme)
        for official_url in (
            "https://open.feishu.cn/document/",
            "https://open.dingtalk.com/document/",
            "https://developer.work.weixin.qq.com/document/",
            "https://core.telegram.org/bots",
        ):
            self.assertIn(official_url, readme)
        self.assertIn("“关闭”只停止该渠道的成交通知", readme)
        self.assertIn("不会删除 Webhook、Bot Token、Chat ID 或签名密钥", readme)
        self.assertIn("“移除”会关闭并收起该渠道", readme)
        self.assertIn("删除该渠道已经保存的全部配置", readme)
        self.assertIn("再次添加时状态为“未设置”", readme)
        self.assertIn("如果在保存前重新添加渠道，原配置不会被删除", readme)
        self.assertIn("发送测试通知", readme)
        self.assertIn("不会创建成交记录、修改资金或持仓", readme)
        self.assertIn("不自动重试", readme)


if __name__ == "__main__":
    unittest.main()
