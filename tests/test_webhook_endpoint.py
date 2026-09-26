"""SaltWebhookEndpoint: a genuinely HMAC-signed card_interaction delivery
verifies, updates the matching on-disk pending-ask file, and gets a 200;
a bad signature never touches the pending-ask store and gets a 401.

Builds a real werkzeug Request the same way Dify's own runtime would
construct one from a raw HTTP POST (`werkzeug.test.EnvironBuilder`), with
a genuinely HMAC-signed `X-Salt-Signature` header via `_signed_request`,
below (the same signing math `saltapp.webhook.handle` verifies for a real
webhook delivery). This is a REAL webhook POST from salt-api, entirely
separate from -- and unaffected by -- the 2026-09-26 fix that moved
`check_for_answer`'s on-demand check off the shared socket-mode outbox
and onto `GET /api/v1/cards/:id`: push delivery never went through that
outbox to begin with. `SaltWebhookEndpoint` is constructed the same way
`tests/fakes.py::make_tool` builds a `Tool`: `Endpoint.__init__` is
`@final` and needs a live plugin `Session` this test never opens, and
`_invoke` never touches `self.session`, so `object.__new__` bypasses it
safely.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from endpoints.salt_webhook import SaltWebhookEndpoint
from tools import _salt_common

WEBHOOK_SECRET = "whsec_test_secret"


def _signed_request(secret: str, body: dict, *, bad_signature: bool = False) -> Request:
    raw = json.dumps(body)
    timestamp = int(time.time())
    signed_string = f"{timestamp}.".encode("utf-8") + raw.encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed_string, hashlib.sha256).hexdigest()
    if bad_signature:
        digest = "0" * len(digest)
    headers = {
        "X-Salt-Signature": f"t={timestamp},v1={digest}",
        "X-Salt-Agent-Id": "agent-1",
        "X-Salt-Delivery-Id": "delivery-1",
        "Content-Type": "application/json",
    }
    builder = EnvironBuilder(method="POST", data=raw, headers=headers)
    return Request(builder.get_environ())


def _make_endpoint() -> SaltWebhookEndpoint:
    # Mirrors tests/fakes.py::make_tool's object.__new__ pattern: Endpoint's
    # __init__ is `@final` and expects a live Session this test never opens.
    return object.__new__(SaltWebhookEndpoint)


def test_webhook_endpoint_applies_a_verified_card_interaction_to_the_pending_ask():
    _salt_common.save_pending_ask({
        "card_id": "card-123",
        "chat_id": "chat-1",
        "action_map": {"opt_0": "Yes", "opt_1": "No"},
        "cursor": 0,
        "host": "https://saltapp.ai",
        "agent_id": "agent-1",
        "created_at": time.time(),
    })

    body = {
        "type": "card_interaction",
        "card_id": "card-123",
        "action_id": "opt_1",
        "chat_id": "chat-1",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
    }
    request = _signed_request(WEBHOOK_SECRET, body)

    endpoint = _make_endpoint()
    response = endpoint._invoke(request, {}, {"webhook_secret": WEBHOOK_SECRET})

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {"ok": True}

    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert record["status"] == "answered"
    assert record["answer"] == "No"
    assert record["action_id"] == "opt_1"
    assert record["user"] == {"id": "user-2", "username": "dana", "account_type": "Human"}


def test_webhook_endpoint_returns_200_for_an_unrelated_verified_event():
    """No pending ask exists for this card_id -- still a 200, never an
    error, since an event for a card this process doesn't know about is
    not a failure (see apply_pushed_card_interaction's docstring)."""
    body = {
        "type": "card_interaction",
        "card_id": "some-other-card",
        "action_id": "opt_0",
        "chat_id": "chat-1",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
    }
    request = _signed_request(WEBHOOK_SECRET, body)

    endpoint = _make_endpoint()
    response = endpoint._invoke(request, {}, {"webhook_secret": WEBHOOK_SECRET})

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {"ok": True}


def test_webhook_endpoint_rejects_a_bad_signature_with_401_and_never_touches_the_store():
    _salt_common.save_pending_ask({
        "card_id": "card-123",
        "chat_id": "chat-1",
        "action_map": {"opt_0": "Yes", "opt_1": "No"},
        "cursor": 0,
        "host": "https://saltapp.ai",
        "agent_id": "agent-1",
        "created_at": time.time(),
    })

    body = {
        "type": "card_interaction",
        "card_id": "card-123",
        "action_id": "opt_1",
        "chat_id": "chat-1",
        "user": {"id": "user-2", "username": "dana", "account_type": "Human"},
    }
    request = _signed_request(WEBHOOK_SECRET, body, bad_signature=True)

    endpoint = _make_endpoint()
    response = endpoint._invoke(request, {}, {"webhook_secret": WEBHOOK_SECRET})

    assert response.status_code == 401
    payload = json.loads(response.get_data(as_text=True))
    assert "error" in payload

    # The pending ask must be untouched -- a rejected signature never
    # reaches apply_pushed_card_interaction.
    record = _salt_common.load_pending_ask("card-123")
    assert record is not None
    assert "status" not in record
