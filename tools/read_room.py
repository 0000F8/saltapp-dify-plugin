from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import error_text, get_client, read_room


class ReadRoomTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        chat_id = tool_parameters.get("chat_id")
        if not chat_id:
            yield self.create_text_message("chat_id is required.")
            return
        last = tool_parameters.get("last") or None

        # Deliberately not client_and_identity: this tool works with no
        # api_key at all (an anonymous read of a public, unencrypted
        # room), so read straight off credentials rather than go through
        # the stricter identity helper every other tool uses.
        client = get_client(self.runtime.credentials)
        api_key = self.runtime.credentials.get("api_key") or ""

        try:
            result = read_room(client, api_key, str(chat_id), last)
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        yield self.create_json_message(result)
