"""get_answer: resolving instantly from a record a pushed webhook already
answered (zero Salt calls), falling back to exactly one on-demand check
when nothing pushed it yet, the unknown-ask_id case, the still-pending
case, and the cross-identity safety check.

Every test that reaches the fallback path asserts the exact
`get_agent_updates` call count -- get_answer must never loop or sleep
either (the owner's explicit no-polling rule): at most one such call per
invocation.
"""
from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool
from tests.webhook_helpers import signed_update_row
from tools import _salt_common
from tools.get_answer import GetAnswerTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}
WEBHOOK_SECRET = "whsec_test_secret"

PENDING_RECORD = {
    "card_id": "card-123",
    "chat_id": "chat-1",
    "action_map": {"opt_0": "Yes", "opt_1": "No"},
    "cursor": 7,
    "host": "https://saltapp.ai",
    "agent_id": "agent-1",
    "created_at": 1_000_000.0,
}


def test_get_answer_returns_unknown_for_a_missing_ask_id(fake_client):
    fake_client(CREDENTIALS)
    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "no-such-card"}))
    result = json_messages(messages)[0]
    assert result["status"] == "unknown"
    assert "no pending ask" in result["error"]


def test_get_answer_resolves_instantly_from_a_pushed_record_with_zero_salt_calls(fake_client):
    """The webhook endpoint already marked this record "answered" (see
    endpoints/salt_webhook.py / apply_pushed_card_interaction) -- get_answer
    must return it straight from disk, calling NOTHING on the Salt client,
    not even who_am_i for the webhook secret."""
    pushed_record = dict(
        PENDING_RECORD,
        status="answered",
        answer="No",
        action_id="opt_1",
        user={"id": "user-2", "username": "dana", "account_type": "Human"},
    )
    _salt_common.save_pending_ask(pushed_record)

    client = fake_client(CREDENTIALS)
    # Deliberately no stubs at all -- any call the tool makes would raise
    # (FakeSaltClient's _record returns None for an unstubbed method, but
    # who_am_i/get_agent_updates being called at all is what this test
    # forbids).

    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result == {
        "status": "answered",
        "answer": "No",
        "action_id": "opt_1",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
    }
    assert client.calls == []  # zero Salt API calls of any kind

    # And the pending-ask file is gone now that it's been delivered.
    assert _salt_common.load_pending_ask("card-123") is None


def test_get_answer_falls_back_to_exactly_one_on_demand_check_and_answers(fake_client):
    _salt_common.save_pending_ask(PENDING_RECORD)

    human_tap_body = {
        "card_id": "card-123",
        "action_id": "opt_1",
        "chat_id": "chat-1",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
    }
    row = signed_update_row(WEBHOOK_SECRET, human_tap_body, row_id=8)

    client = fake_client(CREDENTIALS)
    client.stub("who_am_i", {"agent_id": "agent-1", "webhook_secret": WEBHOOK_SECRET})
    client.stub("get_agent_updates", {"updates": [row], "cursor": 8})

    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result == {
        "status": "answered",
        "answer": "No",
        "action_id": "opt_1",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
    }

    # Exactly one check, resumed from the SAVED cursor (7), not from 0.
    updates_calls = [c for c in client.calls if c[0] == "get_agent_updates"]
    assert len(updates_calls) == 1
    assert updates_calls[0][1]["after"] == 7

    # And the pending-ask file is gone now that it's answered.
    assert _salt_common.load_pending_ask("card-123") is None


def test_get_answer_still_pending_after_one_check_rewrites_the_file_with_the_advanced_cursor(fake_client):
    _salt_common.save_pending_ask(PENDING_RECORD)

    client = fake_client(CREDENTIALS)
    client.stub("who_am_i", {"agent_id": "agent-1", "webhook_secret": WEBHOOK_SECRET})
    client.stub("get_agent_updates", {"updates": [], "cursor": 9})

    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result == {"status": "pending", "ask_id": "card-123"}
    assert len([c for c in client.calls if c[0] == "get_agent_updates"]) == 1

    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert record["cursor"] == 9


def test_get_answer_refuses_a_pending_ask_from_a_different_agent_id(fake_client):
    other_identity_record = dict(PENDING_RECORD, agent_id="agent-999")
    _salt_common.save_pending_ask(other_identity_record)

    fake_client(CREDENTIALS)
    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result["status"] == "unknown"
    assert "different Salt agent_id" in result["error"]


def test_get_answer_refuses_a_pending_ask_from_a_different_host(fake_client):
    other_host_record = dict(PENDING_RECORD, host="https://staging.saltapp.ai")
    _salt_common.save_pending_ask(other_host_record)

    fake_client(CREDENTIALS)
    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result["status"] == "unknown"
    assert "different Salt host" in result["error"]
