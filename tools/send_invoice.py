from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools._salt_common import client_and_identity, error_text, parse_line_items


class SendInvoiceTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        chat_id = tool_parameters.get("chat_id")
        receiver_id = tool_parameters.get("receiver_id")
        wallet_id = tool_parameters.get("wallet_id")
        amount = tool_parameters.get("amount")
        message = tool_parameters.get("message") or None
        due_at = tool_parameters.get("due_at") or None

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

        try:
            line_items = parse_line_items(tool_parameters.get("line_items"))
        except ValueError as exc:
            yield self.create_text_message(str(exc))
            return

        try:
            subtotal_sum = 0.0
            for item in line_items:
                qty = float(item["qty"])
                unit_price = float(item["unit_price"])
                subtotal = float(item["subtotal"])
                subtotal_sum += subtotal
                if abs(qty * unit_price - subtotal) > 1e-6:
                    yield self.create_text_message(
                        f"line item {item.get('name')!r}: qty * unit_price ({qty * unit_price}) "
                        f"does not match subtotal ({subtotal})."
                    )
                    return
            if abs(subtotal_sum - float(amount)) > 1e-6:
                yield self.create_text_message(
                    f"line_items subtotals sum to {subtotal_sum}, which does not match amount ({amount})."
                )
                return
        except (TypeError, ValueError) as exc:
            yield self.create_text_message(f"line_items contains a non-numeric qty/unit_price/subtotal: {exc}")
            return

        client, api_key, _agent_id = client_and_identity(self.runtime.credentials)
        try:
            result = client.create_invoice(
                api_key,
                chat_id=str(chat_id),
                receiver_id=str(receiver_id),
                wallet_id=str(wallet_id),
                amount=str(amount),
                line_items=line_items,
                message=str(message) if message else None,
                due_at=str(due_at) if due_at else None,
            )
        except Exception as exc:  # noqa: BLE001
            yield self.create_text_message(error_text(exc))
            return

        yield self.create_json_message(result)
