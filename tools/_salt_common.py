"""Shared helpers for every Salt tool in this plugin.

Not a Dify entity itself -- just the one place the business logic that
every `tools/*.py` file would otherwise duplicate actually lives:

- SaltClient construction and caching per host (`get_client`).
- `who_am_i` webhook-secret resolution and caching (`get_webhook_secret`).
- `send_message`'s "resolve recipients, encrypt, post" sequence.
- The ask/poll loop shared by `ask_human` and `get_answer`
  (`poll_for_answer`), plus the file-based pending-ask store it and
  `get_answer` read and write.
- Small parameter parsers (`parse_options`, `parse_line_items`) for the
  Dify tool parameters that carry more than one plain scalar.

See AGENTS.md for why this is one shared module rather than six copies of
the same logic, and for the one deliberate simplification versus
`saltapp.socket.SocketClient` (no per-row "secret not available yet"
transient case -- this plugin resolves and caches the webhook secret
before a poll loop ever starts).
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

# Matches saltapp.socket.SOCKET_SIGNATURE_TOLERANCE_SECONDS: an update row
# can sit unpolled in the outbox for up to salt-api's retention window (7
# days) before this plugin ever sees it, so the webhook path's 300s replay
# tolerance would reject perfectly legitimate rows here.
SOCKET_SIGNATURE_TOLERANCE_SECONDS = 7 * 24 * 60 * 60 + 60 * 60

# ask_human never waits past this, no matter what the caller asks for.
MAX_ASK_TIMEOUT_SECONDS = 50
# get_answer is a "check now" call, not another full wait -- a short,
# bounded number of quick rounds.
RESUME_POLL_BUDGET_SECONDS = 8

POLL_ROUND_TIMEOUT = 2  # GET /api/v1/agent/updates clamps this to 0..2s anyway
POLL_ROUND_SLEEP_SECONDS = 1.0

_lock = threading.Lock()
_client_cache: dict[str, SaltClient] = {}
_webhook_secret_cache: dict[str, str] = {}
_cursor_cache: dict[str, int] = {}


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
    form already asked for agent_id directly)."""
    client = get_client(credentials)
    api_key = str(credentials["api_key"])
    agent_id = str(credentials["agent_id"])
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


def get_cursor(client: SaltClient, api_key: str) -> int:
    """The socket-mode cursor this identity last polled up to. 0 the first
    time `ask_human` is ever called for this (host, api_key) -- see this
    module's docstring and AGENTS.md for why starting from 0 once, rather
    than on every ask, is the deliberate trade-off here."""
    with _lock:
        return _cursor_cache.get(_cache_key(client.host, api_key), 0)


def set_cursor(client: SaltClient, api_key: str, cursor: int) -> None:
    with _lock:
        _cursor_cache[_cache_key(client.host, api_key)] = cursor


# -- send_message -------------------------------------------------------------


def send_message(client: SaltClient, api_key: str, agent_id: str, chat_id: str, text: str) -> dict[str, Any]:
    """`saltapp.client.SaltClient.send_message`'s convenience wrapper needs
    an `Identity` (with its own `public_key`), which this plugin doesn't
    have as a credential -- so this re-implements the same sequence
    directly: fetch the chat's current members, encrypt for every member
    but self, and (best-effort) encrypt a copy for self too so this
    agent's own history stays legible if it happens to be a member with a
    known public key."""
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


# -- ask/poll -----------------------------------------------------------------


def _verify_row(row: dict[str, Any], webhook_secret: str) -> Event | None:
    """Verify + parse one `GET /api/v1/agent/updates` row. Returns None on
    ANY verification failure -- at this call site the secret itself is
    already known-good (resolved by `get_webhook_secret` before the poll
    loop starts), so every failure here is a definitive rejection (bad
    signature, malformed header, a timestamp genuinely outside the wide
    socket-mode tolerance), never the "secret not available yet" transient
    case `saltapp.socket.SocketClient` also has to handle. See this
    module's docstring."""
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


def poll_for_answer(
    client: SaltClient,
    api_key: str,
    webhook_secret: str,
    card_id: str,
    action_map: dict[str, str],
    cursor: int,
    deadline: float,
) -> tuple[dict[str, Any], int]:
    """The one poll loop behind both `ask_human`'s initial wait and
    `get_answer`'s resume. Short-polls `GET /api/v1/agent/updates` from
    `cursor` until either a human's tap on `card_id` arrives or
    `deadline` (a `time.monotonic()` instant) passes.

    A tap from an agent (`user.account_type == "Agent"`) is never treated
    as an answer -- only a human's tap resolves a pending ask. Every row
    advances the cursor once handled, whether it matched, was skipped, or
    failed verification (see `_verify_row`'s docstring for why there is no
    "halt without advancing" case here).
    """
    while True:
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

        if time.monotonic() >= deadline:
            return {"status": "pending"}, cursor

        time.sleep(max(0.0, min(POLL_ROUND_SLEEP_SECONDS, deadline - time.monotonic())))


# -- pending-ask file store ---------------------------------------------------
#
# File-based, not in-memory: Dify's serverless install method can run a
# plugin across several worker processes, and `get_answer` needs to find an
# ask that a DIFFERENT process's `ask_human` call created. Mirrors
# saltapp.socket.default_state_dir's chmod convention (0700 dirs, 0600
# files) one level under it, at ~/.salt/dify-plugin/asks/.


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


def save_pending_ask(record: dict[str, Any]) -> None:
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
