"""Builds a socket-mode `GET /api/v1/agent/updates` row whose
`X-Salt-Signature` header is a genuine HMAC over the row's own body --
the same scheme `saltapp.webhook.verify_signature` checks. Tests use this
so `poll_for_answer`'s verification step is exercised for real, never
stubbed out, exactly like `saltapp-python`'s own test suite treats
signature checking (see AGENTS.md's "no fake completion" note)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any


def signed_update_row(
    secret: str,
    body: dict[str, Any],
    *,
    row_id: int,
    delivery_id: str = "delivery-1",
    agent_id: str = "agent-1",
    now: float | None = None,
) -> dict[str, Any]:
    raw = json.dumps(body)
    timestamp = int(now if now is not None else time.time())
    signed_string = f"{timestamp}.".encode("utf-8") + raw.encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed_string, hashlib.sha256).hexdigest()
    headers = {
        "X-Salt-Signature": f"t={timestamp},v1={digest}",
        "X-Salt-Agent-Id": agent_id,
        "X-Salt-Delivery-Id": delivery_id,
    }
    return {"id": row_id, "delivery_id": delivery_id, "headers": headers, "body": raw, "created_at": None}
