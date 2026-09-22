from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import (
    check_for_answer,
    client_and_identity,
    delete_pending_ask,
    error_text,
    get_webhook_secret,
    load_pending_ask,
    save_pending_ask,
)


class GetAnswerTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        ask_id = tool_parameters.get("ask_id")
        if not ask_id:
            yield self.create_text_message("ask_id is required.")
            return

        record = load_pending_ask(str(ask_id))
        if record is None:
            yield self.create_json_message({
                "status": "unknown",
                "error": "no pending ask with that id (never existed, already answered, or expired)",
            })
            return

        client, api_key, agent_id = client_and_identity(self.runtime.credentials)

        record_agent_id = str(record.get("agent_id") or "")
        record_host = str(record.get("host") or "").rstrip("/")
        if record_agent_id and record_agent_id.lower() != agent_id.lower():
            yield self.create_json_message({
                "status": "unknown",
                "error": "this pending ask belongs to a different Salt agent_id than the current credentials.",
            })
            return
        if record_host and record_host != client.host:
            yield self.create_json_message({
                "status": "unknown",
                "error": "this pending ask was created against a different Salt host than the current credentials.",
            })
            return

        # A webhook push (see endpoints/salt_webhook.py) may already have
        # answered this -- if so, resolve instantly with ZERO Salt calls,
        # not even who_am_i for the webhook secret.
        if record.get("status") == "answered":
            delete_pending_ask(str(ask_id))
            yield self.create_json_message({
                "status": "answered",
                "answer": record.get("answer"),
                "action_id": record.get("action_id"),
                "user": record.get("user"),
            })
            return

        # No push arrived (or none is configured) -- fall back to exactly
        # ONE on-demand check, never a loop (the owner's no-polling rule).
        try:
            webhook_secret = get_webhook_secret(client, api_key)
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        try:
            result, new_cursor = check_for_answer(
                client, api_key, webhook_secret,
                record["card_id"], record.get("action_map") or {}, record.get("cursor", 0),
            )
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        if result["status"] == "answered":
            delete_pending_ask(str(ask_id))
            yield self.create_json_message(result)
            return

        record["cursor"] = new_cursor
        save_pending_ask(record)
        yield self.create_json_message({"status": "pending", "ask_id": str(ask_id)})
