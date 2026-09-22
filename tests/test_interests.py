"""interests: set/clear/get, the "mode required when action=set" validation
error, and the "api_key required" guard."""
from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.interests import InterestsTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}
NO_KEY_CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1"}


def test_interests_set_with_keywords_mode(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("set_chat_subscription", {"chat_id": "chat-1", "mode": "keywords", "keywords": ["invoice", "urgent"]})

    tool = make_tool(InterestsTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "action": "set", "mode": "keywords", "keywords": "invoice, urgent",
    }))

    assert json_messages(messages) == [{"chat_id": "chat-1", "mode": "keywords", "keywords": ["invoice", "urgent"]}]
    call = next(c for c in client.calls if c[0] == "set_chat_subscription")
    assert call[1] == {"api_key": "key-1", "chat_id": "chat-1", "mode": "keywords", "keywords": ["invoice", "urgent"]}


def test_interests_set_with_addressed_mode_ignores_keywords(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("set_chat_subscription", {"chat_id": "chat-1", "mode": "addressed"})

    tool = make_tool(InterestsTool, CREDENTIALS)
    collect(tool._invoke({"chat_id": "chat-1", "action": "set", "mode": "addressed", "keywords": "should, be, ignored"}))

    call = next(c for c in client.calls if c[0] == "set_chat_subscription")
    assert call[1]["mode"] == "addressed"
    assert call[1]["keywords"] is None


def test_interests_get(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("get_chat_subscription", {"chat_id": "chat-1", "mode": "addressed"})

    tool = make_tool(InterestsTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "action": "get"}))

    assert json_messages(messages) == [{"chat_id": "chat-1", "mode": "addressed"}]
    assert any(c[0] == "get_chat_subscription" for c in client.calls)
    assert not any(c[0] == "set_chat_subscription" for c in client.calls)


def test_interests_clear(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("clear_chat_subscription", {"chat_id": "chat-1", "mode": "addressed"})

    tool = make_tool(InterestsTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "action": "clear"}))

    assert json_messages(messages) == [{"chat_id": "chat-1", "mode": "addressed"}]
    assert any(c[0] == "clear_chat_subscription" for c in client.calls)


def test_interests_requires_mode_when_action_is_set(fake_client):
    fake_client(CREDENTIALS)
    tool = make_tool(InterestsTool, CREDENTIALS)

    messages = collect(tool._invoke({"chat_id": "chat-1", "action": "set"}))

    texts = text_messages(messages)
    assert len(texts) == 1
    assert 'mode is required when action="set"' in texts[0]


def test_interests_rejects_an_unknown_mode(fake_client):
    fake_client(CREDENTIALS)
    tool = make_tool(InterestsTool, CREDENTIALS)

    messages = collect(tool._invoke({"chat_id": "chat-1", "action": "set", "mode": "everything"}))

    texts = text_messages(messages)
    assert len(texts) == 1
    assert "mode must be one of" in texts[0]


def test_interests_requires_an_api_key(fake_client):
    fake_client(NO_KEY_CREDENTIALS)
    tool = make_tool(InterestsTool, NO_KEY_CREDENTIALS)

    messages = collect(tool._invoke({"chat_id": "chat-1", "action": "get"}))

    texts = text_messages(messages)
    assert len(texts) == 1
    assert "An API key is required for this action" in texts[0]


def test_interests_requires_chat_id(fake_client):
    tool = make_tool(InterestsTool, CREDENTIALS)
    messages = collect(tool._invoke({"action": "get"}))
    assert text_messages(messages) == ["chat_id is required."]
