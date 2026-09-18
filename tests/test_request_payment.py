from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.request_payment import RequestPaymentTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}


def test_request_payment_happy_path(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("request_payment", lambda **kwargs: {"id": "req-1", **kwargs})

    tool = make_tool(RequestPaymentTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "receiver_id": "user-2", "wallet_id": "wallet-1",
        "amount": "4.50", "message": "Coffee",
    }))

    result = json_messages(messages)[0]
    assert result["chat_id"] == "chat-1"
    assert result["receiver_id"] == "user-2"
    assert result["wallet_id"] == "wallet-1"
    assert result["amount"] == "4.50"
    assert result["message"] == "Coffee"


def test_request_payment_requires_every_field(fake_client):
    fake_client(CREDENTIALS)
    tool = make_tool(RequestPaymentTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "amount": "1.00"}))
    text = text_messages(messages)[0]
    assert "receiver_id" in text
    assert "wallet_id" in text


def test_request_payment_surfaces_a_salt_api_error(fake_client):
    from saltapp.errors import SaltApiError

    client = fake_client(CREDENTIALS)
    client.stub(
        "request_payment",
        SaltApiError("POST", "https://saltapp.ai/api/v1/transfer_requests", 422, {"error": "wallet not found"}),
    )

    tool = make_tool(RequestPaymentTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "receiver_id": "user-2", "wallet_id": "bad-wallet", "amount": "1.00",
    }))
    assert "wallet not found" in text_messages(messages)[0]
