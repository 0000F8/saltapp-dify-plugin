"""register_webhook: a normal call, the missing-url guard, and the
missing-api-key guard."""
from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.register_webhook import RegisterWebhookTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}
NO_KEY_CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1"}


def test_register_webhook_sets_the_callback(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("set_callback", {"webhook": "https://dify.example/e/hook-1/webhook"})

    tool = make_tool(RegisterWebhookTool, CREDENTIALS)
    messages = collect(tool._invoke({"webhook_url": "https://dify.example/e/hook-1/webhook"}))

    assert json_messages(messages) == [{"webhook": "https://dify.example/e/hook-1/webhook"}]
    call = next(c for c in client.calls if c[0] == "set_callback")
    assert call[1] == {"api_key": "key-1", "webhook": "https://dify.example/e/hook-1/webhook"}


def test_register_webhook_requires_a_webhook_url(fake_client):
    tool = make_tool(RegisterWebhookTool, CREDENTIALS)
    messages = collect(tool._invoke({}))
    assert text_messages(messages) == ["webhook_url is required."]


def test_register_webhook_requires_an_api_key(fake_client):
    fake_client(NO_KEY_CREDENTIALS)
    tool = make_tool(RegisterWebhookTool, NO_KEY_CREDENTIALS)

    messages = collect(tool._invoke({"webhook_url": "https://dify.example/e/hook-1/webhook"}))

    texts = text_messages(messages)
    assert len(texts) == 1
    assert "An API key is required for this action" in texts[0]
