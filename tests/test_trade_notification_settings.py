#!/usr/bin/env python3
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import niuone_dashboard as dashboard  # noqa: E402
import notifications  # noqa: E402


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


def notification_card_tag(page: str, channel: str) -> str:
    marker = f"data-notification-channel-card='{channel}'"
    marker_at = page.index(marker)
    tag_start = page.rfind("<", 0, marker_at)
    tag_end = page.index(">", marker_at) + 1
    return page[tag_start:tag_end]


def notification_card_markup(page: str, channel: str) -> str:
    marker = f"data-notification-channel-card='{channel}'"
    marker_at = page.index(marker)
    tag_start = page.rfind("<article", 0, marker_at)
    tag_end = page.index("</article>", marker_at) + len("</article>")
    return page[tag_start:tag_end]


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

        page = dashboard.render_admin_group_page("notifications").decode("utf-8")
        self.assertEqual(page.count("<h2>交易通知</h2>"), 1)
        for label in (
            "启用模拟成交通知",
            "飞书机器人 Webhook",
            "钉钉机器人 Webhook",
            "企业微信机器人 Webhook",
            "Telegram Bot Token",
            "Telegram Chat ID",
        ):
            self.assertIn(label, page)

        self.assertIn("data-notification-channels", page)
        self.assertIn("data-notification-channel-picker", page)
        self.assertIn("data-notification-channel-add", page)
        for channel in ("feishu", "dingtalk", "wecom", "telegram"):
            self.assertEqual(page.count(f"data-notification-channel-card='{channel}'"), 1)
            self.assertEqual(page.count(f"data-notification-channel-remove='{channel}'"), 1)
            self.assertEqual(page.count(f"data-notification-channel-test='{channel}'"), 1)
            self.assertEqual(page.count(f"<option value='{channel}'"), 1)

    def test_unselected_channels_are_hidden_and_excluded_from_form_config(self):
        page = dashboard.render_admin_group_page("notifications").decode("utf-8")

        for channel in ("feishu", "dingtalk", "wecom", "telegram"):
            tag = notification_card_tag(page, channel)
            markup = notification_card_markup(page, channel)
            self.assertIn(" hidden", tag)
            self.assertIn("aria-hidden='true'", tag)
            self.assertIn("data-notification-channel-fields disabled", markup)
            self.assertLess(markup.index("data-notification-channel-enabled"), markup.index("<fieldset"))
        for name in NOTIFICATION_ENV_NAMES:
            self.assertEqual(page.count(f"name='env__{name}'"), 1, name)

    def test_enabled_channel_is_rendered_as_an_active_configuration_card(self):
        dashboard.write_env_file_values(
            {
                "DASHBOARD_FEISHU_NOTIFICATION_ENABLED": "1",
                "DASHBOARD_FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/private-feishu",
            },
            self.env_path,
        )

        page = dashboard.render_admin_group_page("notifications").decode("utf-8")
        tag = notification_card_tag(page, "feishu")
        markup = notification_card_markup(page, "feishu")
        self.assertNotIn(" hidden", tag)
        self.assertIn("aria-hidden='false'", tag)
        self.assertIn("data-notification-channel-enabled>", markup)
        self.assertIn("value='1'", markup)
        self.assertIn("data-notification-channel-fields aria-labelledby='notification-channel-name-feishu'>", markup)
        self.assertNotIn("data-notification-channel-fields disabled", markup)
        self.assertIn("<option value='feishu' hidden disabled>", page)
        self.assertIn("data-notification-channel-empty hidden", page)

    def test_process_enabled_channel_is_visible_even_without_file_override(self):
        os.environ["DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED"] = "1"
        page = dashboard.render_admin_group_page("notifications").decode("utf-8")

        tag = notification_card_tag(page, "telegram")
        self.assertNotIn(" hidden", tag)
        self.assertIn("aria-hidden='false'", tag)

    def test_add_remove_interaction_hooks_are_embedded(self):
        page = dashboard.render_admin_group_page("notifications").decode("utf-8")

        self.assertIn("function setNotificationChannelVisibility", page)
        self.assertIn("function syncNotificationChannelSettings", page)
        self.assertIn("target.closest('[data-notification-channel-add]')", page)
        self.assertIn("target.closest('[data-notification-channel-remove]')", page)
        self.assertIn("target.closest('[data-notification-channel-test]')", page)
        self.assertIn("function notificationTestBody", page)
        self.assertIn("fetch('/api/admin/notifications/test'", page)
        self.assertIn("body: notificationTestBody(testCard)", page)
        self.assertIn("status.textContent = message || ''", page)
        self.assertIn("function applyEnvConfigState", page)
        self.assertIn("applyEnvConfigState(form, payload.config)", page)
        self.assertIn("function clearRemovedNotificationChannelFields", page)
        self.assertIn("if (enabledInput && enabledInput.value === '1') return;", page)
        self.assertIn("field.value = '';", page)
        self.assertIn("clearRemovedNotificationChannelFields(form);", page)
        self.assertIn("fields.disabled = !active", page)
        self.assertIn("resetEnvSaveIfDirty(notificationAddButton.closest('form'))", page)

        apply_state_at = page.index("applyEnvConfigState(form, payload.config);")
        clear_removed_at = page.index("clearRemovedNotificationChannelFields(form);", apply_state_at)
        sync_channels_at = page.index("syncNotificationChannelSettings();", clear_removed_at)
        self.assertLess(apply_state_at, clear_removed_at)
        self.assertLess(clear_removed_at, sync_channels_at)

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

    def test_disabled_channel_updates_clear_exactly_that_channels_fields(self):
        expected_by_channel = {
            str(channel["id"]): {
                str(channel["enabled_name"]),
                *(str(name) for name in channel["field_names"]),
            }
            for channel in dashboard.NOTIFICATION_CHANNEL_SETTINGS
        }
        for channel in dashboard.NOTIFICATION_CHANNEL_SETTINGS:
            channel_id = str(channel["id"])
            enabled_name = str(channel["enabled_name"])
            with self.subTest(channel=channel_id):
                self.assertEqual(
                    dashboard.removed_notification_config_names({enabled_name: "0"}),
                    expected_by_channel[channel_id],
                )
                self.assertEqual(
                    dashboard.removed_notification_config_names({enabled_name: "1"}),
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
        disabled_updates = {
            str(channel["enabled_name"]): "0"
            for channel in dashboard.NOTIFICATION_CHANNEL_SETTINGS
        }
        self.assertEqual(
            dashboard.removed_notification_config_names(disabled_updates),
            set().union(*expected_by_channel.values()),
        )
        self.assertEqual(
            dashboard.removed_notification_config_names(
                {"DASHBOARD_TELEGRAM_CHAT_ID": "-1001234567890"}
            ),
            set(),
        )

    def test_removing_a_never_configured_channel_is_unchanged(self):
        disabled_updates = {"DASHBOARD_TELEGRAM_NOTIFICATION_ENABLED": "0"}
        result = dashboard.write_env_file_values(
            disabled_updates,
            self.env_path,
            clear_names=dashboard.removed_notification_config_names(disabled_updates),
        )

        self.assertFalse(result["changed"])
        self.assertEqual(result["changed_names"], [])
        self.assertFalse(self.env_path.exists())

    def test_disabling_channels_clears_saved_credentials_from_file_and_process_env(self):
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
            clear_names=dashboard.removed_notification_config_names(disabled_updates),
        )
        dashboard.sync_business_runtime_settings(result["changed_names"])

        values = dashboard.parse_env_file(self.env_path, include_container_overrides=False)
        for name in {*enabled_updates, *credentials}:
            self.assertNotIn(name, values)
            self.assertNotIn(name, os.environ)
            self.assertIn(name, result["changed_names"])

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
            clear_names=dashboard.removed_notification_config_names(disabled_updates),
        )
        dashboard.sync_business_runtime_settings(removed["changed_names"])

        payload = dashboard.build_admin_config_payload()
        items = {item["name"]: item for item in payload["items"]}
        self.assertEqual(items["DASHBOARD_TELEGRAM_BOT_TOKEN"]["current_state"], "未设置")
        self.assertEqual(items["DASHBOARD_TELEGRAM_CHAT_ID"]["current_state"], "未设置")

        page = dashboard.render_admin_group_page("notifications").decode("utf-8")
        tag = notification_card_tag(page, "telegram")
        markup = notification_card_markup(page, "telegram")
        self.assertIn(" hidden", tag)
        self.assertIn(
            "data-env-current='DASHBOARD_TELEGRAM_BOT_TOKEN'>未设置</span>",
            markup,
        )
        self.assertIn(
            "data-env-current='DASHBOARD_TELEGRAM_CHAT_ID'>未设置</span>",
            markup,
        )
        self.assertIn("name='env__DASHBOARD_TELEGRAM_BOT_TOKEN'", markup)
        self.assertIn("placeholder='未设置'", markup)
        self.assertIn("name='env__DASHBOARD_TELEGRAM_CHAT_ID'", markup)
        self.assertIn("value=''", markup)

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

        page = dashboard.render_admin_group_page("notifications").decode("utf-8")
        markup = notification_card_markup(page, "telegram")
        self.assertIn("name='env__DASHBOARD_TELEGRAM_CHAT_ID'", markup)
        self.assertIn(f"value='{chat_id}'", markup)
        self.assertIn(
            "data-env-current='DASHBOARD_TELEGRAM_CHAT_ID'>已设置</span>",
            markup,
        )
        self.assertNotIn(
            f"data-env-current='DASHBOARD_TELEGRAM_CHAT_ID'>{chat_id}</span>",
            markup,
        )

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
        page = dashboard.render_admin_group_page("notifications").decode("utf-8")
        items = {item["name"]: item for item in payload["items"]}
        for name, secret in secrets.items():
            self.assertTrue(items[name]["secret"])
            self.assertEqual(items[name]["file_value"], "")
            self.assertEqual(items[name]["effective"], "已设置，留空保持不变")
            self.assertEqual(items[name]["current_state"], "已设置")
            self.assertNotIn(secret, repr(payload))
            self.assertNotIn(secret, page)
            self.assertIn(f"type='password' name='env__{name}'", page)
            self.assertIn(f"data-env-current='{name}'>已设置</span>", page)
        self.assertIn("当前状态：", page)
        self.assertNotIn("当前：", page)

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
        self.assertIn("移除”会先停用并收起该渠道", readme)
        self.assertIn("删除该渠道已经保存的 Webhook、Bot Token、Chat ID 和签名密钥", readme)
        self.assertIn("再次添加时状态为“未设置”", readme)
        self.assertIn("如果在保存前重新添加渠道，原配置不会被删除", readme)
        self.assertIn("发送测试通知", readme)
        self.assertIn("不会创建成交记录、修改资金或持仓", readme)
        self.assertIn("不自动重试", readme)


if __name__ == "__main__":
    unittest.main()
