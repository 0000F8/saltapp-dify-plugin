from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import build_button_card, client_and_identity, error_text, parse_options


class PostCardTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        chat_id = tool_parameters.get("chat_id")
        text = tool_parameters.get("text")
        if not chat_id:
            yield self.create_text_message("chat_id is required.")
            return
        if not text:
            yield self.create_text_message("text is required.")
            return

        try:
            buttons = parse_options(tool_parameters.get("buttons"))
        except ValueError as exc:
            yield self.create_text_message(str(exc))
            return

        client, api_key, _agent_id = client_and_identity(self.runtime.credentials)
        try:
            blocks, _action_map = build_button_card(str(text), buttons, "btn")
            result = client.post_card(api_key, str(chat_id), blocks, str(text))
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        yield self.create_json_message(result)
