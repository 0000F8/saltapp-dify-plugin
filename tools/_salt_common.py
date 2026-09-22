"""Shared helpers for every Salt tool in this plugin.

Not a Dify entity itself -- just the one place the business logic that
every `tools/*.py` file would otherwise duplicate actually lives:

- SaltClient construction and caching per host (`get_client`).
- `who_am_i` webhook-secret resolution and caching (`get_webhook_secret`).
- `send_message`'s "resolve recipients, encrypt, post" sequence, now
  branching to a plain-text post for an open (unencrypted) room.
- `read_room`, the anonymous-capable wrapper around `get_chat` the
  `read_room` tool uses.
- The single-shot answer check shared by `ask_human` and `get_answer`
  (`check_for_answer`), plus the file-based pending-ask store both tools
  (and the webhook endpoint's `apply_pushed_card_interaction`) read and
  write.
- Small parameter parsers (`parse_options`, `parse_line_items`) for the
  Dify tool parameters that carry more than one plain scalar.

See AGENTS.md for why this is one shared module rather than several
copies of the same logic, and for why there is no client-side poll loop
anywhere in this plugin (the owner's explicit no-polling rule for
stateless Dify tool calls): `check_for_answer` makes exactly one
`GET /api/v1/agent/updates` call and returns, never retries or sleeps.
Push delivery (a human's tap arriving instantly, without another tool
call) goes through `endpoints/salt_webhook.py`'s verified webhook, not
through polling harder.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from saltapp import cards, crypto
from saltapp.client import SaltClient
from saltapp.errors import SaltApiError
from saltapp.webhook import Event, WebhookVerificationError, handle

# Round-3/4 socket contract (LANES.md K2, revised 2026-09-18, "fix A" --
# serve-time signing): salt-api now re-signs every outbox row FRESH, over
# the stored body, with this agent's CURRENT webhook secret, at the moment
# it's actually served by GET /api/v1/agent/updates -- never once at
# enqueue time. So a row that sat unpolled for the full 7-day retention
# window verifies with a signature timestamped as if written just now, and
# the STANDARD ~300s tolerance (matching the webhook path) is correct and
# sufficient here too. A wider tolerance, which this constant used to be
# (7 days + 1h, matching saltapp.socket's own pre-round-4 value -- see
# saltapp-python's HANDOFF/FOLLOWUPS for that repo's own pending fix), is
# no longer needed and is a real weakness: it would accept a signature far
# older than any genuine serve-time one could be.
SOCKET_SIGNATURE_TOLERANCE_SECONDS = 300

# check_for_answer's one and only GET /api/v1/agent/updates call per
# invocation uses this as the server-side hold (salt-api clamps it to
# 0..2s regardless) -- NOT a client-side retry budget. There is no
# ask_human/get_answer deadline or retry budget any more: see this
# module's docstring and AGENTS.md for why a single check, never a loop,
# is the whole design now.
POLL_ROUND_TIMEOUT = 2

_lock = threading.Lock()
_client_cache: dict[str, SaltClient] = {}
_webhook_secret_cache: dict[str, str] = {}


def _cache_key(host: str, api_key: str) -> str:
    return f"{host.rstrip('/')}::{api_key}"


# -- client / identity -------------------------------------------------------


def get_client(credentials: dict[str, Any]) -> SaltClient:
    """One cached `SaltClient` (and its underlying httpx connection pool)
    per host. Dify may construct a fresh `Tool` instance per invocation, so
    this module-level cache is what keeps a busy plugin from opening a new
    connection pool on every single call."""
    host = str(credentials["host"]).rstrip("/")
    with _lock:
        client = _client_cache.get(host)
        if client is None:
            client = SaltClient(host)
            _client_cache[host] = client
        return client


def client_and_identity(credentials: dict[str, Any]) -> tuple[SaltClient, str, str]:
    """The three things almost every tool needs: a client, this agent's own
    api key, and this agent's own id (both straight from credentials --
    there is no server round trip to resolve identity, since the provider
    form already asked for agent_id directly). `agent_id`/`api_key` are now
    optional provider credentials (read_room works with neither), so this
    reads them with `.get(...) or ""` rather than `credentials[...]` --
    a blank/missing value becomes `""`, never a KeyError. A tool that
    genuinely needs a real api key (everything except read_room) is
    responsible for checking it itself and giving a clear message, same as
    `interests.py` does."""
    client = get_client(credentials)
    api_key = str(credentials.get("api_key") or "")
    agent_id = str(credentials.get("agent_id") or "")
    return client, api_key, agent_id


def get_webhook_secret(client: SaltClient, api_key: str) -> str:
    """`who_am_i`'s `webhook_secret`, cached per (host, api_key) so
    `ask_human`/`get_answer` don't call it on every single invocation."""
    key = _cache_key(client.host, api_key)
    with _lock:
        cached = _webhook_secret_cache.get(key)
    if cached:
        return cached
    info = client.who_am_i(api_key)
    secret = info.get("webhook_secret")
    if not secret:
        raise SaltApiError(
            "GET", f"{client.host}/api/v1/agents/webhook_secret", 0,
            {"error": "who_am_i returned no webhook_secret for this api key"},
        )
    secret = str(secret)
    with _lock:
        _webhook_secret_cache[key] = secret
    return secret


