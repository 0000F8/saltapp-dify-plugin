from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.post_card import PostCardTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}


def test_post_card_with_buttons_builds_one_button_per_label(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("post_card", lambda **kwargs: {"id": "card-1", **kwargs})

    tool = make_tool(PostCardTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "text": "Approve this?", "buttons": "Approve, Reject",
    }))

    result = json_messages(messages)[0]
    actions_block = next(b for b in result["blocks"] if b["type"] == "actions")
    labels = [el["label"] for el in actions_block["elements"]]
    assert labels == ["Approve", "Reject"]
    # Fire-and-forget: no polling call of any kind.
    assert not any(c[0] == "get_agent_updates" for c in client.calls)


def test_post_card_without_buttons_is_a_plain_section(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("post_card", lambda **kwargs: {"id": "card-2", **kwargs})

    tool = make_tool(PostCardTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "text": "Just an update."}))

    result = json_messages(messages)[0]
    assert len(result["blocks"]) == 1
    assert result["blocks"][0]["type"] == "section"


def test_post_card_requires_chat_id_and_text(fake_client):
    tool = make_tool(PostCardTool, CREDENTIALS)
    assert text_messages(collect(tool._invoke({"text": "hi"}))) == ["chat_id is required."]
    assert text_messages(collect(tool._invoke({"chat_id": "chat-1"}))) == ["text is required."]
