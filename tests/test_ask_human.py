"""ask_human: the one-shot answered-instantly path, the agent-tap-is-ignored
rule (within that same single check), the pending -> file-persisted path
(get_answer's resume is covered in test_get_answer.py), and the 429 ->
pending-with-hint path. The matching semantics themselves (wrong human,
wrong action, cursor threading, a garbage cursor) are pinned directly
against `_salt_common.check_for_answer` in test_check_for_answer.py.

Every test here asserts the exact `get_card` call count -- ask_human must
NEVER loop or sleep (the owner's explicit no-polling rule): it makes at
most one such call per invocation, period. And `get_card` is read via
`GET /api/v1/cards/:id`, never the old shared per-agent socket-mode
outbox (`GET /api/v1/agent/updates`) -- `FakeSaltClient` has no method by
that name any more, so a regression that reintroduced it would fail
loudly (AttributeError), not silently pass.
"""
from __future__ import annotations

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools import _salt_common
from tools.ask_human import AskHumanTool
from saltapp.errors import SaltApiError

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}

# A realistic `get_chat` response: `resource_id`/`resource.id` is where a
# real card id actually lives (see extract_card_id's docstring) -- there
# is NO top-level `id` on a real `POST /api/v1/cards` response. Every
# test's `post_card` stub below models that real shape on purpose (a
# 2026-09-26 review found this plugin's own fakes stubbing a fictional
# top-level `id`, hiding a real bug in `extract_card_id`).
POST_CARD_RESPONSE = {"message_id": "msg-1", "resource_id": "card-123", "resource": {"id": "card-123"}}

CHAT_RESPONSE = {
    "session": {
        "users": [
            {"id": "agent-1", "username": "salt_agent", "display_name": "Salt Agent", "account_type": "Agent"},
            {"id": "user-2", "username": "dana", "display_name": "Dana", "account_type": "Human"},
            {"id": "agent-9", "username": "some_bot", "display_name": "Some Bot", "account_type": "Agent"},
        ],
    },
}

EXPECTED_HUMANS = {
    "user-2": {"id": "user-2", "username": "dana", "display_name": "Dana", "account_type": "Human"},
}


def _setup(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("post_card", POST_CARD_RESPONSE)
    client.stub("get_chat", CHAT_RESPONSE)
    return client


def test_ask_human_returns_immediately_on_a_human_tap_already_present(fake_client):
    """Covers the rare "already answered instantly" case: the one-shot
    check made right after posting the card happens to find the tap on
    its single call."""
    client = _setup(fake_client)
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-1", "user_id": "user-2", "action_id": "opt_0", "value": None, "created_at": "2026-09-26T00:00:01Z"},
        ],
    })

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "Pick one", "options": "Yes, No"}))

    results = json_messages(messages)
    assert len(results) == 1
    assert results[0] == {
        "status": "answered",
        "answer": "Yes",
        "action_id": "opt_0",
        "user": EXPECTED_HUMANS["user-2"],
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

    # Exactly one check -- no loop -- and it read the CARD, never the
    # shared per-agent outbox.
    assert len([c for c in client.calls if c[0] == "get_card"]) == 1
    assert not any(c[0] == "get_agent_updates" for c in client.calls)
    # And nothing was persisted -- it was answered on that one check.
    assert _salt_common.load_pending_ask("card-123") is None


def test_ask_human_ignores_a_tap_from_an_agent_within_the_same_single_check(fake_client):
    """An agent's tap and a human's tap both arrive in the SAME
    get_card response (one call, one page of rows) -- the agent row
    must be skipped and the human row must still resolve the ask,
    without ever making a second call."""
    client = _setup(fake_client)
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-2", "user_id": "agent-9", "action_id": "opt_1", "value": None, "created_at": "2026-09-26T00:00:02Z"},
            {"id": "ia-1", "user_id": "user-2", "action_id": "opt_0", "value": None, "created_at": "2026-09-26T00:00:01Z"},
        ],
    })

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "Pick one", "options": "Yes, No"}))

    results = json_messages(messages)
    assert results[0]["status"] == "answered"
    assert results[0]["answer"] == "Yes"
    assert results[0]["user"]["account_type"] == "Human"

    get_card_calls = [c for c in client.calls if c[0] == "get_card"]
    assert len(get_card_calls) == 1, "ask_human must never loop or sleep -- one check, period"
    assert get_card_calls[0][1]["after"] is None


def test_ask_human_persists_a_pending_ask_when_the_one_shot_check_finds_nothing(fake_client):
    client = _setup(fake_client)
    client.stub("get_card", {"interactions": []})

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "Pick one", "options": "Yes, No"}))

    results = json_messages(messages)
    assert results == [{"status": "pending", "ask_id": "card-123"}]

    assert len([c for c in client.calls if c[0] == "get_card"]) == 1

    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert record["chat_id"] == "chat-1"
    assert record["action_map"] == {"opt_0": "Yes", "opt_1": "No"}
    assert record["humans"] == EXPECTED_HUMANS
    assert record["agent_id"] == "agent-1"
    assert record["host"] == "https://saltapp.ai"
    assert record["cursor"] is None


def test_ask_human_rate_limited_returns_pending_with_a_retry_hint_and_persists(fake_client):
    """A 429 on the one-shot check is never retried and never raised as a
    hard error -- it's still a "pending" result, carrying a hint, and the
    card (already posted) is still recorded so get_answer can pick it up
    later."""
    client = _setup(fake_client)
    client.stub("get_card", SaltApiError(
        "GET", "https://saltapp.ai/api/v1/cards/card-123", 429, {"error": "rate limited"}, retry_after=5,
    ))

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "Pick one", "options": "Yes, No"}))

    results = json_messages(messages)
    assert results[0]["status"] == "pending"
    assert results[0]["ask_id"] == "card-123"
    assert results[0]["retry_after_seconds"] == 5
    assert "5" in results[0]["hint"]

    assert len([c for c in client.calls if c[0] == "get_card"]) == 1

    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert record["humans"] == EXPECTED_HUMANS
    assert record["cursor"] is None


def test_ask_human_has_no_timeout_seconds_parameter(fake_client):
    """A behavior change from the previous synchronous-wait design: there
    is nothing left to wait for, so a caller passing timeout_seconds is
    simply ignored (Dify drops parameters the yaml schema doesn't
    declare) rather than clamped or validated -- confirmed here by
    checking a huge value doesn't change anything about the call count or
    outcome."""
    client = _setup(fake_client)
    client.stub("get_card", {"interactions": []})

    tool = make_tool(AskHumanTool, CREDENTIALS)
    messages = collect(tool._invoke({
        "chat_id": "chat-1", "question": "Pick one", "options": "Yes", "timeout_seconds": 999999,
    }))

    assert json_messages(messages) == [{"status": "pending", "ask_id": "card-123"}]
    assert len([c for c in client.calls if c[0] == "get_card"]) == 1


def test_ask_human_requires_chat_id_question_and_options(fake_client):
    tool = make_tool(AskHumanTool, CREDENTIALS)

    messages = collect(tool._invoke({"question": "q", "options": "Yes"}))
    assert text_messages(messages) == ["chat_id is required."]

    messages = collect(tool._invoke({"chat_id": "chat-1", "options": "Yes"}))
    assert text_messages(messages) == ["question is required."]

    messages = collect(tool._invoke({"chat_id": "chat-1", "question": "q"}))
    assert text_messages(messages) == ["options is required -- give at least one comma-separated choice."]
