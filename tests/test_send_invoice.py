from __future__ import annotations

import json

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.send_invoice import SendInvoiceTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}

VALID_LINE_ITEMS = json.dumps([
    {"name": "Coffee", "qty": 2, "unit_price": "4.50", "subtotal": "9.00"},
])


def test_send_invoice_happy_path(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("create_invoice", lambda **kwargs: {"id": "inv-1", **kwargs})

    tool = make_tool(SendInvoiceTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "receiver_id": "user-2", "wallet_id": "wallet-1",
        "amount": "9.00", "line_items": VALID_LINE_ITEMS,
    }))

    result = json_messages(messages)[0]
    assert result["amount"] == "9.00"
    assert result["line_items"][0]["name"] == "Coffee"


def test_send_invoice_rejects_malformed_json(fake_client):
    fake_client(CREDENTIALS)
    tool = make_tool(SendInvoiceTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "receiver_id": "user-2", "wallet_id": "wallet-1",
        "amount": "9.00", "line_items": "{not valid json",
    }))
    assert "not valid JSON" in text_messages(messages)[0]


def test_send_invoice_rejects_a_subtotal_that_does_not_match_qty_times_unit_price(fake_client):
    fake_client(CREDENTIALS)
    bad_items = json.dumps([{"name": "Coffee", "qty": 2, "unit_price": "4.50", "subtotal": "8.00"}])

    tool = make_tool(SendInvoiceTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "receiver_id": "user-2", "wallet_id": "wallet-1",
        "amount": "8.00", "line_items": bad_items,
    }))
    assert "does not match subtotal" in text_messages(messages)[0]


def test_send_invoice_rejects_subtotals_that_do_not_sum_to_amount(fake_client):
    fake_client(CREDENTIALS)
    tool = make_tool(SendInvoiceTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "receiver_id": "user-2", "wallet_id": "wallet-1",
        "amount": "99.00", "line_items": VALID_LINE_ITEMS,
    }))
    assert "does not match amount" in text_messages(messages)[0]


def test_send_invoice_rejects_a_line_item_missing_required_keys(fake_client):
    fake_client(CREDENTIALS)
    tool = make_tool(SendInvoiceTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "receiver_id": "user-2", "wallet_id": "wallet-1",
        "amount": "9.00", "line_items": json.dumps([{"name": "Coffee", "qty": 2}]),
    }))
    assert "needs" in text_messages(messages)[0]
