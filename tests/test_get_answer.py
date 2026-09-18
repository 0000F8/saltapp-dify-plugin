"""get_answer: resuming a pending ask from its saved cursor, across a
brand-new tool invocation (simulating a different worker process reading
the same file), the unknown-ask_id case, the still-pending-after-resume
case, and the cross-identity safety check."""
from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool
from tests.webhook_helpers import signed_update_row
from tools import _salt_common, get_answer
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


def test_get_answer_resumes_from_the_saved_cursor_and_answers(fake_client):
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

    # Polling resumed from the SAVED cursor (7), not from 0.
    updates_call = next(c for c in client.calls if c[0] == "get_agent_updates")
    assert updates_call[1]["after"] == 7

    # And the pending-ask file is gone now that it's answered.
    assert _salt_common.load_pending_ask("card-123") is None


def test_get_answer_still_pending_rewrites_the_file_with_the_advanced_cursor(fake_client, monkeypatch):
    _salt_common.save_pending_ask(PENDING_RECORD)

    # get_answer is a short "check now" call -- shrink its budget so this
    # test doesn't take RESUME_POLL_BUDGET_SECONDS (8s) of fake polling.
    # RESUME_POLL_BUDGET_SECONDS is imported by name into tools.get_answer,
    # so the module that actually uses it is the one to patch.
    monkeypatch.setattr(get_answer, "RESUME_POLL_BUDGET_SECONDS", 0.03)

    client = fake_client(CREDENTIALS)
    client.stub("who_am_i", {"agent_id": "agent-1", "webhook_secret": WEBHOOK_SECRET})
    client.stub("get_agent_updates", {"updates": [], "cursor": 7})

    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result == {"status": "pending", "ask_id": "card-123"}
    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert record["cursor"] == 7


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
