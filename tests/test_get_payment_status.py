from __future__ import annotations

from saltapp.errors import SaltApiError

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.get_payment_status import GetPaymentStatusTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}


def test_get_payment_status_happy_path(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("get_transfer", {"id": "transfer-1", "status": "confirmed"})

    tool = make_tool(GetPaymentStatusTool, CREDENTIALS)
    messages = collect(tool._invoke({"transfer_id": "transfer-1"}))
    assert json_messages(messages)[0] == {"id": "transfer-1", "status": "confirmed"}


def test_get_payment_status_requires_transfer_id(fake_client):
    tool = make_tool(GetPaymentStatusTool, CREDENTIALS)
    messages = collect(tool._invoke({}))
    assert text_messages(messages) == ["transfer_id is required."]


def test_get_payment_status_surfaces_a_not_found_error(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub(
        "get_transfer",
        SaltApiError("GET", "https://saltapp.ai/api/v1/transfers/missing", 404, {"error": "not found"}),
    )

    tool = make_tool(GetPaymentStatusTool, CREDENTIALS)
    messages = collect(tool._invoke({"transfer_id": "missing"}))
    assert "not found" in text_messages(messages)[0]