# -- read_room -----------------------------------------------------------------


def read_room(client: SaltClient, api_key: str, chat_id: str, last: Any = None) -> dict[str, Any]:
    """Thin wrapper around `SaltClient.get_chat` for the `read_room` tool.
    `api_key or ""` means a blank/missing credential sends NO api-key
    header at all (see `SaltClient._request`'s own docstring) -- enough to
    read a `public && !encrypted` "open" room anonymously (no PGP
    recipient list, no per-viewer membership state); anything else with a
    blank api_key still 404s, indistinguishably, on purpose. Every message
    in the response's `messages` array already carries its own
    `encrypted`/`delivered_because` fields verbatim from salt-api -- this
    plugin never needs to compute or branch on them itself, just pass the
    response through."""
    return client.get_chat(api_key or "", chat_id, last=last)


# -- send_message -------------------------------------------------------------


def send_message(client: SaltClient, api_key: str, agent_id: str, chat_id: str, text: str) -> dict[str, Any]:
    """`saltapp.client.SaltClient.send_message`'s convenience wrapper needs
    an `Identity` (with its own `public_key`), which this plugin doesn't
    have as a credential -- so this re-implements the same sequence
    directly: fetch the chat's current session, and either post plain text
    (an open/unencrypted room) or encrypt for every member but self and
    (best-effort) also encrypt a copy for self so this agent's own history
    stays legible if it happens to be a member with a known public key.

    **Member source.** The chat's own `get_chat` response already carries
    every member under `session.users` (see `SaltClient.get_chat_members`,
    which just reads that same key) -- this function reads it straight off
    the `chat` it already fetched to decide encrypted-vs-plain, saving a
    second round trip, and falls back to a real `get_chat_members` call
    only on the rare response that has a `session` but no `users` key
    (`session` itself is always present on a real chat; a missing `users`
    would be unusual, not the common case, but cheap to guard against)."""
    chat = client.get_chat(api_key, chat_id)
    session = chat.get("session") or {}

    if not session.get("encrypted", True):
        return client.post_plain_message(api_key, chat_id, text)

    members = session.get("users")
    if members is None:
        members = client.get_chat_members(api_key, chat_id)
    self_id = agent_id.lower()
    recipient_keys: list[str] = []
    self_public_key: str | None = None
    for member in members:
        member_id = str(member.get("id", "")).lower()
        public_key = member.get("public_key")
        if member_id == self_id:
            if public_key:
                self_public_key = public_key
            continue
        if public_key:
            recipient_keys.append(public_key)

    if not recipient_keys:
        raise SaltApiError(
            "POST", f"{client.host}/api/v1/messages", 0,
            {"error": "no recipient public keys in this chat; nothing to send to."},
        )

    encrypted = crypto.encrypt_for(text, recipient_keys)
    sender_message: str | None = None
    if self_public_key:
        try:
            sender_message = crypto.encrypt_for(text, [self_public_key])
        except Exception:  # noqa: BLE001 -- best-effort only, see docstring
            sender_message = None
    return client.post_message(api_key, chat_id, encrypted, sender_message)


# -- cards --------------------------------------------------------------------


