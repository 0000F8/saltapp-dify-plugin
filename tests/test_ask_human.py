"""ask_human: the immediate-answer path, the agent-tap-is-ignored rule,
and the pending -> file-persisted path (get_answer's resume is covered in
test_get_answer.py)."""
from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool, text_messages
from tests.webhook_helpers import signed_update_row
from tools import _salt_common
from tools.ask_human import AskHumanTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}
WEBHOOK_SECRET = "whsec_test_secret"


def _setup(fake_client, updates_stub):
    client = fake_client(CREDENTIALS)
    client.stub("who_am_i", {"agent_id": "agent-1", "webhook_secret": WEBHOOK_SECRET})
    client.stub("post_card", {"id": "card-123"})
    client.stub("get_agent_updates", updates_stub)
    return client


def test_ask_human_returns_immediately_on_a_human_tap(fake_client):
    human_tap_body = {
        "card_id": "card-123",
        "action_id": "opt_0",
        "chat_id": "chat-1",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
        "state": {"blocks": []},
    }
    row = signed_update_row(WEBHOOK_SECRET, human_tap_body, row_id=1)
    client = _setup(fake_client, lambda **kw: {"updates": [row], "cursor": 1})

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "question": "Pick one", "options": "Yes, No", "timeout_seconds": 5,
    }))

    results = json_messages(messages)
    assert len(results) == 1
    assert results[0] == {
        "status": "answered",
        "answer": "Yes",
        "action_id": "opt_0",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
    }

    # The card really carried one button per option.
    post_card_call = next(c for c in client.calls if c[0] == "post_card")[1]
    action_ids = [
        b["action_id"]
        for block in post_card_call["blocks"]
        if block.get("type") == "actions"
        for b in block["elements"]
    ]
    assert action_ids == ["opt_0", "opt_1"]

    # And nothing was persisted -- it was answered before ever giving up.
    assert _salt_common.load_pending_ask("card-123") is None


def test_ask_human_ignores_a_tap_from_an_agent_then_answers_on_the_human_one(fake_client):
    agent_tap_body = {
        "card_id": "card-123",
        "action_id": "opt_1",
        "chat_id": "chat-1",
        "user": {"id": "agent-9", "username": "some_bot", "account_type": "Agent"},
    }
    human_tap_body = {
        "card_id": "card-123",
        "action_id": "opt_0",
        "chat_id": "chat-1",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
    }
    rows = iter([
        {"updates": [signed_update_row(WEBHOOK_SECRET, agent_tap_body, row_id=1)], "cursor": 1},
        {"updates": [signed_update_row(WEBHOOK_SECRET, human_tap_body, row_id=2)], "cursor": 2},
    ])
    client = _setup(fake_client, lambda **kw: next(rows))

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "question": "Pick one", "options": "Yes, No", "timeout_seconds": 5,
    }))

    results = json_messages(messages)
    assert results[0]["status"] == "answered"
    assert results[0]["answer"] == "Yes"
    assert results[0]["user"]["account_type"] == "Human"

    get_updates_calls = [c for c in client.calls if c[0] == "get_agent_updates"]
    assert len(get_updates_calls) == 2, "the agent's tap must not resolve the ask -- polling must continue"
    # The cursor must have advanced past the (ignored) agent row too.
    assert get_updates_calls[1][1]["after"] == 1


def test_ask_human_persists_a_pending_ask_when_nobody_answers_in_time(fake_client):
    client = _setup(fake_client, lambda **kw: {"updates": [], "cursor": 0})

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "question": "Pick one", "options": "Yes, No", "timeout_seconds": 0.05,
    }))

    results = json_messages(messages)
    assert results == [{"status": "pending", "ask_id": "card-123"}]

    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert record["chat_id"] == "chat-1"
    assert record["action_map"] == {"opt_0": "Yes", "opt_1": "No"}
    assert record["agent_id"] == "agent-1"
    assert record["host"] == "https://saltapp.ai"


def test_ask_human_caps_the_timeout_at_fifty_seconds(fake_client, monkeypatch):
    """Passing a huge timeout_seconds must still give up at
    MAX_ASK_TIMEOUT_SECONDS (50), never wait for the value the caller
    asked for. Uses a fake clock (real time.monotonic/time.sleep,
    monkeypatched) so this proves the actual clamped deadline math
    without the test itself taking anywhere near 50 real seconds."""
    import time as time_module

    _setup(fake_client, lambda **kw: {"updates": [], "cursor": 0})

    fake_now = [1_000_000.0]

    def fake_monotonic():
        return fake_now[0]

    def fake_sleep(seconds):
        fake_now[0] += seconds

    monkeypatch.setattr(time_module, "monotonic", fake_monotonic)
    monkeypatch.setattr(time_module, "sleep", fake_sleep)

    tool = make_tool(AskHumanTool, CREDENTIALS)
    start = fake_now[0]
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "question": "Pick one", "options": "Yes",
        "timeout_seconds": 999999,
    }))
    elapsed = fake_now[0] - start

    assert 50 <= elapsed < 51, f"expected the wait clamped to ~50s of (fake) time, got {elapsed}"
    assert json_messages(messages) == [{"status": "pending", "ask_id": "card-123"}]
