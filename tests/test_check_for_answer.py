"""Direct unit tests of `_salt_common.check_for_answer`/`get_card`.

These exercise the CORE of the 2026-09-26 fix in isolation from any
particular tool: `check_for_answer` must read `GET /api/v1/cards/:id`
(via `FakeSaltClient._request`, which stands in for the real
`SaltClient._request` -- see that fake's docstring for why), never the
old shared per-agent socket-mode outbox, and must make exactly ONE such
call per invocation (no retry, no sleep).

`tools/ask_human.py`/`tools/get_answer.py`'s own test files cover the
tool-level integration (persistence, cross-identity checks, the
one-call-per-invocation contract end to end); this file is where the
matching semantics themselves -- which tap counts, cursor threading,
429 handling -- are pinned precisely.
"""
from __future__ import annotations

from saltapp.errors import SaltApiError

from tools import _salt_common

CARD_ID = "card-123"
ACTION_MAP = {"opt_0": "Yes", "opt_1": "No"}
HUMANS = {
    "user-2": {"id": "user-2", "username": "dana", "display_name": "Dana", "account_type": "Human"},
}


def test_tap_answered(fake_client):
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_card", {
        "id": CARD_ID,
        "interactions": [
            {"id": "ia-1", "user_id": "user-2", "action_id": "opt_0", "value": None, "created_at": "2026-09-26T00:00:01Z"},
        ],
    })

    result, new_after = _salt_common.check_for_answer(client, "key-1", CARD_ID, ACTION_MAP, HUMANS, None)

    assert result == {"status": "answered", "answer": "Yes", "action_id": "opt_0", "user": HUMANS["user-2"]}
    assert new_after == "ia-1"
    get_card_calls = [c for c in client.calls if c[0] == "get_card"]
    assert len(get_card_calls) == 1, "check_for_answer must never loop or sleep -- one call, period"
    assert get_card_calls[0][1] == {"api_key": "key-1", "card_id": CARD_ID, "after": None}


def test_wrong_human_ignored_an_agents_tap_never_resolves_the_ask(fake_client):
    """The newest interaction is an AGENT's tap on a real button -- it
    must be skipped, and since it's the only interaction, the ask stays
    pending, with the cursor still advanced past it (so a resumed check
    never re-reads it)."""
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-2", "user_id": "agent-9", "action_id": "opt_1", "value": None, "created_at": "2026-09-26T00:00:02Z"},
        ],
    })

    result, new_after = _salt_common.check_for_answer(client, "key-1", CARD_ID, ACTION_MAP, HUMANS, None)

    assert result == {"status": "pending"}
    assert new_after == "ia-2"


def test_a_humans_tap_resolves_even_with_an_agents_tap_ahead_of_it_in_the_same_check(fake_client):
    """Newest-first: the agent's tap (row 0) is skipped, the human's tap
    (row 1) resolves -- both read in the SAME single call."""
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-2", "user_id": "agent-9", "action_id": "opt_1", "value": None, "created_at": "2026-09-26T00:00:02Z"},
            {"id": "ia-1", "user_id": "user-2", "action_id": "opt_0", "value": None, "created_at": "2026-09-26T00:00:01Z"},
        ],
    })

    result, new_after = _salt_common.check_for_answer(client, "key-1", CARD_ID, ACTION_MAP, HUMANS, None)

    assert result["status"] == "answered"
    assert result["answer"] == "Yes"
    # The cursor advances to the NEWEST row seen (row 0), not the matched row.
    assert new_after == "ia-2"
    assert len([c for c in client.calls if c[0] == "get_card"]) == 1


def test_wrong_action_ignored(fake_client):
    """An interaction whose action_id isn't one of THIS ask's own buttons
    (e.g. a stray tap the card carries for some other reason) must never
    resolve the ask, even when the tapper is a known human."""
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-1", "user_id": "user-2", "action_id": "some_other_action", "value": None, "created_at": "2026-09-26T00:00:01Z"},
        ],
    })

    result, new_after = _salt_common.check_for_answer(client, "key-1", CARD_ID, ACTION_MAP, HUMANS, None)

    assert result == {"status": "pending"}
    assert new_after == "ia-1"


def test_after_is_threaded_through_to_the_next_call(fake_client):
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_card", {"interactions": []})

    _salt_common.check_for_answer(client, "key-1", CARD_ID, ACTION_MAP, HUMANS, "ia-7")

    get_card_calls = [c for c in client.calls if c[0] == "get_card"]
    assert len(get_card_calls) == 1
    assert get_card_calls[0][1]["after"] == "ia-7"


def test_a_garbage_after_still_answers(fake_client):
    """salt-api's `CardInteraction.page` fails OPEN on an unrecognised
    `after` (the full list, never a 500) -- this client never validates
    its own cursor before sending it, so a corrupt/garbage value simply
    gets the full (capped) interaction list back, same as any other
    call, and still resolves normally."""
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_card", {
        "interactions": [
            {"id": "ia-1", "user_id": "user-2", "action_id": "opt_0", "value": None, "created_at": "2026-09-26T00:00:01Z"},
        ],
    })

    result, _new_after = _salt_common.check_for_answer(
        client, "key-1", CARD_ID, ACTION_MAP, HUMANS, "not-a-real-interaction-id",
    )

    assert result["status"] == "answered"
    assert result["answer"] == "Yes"


def test_a_pay_taps_transfer_request_fields_ride_along(fake_client):
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_card", {
        "interactions": [
            {
                "id": "ia-1", "user_id": "user-2", "action_id": "opt_0", "value": {"transfer_request_id": "tr-1"},
                "transfer_request_id": "tr-1", "transfer_request_status": "Confirmed",
                "created_at": "2026-09-26T00:00:01Z",
            },
        ],
    })

    result, _new_after = _salt_common.check_for_answer(client, "key-1", CARD_ID, ACTION_MAP, HUMANS, None)

    assert result["transfer_request_id"] == "tr-1"
    assert result["transfer_request_status"] == "Confirmed"


def test_a_429_propagates_as_a_salt_api_error_carrying_retry_after(fake_client):
    """check_for_answer itself never turns a 429 into a "pending" result --
    that decision belongs to the CALLER (ask_human/get_answer), which is
    what actually implements the owner's "never loop on a 429, surface a
    hint instead" rule. This just confirms the error (with its
    retry_after) reaches the caller intact."""
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_card", SaltApiError("GET", "https://saltapp.ai/api/v1/cards/card-123", 429, {"error": "rate limited"}, retry_after=5))

    try:
        _salt_common.check_for_answer(client, "key-1", CARD_ID, ACTION_MAP, HUMANS, None)
        raise AssertionError("expected a SaltApiError")
    except SaltApiError as exc:
        assert exc.status == 429
        assert exc.retry_after == 5

    assert len([c for c in client.calls if c[0] == "get_card"]) == 1


def test_check_for_answer_never_reads_the_agent_updates_outbox(fake_client):
    """Belt and suspenders on top of the grep check: FakeSaltClient has no
    `get_agent_updates` method at all any more, so a regression that
    reintroduced that call would raise AttributeError here, not silently
    pass."""
    client = fake_client({"host": "https://saltapp.ai"})
    assert not hasattr(client, "get_agent_updates")
