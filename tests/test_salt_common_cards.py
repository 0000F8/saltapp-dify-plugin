"""Direct unit tests for `_salt_common.extract_card_id`/`humans_by_id`/
`get_chat_members_map` -- the pieces of the 2026-09-26 fix that aren't
already covered end to end by `test_ask_human.py`.

`extract_card_id` in particular: a 2026-09-26 review found it reading
`response.get("id")`, which a real `POST /api/v1/cards` response never
carries (salt-api 0.96.1's `Api::V1::CardsController#create` answers
`{message_id, resource_id, resource: {...}, ...}` -- no top-level `id`
at all); this plugin's own test fakes had stubbed a fictional `{"id":
...}` shape, which is exactly how the bug went unnoticed. These tests
use the REAL response shape.
"""
from __future__ import annotations

import pytest
from saltapp.errors import SaltApiError

from tools import _salt_common


def test_extract_card_id_reads_resource_id():
    response = {"message_id": "msg-1", "resource_id": "card-123", "resource": {"id": "card-123"}}
    assert _salt_common.extract_card_id(response) == "card-123"


def test_extract_card_id_falls_back_to_resource_dot_id_when_resource_id_is_missing():
    response = {"message_id": "msg-1", "resource": {"id": "card-456"}}
    assert _salt_common.extract_card_id(response) == "card-456"


def test_extract_card_id_ignores_a_top_level_id_a_real_response_never_has():
    """A real `POST /api/v1/cards` response has no top-level `id` -- if
    one is somehow present (e.g. a caller accidentally merged in another
    field), it must NOT be read; only resource_id/resource.id are ever
    the card's actual id."""
    response = {"id": "not-the-card-id", "resource_id": "card-123", "resource": {"id": "card-123"}}
    assert _salt_common.extract_card_id(response) == "card-123"


def test_extract_card_id_raises_clearly_when_neither_field_is_present():
    with pytest.raises(SaltApiError, match="resource_id"):
        _salt_common.extract_card_id({"message_id": "msg-1"})


def test_humans_by_id_excludes_agents_and_keys_by_lowercased_id():
    members = [
        {"id": "Agent-1", "username": "salt_agent", "account_type": "Agent"},
        {"id": "User-2", "username": "dana", "display_name": "Dana", "account_type": "Human"},
    ]
    result = _salt_common.humans_by_id(members)
    assert result == {
        "user-2": {"id": "User-2", "username": "dana", "display_name": "Dana", "account_type": "Human"},
    }


def test_humans_by_id_skips_a_member_with_no_id():
    members = [{"username": "ghost", "account_type": "Human"}]
    assert _salt_common.humans_by_id(members) == {}


def test_get_chat_members_map_reads_session_users_without_a_second_call(fake_client):
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_chat", {
        "session": {"users": [{"id": "user-2", "username": "dana", "account_type": "Human"}]},
    })

    result = _salt_common.get_chat_members_map(client, "key-1", "chat-1")

    assert result == {"user-2": {"id": "user-2", "username": "dana", "display_name": None, "account_type": "Human"}}
    assert not any(c[0] == "get_chat_members" for c in client.calls)


def test_get_chat_members_map_falls_back_to_get_chat_members_when_session_has_no_users(fake_client):
    client = fake_client({"host": "https://saltapp.ai"})
    client.stub("get_chat", {"session": {}})
    client.stub("get_chat_members", [{"id": "user-2", "username": "dana", "account_type": "Human"}])

    result = _salt_common.get_chat_members_map(client, "key-1", "chat-1")

    assert result == {"user-2": {"id": "user-2", "username": "dana", "display_name": None, "account_type": "Human"}}
    assert any(c[0] == "get_chat_members" for c in client.calls)
