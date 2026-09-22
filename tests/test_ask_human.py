"""ask_human: the one-shot answered-instantly path, the agent-tap-is-ignored
rule (within that same single check), and the pending -> file-persisted
path (get_answer's resume is covered in test_get_answer.py).

Every test here asserts the exact `get_agent_updates` call count -- ask_human
must NEVER loop or sleep (the owner's explicit no-polling rule): it makes
at most one such call per invocation, period.
"""
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


def test_ask_human_returns_immediately_on_a_human_tap_already_present(fake_client):
    """Covers the rare "already answered instantly" case: the one-shot
    check made right after posting the card happens to find the tap on
    its single call."""
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
    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "Pick one", "options": "Yes, No"}))

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

    # Exactly one check -- no loop.
    assert len([c for c in client.calls if c[0] == "get_agent_updates"]) == 1
    # And nothing was persisted -- it was answered on that one check.
    assert _salt_common.load_pending_ask("card-123") is None


def test_ask_human_ignores_a_tap_from_an_agent_within_the_same_single_check(fake_client):
    """An agent's tap and a human's tap both arrive in the SAME
    get_agent_updates response (one call, one page of rows) -- the agent
    row must be skipped and the human row must still resolve the ask,
    without ever making a second call."""
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
    rows = [
        signed_update_row(WEBHOOK_SECRET, agent_tap_body, row_id=1),
        signed_update_row(WEBHOOK_SECRET, human_tap_body, row_id=2),
    ]
    client = _setup(fake_client, lambda **kw: {"updates": rows, "cursor": 2})

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "Pick one", "options": "Yes, No"}))

    results = json_messages(messages)
    assert results[0]["status"] == "answered"
    assert results[0]["answer"] == "Yes"
    assert results[0]["user"]["account_type"] == "Human"

    get_updates_calls = [c for c in client.calls if c[0] == "get_agent_updates"]
    assert len(get_updates_calls) == 1, "ask_human must never loop or sleep -- one check, period"
    assert get_updates_calls[0][1]["after"] == 0


def test_ask_human_persists_a_pending_ask_when_the_one_shot_check_finds_nothing(fake_client):
    client = _setup(fake_client, lambda **kw: {"updates": [], "cursor": 5})

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "Pick one", "options": "Yes, No"}))

    results = json_messages(messages)
    assert results == [{"status": "pending", "ask_id": "card-123"}]

    assert len([c for c in client.calls if c[0] == "get_agent_updates"]) == 1

    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert record["chat_id"] == "chat-1"
    assert record["action_map"] == {"opt_0": "Yes", "opt_1": "No"}
    assert record["agent_id"] == "agent-1"
    assert record["host"] == "https://saltapp.ai"
    assert record["cursor"] == 5


def test_ask_human_has_no_timeout_seconds_parameter(fake_client):
    """A behavior change from the previous synchronous-wait design: there
    is nothing left to wait for, so a caller passing timeout_seconds is
    simply ignored (Dify drops parameters the yaml schema doesn't
    declare) rather than clamped or validated -- confirmed here by
    checking a huge value doesn't change anything about the call count or
    outcome."""
    client = _setup(fake_client, lambda **kw: {"updates": [], "cursor": 0})

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "question": "Pick one", "options": "Yes", "timeout_seconds": 999999,
    }))

    assert json_messages(messages) == [{"status": "pending", "ask_id": "card-123"}]
    assert len([c for c in client.calls if c[0] == "get_agent_updates"]) == 1


def test_ask_human_requires_chat_id_question_and_options(fake_client):
    tool = make_tool(AskHumanTool, CREDENTIALS)

    messages = collect(tool._invoke({"question": "q", "options": "Yes"}))
    assert text_messages(messages) == ["chat_id is required."]

    messages = collect(tool._invoke({"chat_id": "chat-1", "options": "Yes"}))
    assert text_messages(messages) == ["question is required."]

    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "q"}))
    assert text_messages(messages) == ["options is required -- give at least one comma-separated choice."]
