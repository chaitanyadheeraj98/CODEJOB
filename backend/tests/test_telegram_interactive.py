import unittest

from app import main
from app.telegram_bot import TelegramBotService, TelegramReply


class TelegramInteractiveMainTests(unittest.TestCase):
    def setUp(self) -> None:
        main.telegram_pending_inputs.clear()

    def test_start_returns_main_menu_reply(self) -> None:
        reply = main._handle_telegram_command(123, "u1", "tester", "/start")
        self.assertIsInstance(reply, TelegramReply)
        typed = reply if isinstance(reply, TelegramReply) else None
        self.assertIsNotNone(typed)
        self.assertIn("Choose a section", typed.text)
        self.assertIsNotNone(typed.inline_keyboard)

    def test_callback_menu_edit_response(self) -> None:
        reply = main._handle_telegram_callback(123, "u1", "tester", "menu:config:0", 55)
        self.assertIsInstance(reply, TelegramReply)
        typed = reply if isinstance(reply, TelegramReply) else None
        self.assertIsNotNone(typed)
        self.assertEqual(typed.edit_message_id, 55)
        self.assertIn("Config menu", typed.text)
        self.assertIsNotNone(typed.inline_keyboard)

    def test_flow_sets_pending_and_cancel_clears(self) -> None:
        reply = main._handle_telegram_callback(123, "u1", "tester", "flow:await_setquery", 21)
        self.assertIsInstance(reply, TelegramReply)
        self.assertEqual(main.telegram_pending_inputs.get(123), "await_setquery")
        cancel = main._handle_telegram_callback(123, "u1", "tester", "cancel:pending", 21)
        self.assertIsInstance(cancel, TelegramReply)
        self.assertNotIn(123, main.telegram_pending_inputs)

    def test_pagination_has_more_button(self) -> None:
        buttons = [main._tg_btn(f"Item {i}", f"cmd:/x{i}") for i in range(8)]
        rows = main._telegram_paginate_buttons(buttons, 0, menu_action="config", include_home=True, include_back=True)
        flat = [button["text"] for row in rows for button in row]
        self.assertIn("More", flat)


class TelegramBotServiceCallbackTests(unittest.TestCase):
    def test_callback_update_is_processed(self) -> None:
        sent: list[tuple[str, dict]] = []
        callback_calls: list[str] = []

        def fake_post(method: str, payload: dict) -> dict:
            sent.append((method, payload))
            return {"ok": True, "result": []}

        def command_handler(chat_id: int, user_id: str, username: str, text: str) -> str:
            _ = (chat_id, user_id, username)
            return f"echo:{text}"

        def callback_handler(chat_id: int, user_id: str, username: str, data: str, message_id: int) -> TelegramReply:
            _ = (chat_id, user_id, username, message_id)
            callback_calls.append(data)
            return TelegramReply(text="updated", edit_message_id=42, callback_notice="ok")

        service = TelegramBotService(
            token="x",
            allowed_chat_ids={999},
            alerts_enabled=True,
            command_handler=command_handler,
            callback_handler=callback_handler,
        )
        service._post_json = fake_post  # type: ignore[method-assign]
        update = {
            "callback_query": {
                "id": "cb1",
                "data": "menu:main:0",
                "from": {"id": 1, "username": "user1"},
                "message": {"message_id": 42, "chat": {"id": 999}},
            }
        }
        service._handle_update(update)
        self.assertEqual(callback_calls, ["menu:main:0"])
        methods = [item[0] for item in sent]
        self.assertIn("answerCallbackQuery", methods)
        self.assertIn("editMessageText", methods)


if __name__ == "__main__":
    unittest.main()
