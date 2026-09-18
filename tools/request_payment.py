from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import client_and_identity, error_text


class RequestPaymentTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        chat_id = tool_parameters.get("chat_id")
        receiver_id = tool_parameters.get("receiver_id")
        wallet_id = tool_parameters.get("wallet_id")
        amount = tool_parameters.get("amount")
        message = tool_parameters.get("message") or None

        missing = [
            name
            for name, value in (
                ("chat_id", chat_id),
                ("receiver_id", receiver_id),
                ("wallet_id", wallet_id),
                ("amount", amount),
            )
            if not value
        ]
        if missing:
            verb = "is" if len(missing) == 1 else "are"
            yield self.create_text_message(f"{', '.join(missing)} {verb} required.")
            return

        client, api_key, _agent_id = client_and_identity(self.runtime.credentials)
        try:
            result = client.request_payment(
                api_key,
                chat_id=str(chat_id),
                receiver_id=str(receiver_id),
                wallet_id=str(wallet_id),
                amount=str(amount),
                message=str(message) if message else None,
            )
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        yield self.create_json_message(result)
