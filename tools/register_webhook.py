from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import client_and_identity, error_text


class RegisterWebhookTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        webhook_url = tool_parameters.get("webhook_url")
        if not webhook_url:
            yield self.create_text_message("webhook_url is required.")
            return

        client, api_key, _agent_id = client_and_identity(self.runtime.credentials)
        if not api_key:
            yield self.create_text_message(
                "An API key is required for this action; configure it in the Salt provider credentials."
            )
            return

        try:
            result = client.set_callback(api_key, str(webhook_url))
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        yield self.create_json_message(result)
