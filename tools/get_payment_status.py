from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import client_and_identity, error_text


class GetPaymentStatusTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        transfer_id = tool_parameters.get("transfer_id")
        if not transfer_id:
            yield self.create_text_message("transfer_id is required.")
            return

        client, api_key, _agent_id = client_and_identity(self.runtime.credentials)
        try:
            result = client.get_transfer(api_key, str(transfer_id))
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        yield self.create_json_message(result)
