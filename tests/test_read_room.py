"""read_room: a normal call, a call with last=, and the anonymous path
(blank/missing api_key credential must still work, sending api_key=""
rather than raising or sending a literal "None")."""
from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.read_room import ReadRoomTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}
ANONYMOUS_CREDENTIALS = {"host": "https://saltapp.ai"}


def test_read_room_reads_the_chat(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("get_chat", {"session": {"encrypted": False}, "messages": [{"id": "m1"}]})

    tool = make_tool(ReadRoomTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1"}))

    results = json_messages(messages)
    assert results == [{"session": {"encrypted": False}, "messages": [{"id": "m1"}]}]

    call = next(c for c in client.calls if c[0] == "get_chat")
    assert call[1] == {"api_key": "key-1", "chat_id": "chat-1", "last": None}


def test_read_room_passes_last_through_as_a_catch_up_cursor(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("get_chat", {"session": {}, "messages": []})

    tool = make_tool(ReadRoomTool, CREDENTIALS)
    collect(tool._invoke({"chat_id": "chat-1", "last": "42"}))

    call = next(c for c in client.calls if c[0] == "get_chat")
    assert call[1]["last"] == "42"


def test_read_room_works_with_no_api_key_credential_at_all(fake_client):
    """The provider credentials carry no api_key (a read-only, host-only
    setup) -- the fake client must have received api_key="", never a
    KeyError and never the literal string "None"."""
    client = fake_client(ANONYMOUS_CREDENTIALS)
    client.stub("get_chat", {"session": {"encrypted": False}, "messages": []})

    tool = make_tool(ReadRoomTool, ANONYMOUS_CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1"}))

    assert json_messages(messages) == [{"session": {"encrypted": False}, "messages": []}]

    call = next(c for c in client.calls if c[0] == "get_chat")
    assert call[1]["api_key"] == ""


def test_read_room_requires_chat_id():
    tool = make_tool(ReadRoomTool, CREDENTIALS)

    messages = collect(tool._invoke({}))
    assert text_messages(messages) == ["chat_id is required."]
