"""The push half of ask_human/get_answer.

Salt POSTs a `card_interaction` webhook here the instant a human taps a
card's button; this verifies the delivery and writes it to the exact same
on-disk pending-ask record `tools._salt_common.check_for_answer`'s
on-demand path reads (via `apply_pushed_card_interaction`) -- there are
not two answer stores, just two ways of reaching one. See
`tools/_salt_common.py`'s module docstring and `AGENTS.md` for the full
push-vs-on-demand design, and the owner's explicit no-polling rule this
whole thing exists to satisfy.

Configured by calling `register_webhook` (`tools/register_webhook.py`)
once, with the URL Dify shows on this plugin's Endpoints tab after
install -- that call points this agent's Salt webhook callback at this
endpoint. `settings["webhook_secret"]` below comes from a SEPARATE
credential form from the Salt tool provider's own credentials (see
`endpoints/salt_events.yaml`'s `settings:`), filled in by hand from
`GET /api/v1/agents/webhook_secret` / Developers > Your agents on Salt.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from werkzeug import Request, Response

from dify_plugin import Endpoint
from saltapp.webhook import WebhookVerificationError, handle

from tools._salt_common import apply_pushed_card_interaction


def _json_response(status: int, payload: dict) -> Response:
    return Response(json.dumps(payload), status=status, content_type="application/json")


class SaltWebhookEndpoint(Endpoint):
    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        secret = settings.get("webhook_secret")
        try:
            event = handle(dict(r.headers), r.get_data(as_text=True), secret=secret, verify=True)
        except WebhookVerificationError as exc:
            return _json_response(401, {"error": str(exc)})

        if event.type == "card_interaction":
            apply_pushed_card_interaction(event.body or {})

        # Always 200 once the signature verifies -- an event for a card
        # this process doesn't recognize (unrelated, already answered, or
        # from a different host/process) is not an error;
        # apply_pushed_card_interaction's own return value is
        # informational only, never a failure signal here.
        return _json_response(200, {"ok": True})
