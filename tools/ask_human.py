import time
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import (
    MAX_ASK_TIMEOUT_SECONDS,
    build_button_card,
    client_and_identity,
    error_text,
    extract_card_id,
    get_cursor,
    get_webhook_secret,
    parse_options,
    poll_for_answer,
    save_pending_ask,
    set_cursor,
)


class AskHumanTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        chat_id = tool_parameters.get("chat_id")
        question = tool_parameters.get("question")
        if not chat_id:
            yield self.create_text_message("chat_id is required.")
            return
        if not question:
            yield self.create_text_message("question is required.")
            return

        try:
            options = parse_options(tool_parameters.get("options"))
        except ValueError as exc:
            yield self.create_text_message(str(exc))
            return
        if not options:
            yield self.create_text_message("options is required -- give at least one comma-separated choice.")
            return

        try:
            timeout_seconds = float(tool_parameters.get("timeout_seconds") or MAX_ASK_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            timeout_seconds = MAX_ASK_TIMEOUT_SECONDS
        timeout_seconds = max(0.0, min(timeout_seconds, MAX_ASK_TIMEOUT_SECONDS))

        client, api_key, agent_id = client_and_identity(self.runtime.credentials)

        try:
            blocks, action_map = build_button_card(str(question), options, "opt")
        except ValueError as exc:
            yield self.create_text_message(str(exc))
            return

        try:
            card_response = client.post_card(api_key, str(chat_id), blocks, str(question))
            card_id = extract_card_id(card_response)
            webhook_secret = get_webhook_secret(client, api_key)
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        cursor = get_cursor(client, api_key)
        deadline = time.monotonic() + timeout_seconds

        try:
            result, new_cursor = poll_for_answer(
                client, api_key, webhook_secret, card_id, action_map, cursor, deadline,
            )
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        set_cursor(client, api_key, new_cursor)

        if result["status"] == "answered":
            yield self.create_json_message(result)
            return

        save_pending_ask({
            "card_id": card_id,
            "chat_id": str(chat_id),
            "action_map": action_map,
            "cursor": new_cursor,
            "host": client.host,
            "agent_id": agent_id,
            "created_at": time.time(),
        })
        yield self.create_json_message({"status": "pending", "ask_id": card_id})
