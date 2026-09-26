"""get_answer: resolving instantly from a record a pushed webhook already
answered (zero Salt calls), falling back to exactly one on-demand check
(against this card's own interaction log, never the old shared per-agent
outbox) when nothing pushed it yet, the 429 -> pending-with-hint path, the
unknown-ask_id case, the still-pending case, and the cross-identity
safety check.

Every test that reaches the fallback path asserts the exact `get_card`
call count -- get_answer must never loop or sleep either (the owner's
explicit no-polling rule): at most one such call per invocation.
"""
from __future__ import annotations

from saltapp.errors import SaltApiError

from tests.fakes import collect, json_messages, make_tool
from tools import _salt_common
from tools.get_answer import GetAnswerTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}

HUMANS = {
    "user-2": {"id": "user-2", "username": "dana", "display_name": "Dana", "account_type": "Human"},
}

PENDING_RECORD = {
    "card_id": "card-123",
    "chat_id": "chat-1",
    "action_map": {"opt_0": "Yes", "opt_1": "No"},
    "humans": HUMANS,
    "cursor": "ia-7",
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
    not even a card read."""
    pushed_record = dict(
        PENDING_RECORD,
        status="answered",
        answer="No",
        action_id="opt_1",
        user={"id": "user-2", "username": "dana", "account_type": "Human"},
    )
    _salt_common.save_pending_ask(pushed_record)

    client = fake_client(CREDENTIALS)
    # Deliberately no stubs at all -- `client.calls == []` below is what
    # actually proves no Salt call happened; an unstubbed get_card would
    # anyway blow up downstream (a None response has no .get), so this
    # test would fail loudly, not quietly, if the pushed-answer shortcut
    # ever regressed into calling get_card.

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

    client = fake_client(CREDENTIALS)
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-8", "user_id": "user-2", "action_id": "opt_1", "value": None, "created_at": "2026-09-26T00:00:08Z"},
        ],
    })

    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result == {
        "status": "answered",
        "answer": "No",
        "action_id": "opt_1",
        "user": HUMANS["user-2"],
    }

    # Exactly one check, resumed from the SAVED cursor ("ia-7"), not from
    # scratch.
    get_card_calls = [c for c in client.calls if c[0] == "get_card"]
    assert len(get_card_calls) == 1
    assert get_card_calls[0][1]["after"] == "ia-7"
    assert not any(c[0] == "get_agent_updates" for c in client.calls)

    # And the pending-ask file is gone now that it's answered.
    assert _salt_common.load_pending_ask("card-123") is None


def test_get_answer_still_pending_after_one_check_advances_the_cursor_past_a_non_matching_tap(fake_client):
    """A tap arrived (an agent's, on a real button) but doesn't resolve
    the ask -- the file is rewritten with the ADVANCED cursor anyway, so
    a later check never re-reads that same row."""
    _salt_common.save_pending_ask(PENDING_RECORD)

    client = fake_client(CREDENTIALS)
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-9", "user_id": "agent-9", "action_id": "opt_0", "value": None, "created_at": "2026-09-26T00:00:09Z"},
        ],
    })

    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result == {"status": "pending", "ask_id": "card-123"}
    assert len([c for c in client.calls if c[0] == "get_card"]) == 1

    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert record["cursor"] == "ia-9"


def test_get_answer_a_garbage_saved_cursor_still_answers(fake_client):
    """salt-api's `CardInteraction.page` fails OPEN on an unrecognised
    `after` -- a corrupted/garbage saved cursor must not stop get_answer
    from resolving a real tap once the (simulated) server hands the full
    interaction list back anyway."""
    garbage_record = dict(PENDING_RECORD, cursor="not-a-real-interaction-id")
    _salt_common.save_pending_ask(garbage_record)

    client = fake_client(CREDENTIALS)
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-1", "user_id": "user-2", "action_id": "opt_0", "value": None, "created_at": "2026-09-26T00:00:01Z"},
        ],
    })

    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result["status"] == "answered"
    assert result["answer"] == "Yes"


def test_get_answer_rate_limited_returns_pending_with_a_retry_hint_without_advancing_the_saved_cursor(fake_client):
    _salt_common.save_pending_ask(PENDING_RECORD)

    client = fake_client(CREDENTIALS)
    client.stub("get_card", SaltApiError(
        "GET", "https://saltapp.ai/api/v1/cards/card-123", 429, {"error": "rate limited"}, retry_after=3,
    ))

    tool = make_tool(GetAnswerTool, CREDENTIALS)
    messages = collect(tool._invoke({"ask_id": "card-123"}))
    result = json_messages(messages)[0]

    assert result["status"] == "pending"
    assert result["ask_id"] == "card-123"
    assert result["retry_after_seconds"] == 3
    assert "3" in result["hint"]
    assert len([c for c in client.calls if c[0] == "get_card"]) == 1

    # The saved record is untouched -- still there, still resumable.
    record = _salt_common.load_pending_ask("card-123")
    assert record == PENDING_RECORD


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