def build_button_card(text: str, button_labels: list[str], action_prefix: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """A `section(text)` + one `actions` row, one button per label, ids
    `<action_prefix>_<i>`. Returns (blocks, {action_id: label}) so a caller
    can translate a tap back to the label the human saw."""
    if not button_labels:
        return cards.blocks(cards.section(text=text)), {}
    action_map: dict[str, str] = {}
    buttons = []
    for i, label in enumerate(button_labels):
        action_id = f"{action_prefix}_{i}"
        action_map[action_id] = label
        buttons.append(cards.button(action_id, label))
    blocks = cards.blocks(cards.section(text=text), cards.actions(buttons))
    return blocks, action_map


def extract_card_id(response: dict[str, Any]) -> str:
    card_id = response.get("id") or response.get("card_id")
    if not card_id:
        raise SaltApiError("POST", "/api/v1/cards", 0, {"error": "card response carried no id/card_id"})
    return str(card_id)


# -- ask/answer (single-shot, no polling) -------------------------------------
#
# The owner's explicit rule: a Dify tool call is one stateless HTTP round
# trip, never a client-side while/for + time.sleep() retry loop. Everything
# below makes exactly one `GET /api/v1/agent/updates` call per invocation
# and returns -- `POLL_ROUND_TIMEOUT` is a short SERVER-side hold on that
# one request (salt-api clamps it to 0..2s), not a client retry budget.
# Instant delivery now comes from `endpoints/salt_webhook.py`'s verified
# push (`apply_pushed_card_interaction`, below), which writes the same
# pending-ask file this on-demand check reads.


def _verify_row(row: dict[str, Any], webhook_secret: str) -> Event | None:
    """Verify + parse one `GET /api/v1/agent/updates` row. Returns None on
    ANY verification failure -- at this call site the secret itself is
    already known-good (resolved by `get_webhook_secret` before
    `check_for_answer` is ever called), so every failure here is a
    definitive rejection (bad signature, malformed header, a timestamp
    genuinely outside the socket-mode tolerance), never the "secret not
    available yet" transient case `saltapp.socket.SocketClient` also has
    to handle. See this module's docstring."""
    raw_body = row.get("body")
    payload = raw_body if isinstance(raw_body, str) else json.dumps(raw_body or {})
    try:
        return handle(
            row.get("headers") or {},
            payload,
            secret=webhook_secret,
            verify=True,
            tolerance_seconds=SOCKET_SIGNATURE_TOLERANCE_SECONDS,
        )
    except WebhookVerificationError:
        return None


def check_for_answer(
    client: SaltClient,
    api_key: str,
    webhook_secret: str,
    card_id: str,
    action_map: dict[str, str],
    cursor: int,
) -> tuple[dict[str, Any], int]:
    """The one check behind both `ask_human`'s initial look and
    `get_answer`'s on-demand fallback. Exactly ONE
    `GET /api/v1/agent/updates` call from `cursor` -- no retry, no sleep,
    no deadline. Returns `({"status": "answered", ...}, new_cursor)` the
    moment a verified human tap on `card_id` is found among the rows this
    one call returned, or `({"status": "pending"}, new_cursor)` if none
    was -- the caller decides what to do next (ask_human persists a
    pending-ask record; get_answer does the same and returns pending).

    A tap from an agent (`user.account_type == "Agent"`) is never treated
    as an answer -- only a human's tap resolves a pending ask. Every row
    this one call saw advances the cursor, whether it matched, was
    skipped, or failed verification (see `_verify_row`'s docstring for why
    there is no "halt without advancing" case here).
    """
    response = client.get_agent_updates(api_key, after=cursor, timeout=POLL_ROUND_TIMEOUT, limit=100)
    updates = response.get("updates") or []
    for row in updates:
        row_id = row.get("id")
        if row_id is not None:
            cursor = row_id
        event = _verify_row(row, webhook_secret)
        if event is None or event.type != "card_interaction":
            continue
        if event.body.get("card_id") != card_id:
            continue
        user = event.body.get("user") or {}
        if str(user.get("account_type")) == "Agent":
            continue  # an agent's tap never resolves a pending human ask
        action_id = event.body.get("action_id")
        answered = {
            "status": "answered",
            "answer": action_map.get(str(action_id), action_id),
            "action_id": action_id,
            "user": user,
        }
        return answered, cursor

    server_cursor = response.get("cursor")
    if isinstance(server_cursor, int) and server_cursor > cursor:
        cursor = server_cursor

    return {"status": "pending"}, cursor


# -- pending-ask file store ---------------------------------------------------
#
# File-based, not in-memory: Dify's serverless install method can run a
# plugin across several worker processes, and `get_answer` needs to find an
# ask that a DIFFERENT process's `ask_human` call created. Mirrors
# saltapp.socket.default_state_dir's chmod convention (0700 dirs, 0600
# files) one level under it, at ~/.salt/dify-plugin/asks/.
#
# Garbage collection is opportunistic, not a background job -- a Dify tool
# plugin has no scheduler of its own, so `save_pending_ask` sweeps the
# directory for stale entries every time it writes a new one (cheap: a
# directory listing plus one stat() per file, never anywhere outside this
# one directory). An ask left on disk for over a day is abandoned -- either
# its workflow crashed before ever calling get_answer, or the human simply
# never will -- long past ask_human's own MAX_ASK_TIMEOUT_SECONDS (50s), so
# keeping it forever would just accumulate one file per abandoned ask.

STALE_ASK_MAX_AGE_SECONDS = 24 * 60 * 60


def _state_dir() -> Path:
    directory = Path.home() / ".salt" / "dify-plugin" / "asks"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def _safe_ask_id(ask_id: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(ask_id)) or "unknown"


def _ask_path(ask_id: str) -> Path:
    return _state_dir() / f"{_safe_ask_id(ask_id)}.json"


def gc_stale_pending_asks(max_age_seconds: float = STALE_ASK_MAX_AGE_SECONDS, now: float | None = None) -> int:
    """Deletes every pending-ask file older than `max_age_seconds` (by
    mtime). Best-effort: a file that vanishes or can't be stat'd/removed
    between listing and acting on it (another process's own delete_pending_ask
    racing this) is skipped, not an error. Returns the number removed --
    tests use this; callers otherwise don't need it."""
    cutoff = (now if now is not None else time.time()) - max_age_seconds
    removed = 0
    try:
        entries = list(_state_dir().iterdir())
    except OSError:
        return 0
    for path in entries:
        if not path.name.endswith(".json"):
            continue  # a stray .tmp from a crashed write, or anything else -- not ours to sweep here
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def save_pending_ask(record: dict[str, Any]) -> None:
    try:
        gc_stale_pending_asks()
    except Exception:  # noqa: BLE001 -- GC must never block saving a real, current ask
        pass
    path = _ask_path(record["card_id"])
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def load_pending_ask(ask_id: str) -> dict[str, Any] | None:
    path = _ask_path(ask_id)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def delete_pending_ask(ask_id: str) -> None:
    try:
        _ask_path(ask_id).unlink()
    except FileNotFoundError:
        pass


def apply_pushed_card_interaction(body: dict[str, Any]) -> bool:
    """What makes push real: `endpoints/salt_webhook.py` calls this with a
    verified `card_interaction` event's body, and this writes to the exact
    same on-disk record `check_for_answer`'s on-demand path reads --
    there are not two answer stores, just two ways of reaching one.

    Looks up `body["card_id"]` in the pending-ask store. If a record is
    found, still pending (its `status` isn't already `"answered"` -- a
    guard against Salt redelivering the same webhook, see the workspace
    note on `X-Salt-Delivery-Id`; `get_answer` deletes the file the moment
    it reads an answered one, so this is normally a fast no-op, not a
    common case), and the tapper isn't an agent (the same rule
    `check_for_answer` applies -- only a human's tap counts), marks it
    `"answered"` in place with `answer`/`action_id`/`user` (via
    `save_pending_ask`) and returns True.

    Returns False for anything else -- no `card_id`, an unrelated card
    this process never asked about, an ask that's already answered, or an
    agent's own tap. None of those are errors; the webhook endpoint
    returns 200 regardless, since an event for a card this process
    doesn't know about is not a failure."""
    card_id = body.get("card_id")
    if not card_id:
        return False
    record = load_pending_ask(str(card_id))
    if record is None or record.get("status") == "answered":
        return False
    user = body.get("user") or {}
    if str(user.get("account_type")) == "Agent":
        return False
    action_id = body.get("action_id")
    action_map = record.get("action_map") or {}
    record["status"] = "answered"
    record["answer"] = action_map.get(str(action_id), action_id)
    record["action_id"] = action_id
    record["user"] = user
    save_pending_ask(record)
    return True


# -- parameter parsing ---------------------------------------------------------


def parse_options(raw: Any) -> list[str]:
    """`ask_human`/`post_card`'s button-label parameters arrive from Dify
    as either a plain comma-separated string (the common case for an
    LLM-filled parameter -- see e.g. a real Dify plugin's `usernames`
    parameter) or, occasionally, an already-parsed list. Accept both."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    raise ValueError(f"cannot read options/buttons from a {type(raw).__name__}; use a comma-separated string")


_LINE_ITEM_FIELDS = {"name", "qty", "unit_price", "subtotal"}


def parse_line_items(raw: Any) -> list[dict[str, Any]]:
    """`send_invoice`'s `line_items` parameter: there is no native
    structured-list Dify tool parameter type in the classic schema, so
    this accepts a JSON string (or an already-parsed list, defensively)
    and validates the shape `saltapp.client.SaltClient.create_invoice`
    (and salt-api's own model) expect: qty * unit_price == subtotal, and
    the subtotals must sum to the invoice's total amount -- that second
    check needs the amount too, so it happens in the tool, not here."""
    if raw is None:
        raise ValueError("line_items is required")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line_items is not valid JSON: {exc}") from exc
    else:
        parsed = raw
    if not isinstance(parsed, list) or not parsed:
        raise ValueError('line_items must be a JSON array of {"name", "qty", "unit_price", "subtotal"} objects')
    for item in parsed:
        if not isinstance(item, dict) or not _LINE_ITEM_FIELDS <= set(item.keys()):
            raise ValueError('each line_items entry needs "name", "qty", "unit_price", and "subtotal"')
    return parsed


def error_text(exc: Exception) -> str:
    """One plain sentence for `create_text_message`, whatever kind of
    error this plugin raised."""
    if isinstance(exc, SaltApiError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
