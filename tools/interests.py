from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import client_and_identity, error_text, parse_options

_ACTIONS = {"set", "get", "clear"}
_MODES = {"addressed", "keywords", "all"}


class InterestsTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        chat_id = tool_parameters.get("chat_id")
        if not chat_id:
            yield self.create_text_message("chat_id is required.")
            return

        action = str(tool_parameters.get("action") or "set").strip().lower()
        if action not in _ACTIONS:
            yield self.create_text_message('action must be one of "set", "get", "clear".')
            return

        mode = tool_parameters.get("mode")
        mode = str(mode).strip().lower() if mode else None
        # Dify's classic tool parameter schema has no conditional-required
        # (a parameter can't be "required only when another field has a
        # given value"), so this is validated here instead.
        if action == "set" and not mode:
            yield self.create_text_message(
                'mode is required when action="set" (one of "addressed", "keywords", "all").'
            )
            return
        if mode and mode not in _MODES:
            yield self.create_text_message('mode must be one of "addressed", "keywords", "all".')
            return

        try:
            keywords = parse_options(tool_parameters.get("keywords"))
        except ValueError as exc:
            yield self.create_text_message(str(exc))
            return

        client, api_key, _agent_id = client_and_identity(self.runtime.credentials)
        if not api_key:
            yield self.create_text_message(
                "An API key is required for this action; configure it in the Salt provider credentials."
            )
            return

        try:
            if action == "get":
                result = client.get_chat_subscription(api_key, str(chat_id))
            elif action == "clear":
                result = client.clear_chat_subscription(api_key, str(chat_id))
            else:  # "set"
                result = client.set_chat_subscription(
                    api_key, str(chat_id), str(mode),
                    keywords=keywords if mode == "keywords" else None,
                )
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        yield self.create_json_message(result)
