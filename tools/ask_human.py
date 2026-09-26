import time
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from saltapp.errors import SaltApiError

from tools._salt_common import (
    build_button_card,
    check_for_answer,
    client_and_identity,
    error_text,
    extract_card_id,
    get_chat_members_map,
    parse_options,
    pending_with_retry_hint,
    save_pending_ask,
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

        client, api_key, agent_id = client_and_identity(self.runtime.credentials)

        try:
            blocks, action_map = build_button_card(str(question), options, "opt")
        except ValueError as exc:
            yield self.create_text_message(str(exc))
            return

        try:
            card_response = client.post_card(api_key, str(chat_id), blocks, str(question))
            card_id = extract_card_id(card_response)
            # Who in this chat is human, as of right now -- persisted below
            # so get_answer's one on-demand check never needs a second
            # chat read to tell a human's tap from an agent's (see
            # _salt_common.check_for_answer's docstring).
            humans = get_chat_members_map(client, api_key, str(chat_id))
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        # No waiting, no loop (the owner's explicit no-polling rule): one
        # single check of THIS card's own interaction log (never the
        # shared per-agent socket-mode outbox -- see _salt_common.py's
        # module docstring) covers the rare case where a human somehow
        # already tapped by the time this runs. Almost every real call
        # will find nothing here and fall through to the pending path
        # below -- call get_answer later, or configure the webhook
        # endpoint (see register_webhook) for instant push instead.
        try:
            result, new_cursor = check_for_answer(client, api_key, card_id, action_map, humans, None)
        except SaltApiError as exc:
            if exc.status == 429:
                save_pending_ask({
                    "card_id": card_id,
                    "chat_id": str(chat_id),
                    "action_map": action_map,
                    "humans": humans,
                    "cursor": None,
                    "host": client.host,
                    "agent_id": agent_id,
                    "created_at": time.time(),
                })
                yield self.create_json_message(pending_with_retry_hint(card_id, exc.retry_after))
                return
            yield self.create_text_message(error_text(exc))
            return
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        if result["status"] == "answered":
            yield self.create_json_message(result)
            return

        save_pending_ask({
            "card_id": card_id,
            "chat_id": str(chat_id),
            "action_map": action_map,
            "humans": humans,
            "cursor": new_cursor,
            "host": client.host,
            "agent_id": agent_id,
            "created_at": time.time(),
        })
        yield self.create_json_message({"status": "pending", "ask_id": card_id})
