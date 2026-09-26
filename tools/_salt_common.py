"""Shared helpers for every Salt tool in this plugin.

Not a Dify entity itself -- just the one place the business logic that
every `tools/*.py` file would otherwise duplicate actually lives:

- SaltClient construction and caching per host (`get_client`).
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
stateless Dify tool calls): `check_for_answer` makes exactly one Salt API
call and returns, never retries or sleeps. Push delivery (a human's tap
arriving instantly, without another tool call) goes through
`endpoints/salt_webhook.py`'s verified webhook, not through polling
harder.

**2026-09-26: `check_for_answer` no longer reads `GET
/api/v1/agent/updates` (the socket-mode outbox) at all.** That outbox
keeps exactly ONE forward-only cursor PER AGENT on the server (`after=0`
is the same as omitting it; a lower `after` is silently ignored). A
stateless tool call that polls it therefore races every other consumer of
that same agent's deliveries: two concurrent `ask_human`/`get_answer`
checks -- or an ask running beside anything else reading that agent's
outbox (a real socket-mode client, another pending ask) -- could each
advance the SAME cursor and silently steal each other's rows, stranding
one of them forever. `check_for_answer` now reads `GET
/api/v1/cards/:id` instead (`get_card`, below): one card's own
interaction log, scoped to that card alone. Reading a card by id is
idempotent and shares no state with any other ask, this agent's other
checks, or any other consumer of this agent's outbox -- see `get_card`'s
docstring. Mirrors the identical fix in salt-mcp's `getCard`/
`pollForCardInteraction` and saltapp-agentkit's `ask_poll.py`.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from saltapp import cards, crypto
from saltapp.client import SaltClient
from saltapp.errors import SaltApiError

_lock = threading.Lock()
_client_cache: dict[str, SaltClient] = {}


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


def humans_by_id(members: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Builds `check_for_answer`'s `humans` map: every non-agent member of
    `members` (a chat's `session.users`, or a `get_chat_members` call),
    keyed by lowercased id, holding just enough of that member's public
    info to answer `ask_human`/`get_answer`'s response shape with (`id`,
    `username`, `display_name`, `account_type`).

    This exists because `GET /api/v1/cards/:id`'s own interaction log
    (see `get_card`, below) carries only `user_id` per tap -- never a
    full user object or `account_type` -- unlike the old socket-mode
    outbox event body, which carried the tapper's whole `user` dict for
    free. Resolving "is this tapper a human" therefore needs the chat's
    member list; computed ONCE, at `ask_human` time, and persisted in
    the pending-ask record so `get_answer`'s one on-demand check never
    needs a second chat read to tell a human's tap from an agent's (see
    `check_for_answer`'s docstring)."""
    result: dict[str, dict[str, Any]] = {}
    for member in members:
        if str(member.get("account_type")) == "Agent":
            continue  # an agent's tap never resolves a pending human ask
        member_id = str(member.get("id", "")).lower()
        if not member_id:
            continue
        result[member_id] = {
            "id": member.get("id"),
            "username": member.get("username"),
            "display_name": member.get("display_name"),
            "account_type": member.get("account_type"),
        }
    return result


def get_chat_members_map(client: SaltClient, api_key: str, chat_id: str) -> dict[str, dict[str, Any]]:
    """Fetches `chat_id`'s member list -- mirroring `send_message`'s own
    `session.users`-first, `get_chat_members`-fallback read -- and
    returns `check_for_answer`'s `humans` map (`humans_by_id`, above).
    Called once, at `ask_human` time; `get_answer` reuses the persisted
    result rather than repeating this call (see `humans_by_id`'s
    docstring)."""
    chat = client.get_chat(api_key, chat_id)
    session = chat.get("session") or {}
    members = session.get("users")
    if members is None:
        members = client.get_chat_members(api_key, chat_id)
    return humans_by_id(members or [])


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
    """`POST /api/v1/cards`'s response (salt-api 0.96.1,
    `Api::V1::CardsController#create`) has NO top-level `id` -- the
    card's own id comes back as `resource_id` (and, redundantly,
    `resource.id`); `message_id` is the bubble's own id, a different
    thing. A 2026-09-26 review found this function reading `response.get
    ("id")`, which is always None on a real response (only this
    plugin's own test fakes ever stubbed a top-level `id`, hiding the
    bug) -- fixed here, and the fakes now model the real shape too."""
    card_id = response.get("resource_id") or (response.get("resource") or {}).get("id")
    if not card_id:
        raise SaltApiError(
            "POST", "/api/v1/cards", 0,
            {"error": "card response carried no resource_id/resource.id"},
        )
    return str(card_id)


# -- ask/answer (single-shot, no polling) -------------------------------------
#
# The owner's explicit rule: a Dify tool call is one stateless HTTP round
# trip, never a client-side while/for + time.sleep() retry loop. Everything
# below makes exactly one Salt API call per invocation and returns. Instant
# delivery comes from `endpoints/salt_webhook.py`'s verified push
# (`apply_pushed_card_interaction`, below), which writes the same
# pending-ask file this on-demand check reads.
#
# THIS NO LONGER READS `GET /api/v1/agent/updates` (the socket-mode
# outbox) -- see this module's docstring for why that was a bug (one
# forward-only cursor per agent, shared across every concurrent ask and
# every other consumer of that agent's deliveries) and `get_card`'s
# docstring, immediately below, for the replacement.


def get_card(client: SaltClient, api_key: str, card_id: str, after: Any = None) -> dict[str, Any]:
    """`GET /api/v1/cards/:id` (salt-api 0.96.1) -- one card's own
    interaction log, scoped to that card alone: owner-only (a bearer
    whose agent doesn't own the card 404s, indistinguishably from an
    unknown id). `interactions` comes back newest first, capped at 50
    server-side; `after` (an interaction id, or an ISO8601 timestamp)
    asks for only interactions strictly newer than that, and an
    unrecognised value fails OPEN server-side (the full list, never a
    500) -- this never needs to validate its own cursor before sending
    it.

    Replaces the old outbox poll (`GET /api/v1/agent/updates`): that
    outbox keeps exactly ONE forward-only cursor PER AGENT, so two
    concurrent asks for the same agent -- or an ask running beside any
    other consumer of that agent's deliveries -- could each advance the
    SAME cursor and silently steal each other's rows. Reading one card by
    id is idempotent and shares no state with any other ask. Mirrors
    salt-mcp's `getCard` and saltapp-agentkit's `ask_poll.py::_get_card`.

    `saltapp.client.SaltClient` has no public `get_card` method as of
    this writing (confirmed: no `def get_card` anywhere in that package,
    version 0.3.2) -- this goes through `_request`, the same underlying
    method every public call on `SaltClient` is already built on, rather
    than adding one to a sibling package this repo doesn't own."""
    path = f"/api/v1/cards/{quote(str(card_id), safe='')}"
    if after is not None and after != "":
        path += f"?after={quote(str(after), safe='')}"
    return client._request("GET", path, api_key)  # noqa: SLF001 -- see docstring


def check_for_answer(
    client: SaltClient,
    api_key: str,
    card_id: str,
    action_map: dict[str, str],
    humans: dict[str, dict[str, Any]],
    after: Any,
) -> tuple[dict[str, Any], Any]:
    """The one check behind both `ask_human`'s initial look and
    `get_answer`'s on-demand fallback. Exactly ONE `GET /api/v1/cards/:id`
    call from `after` -- no retry, no sleep, no deadline. Returns
    `({"status": "answered", ...}, new_after)` the moment a human's tap on
    one of `action_map`'s buttons is found among the interactions this one
    call returned, or `({"status": "pending"}, new_after)` if none was --
    the caller decides what to do next (`ask_human` persists a pending-ask
    record; `get_answer` does the same and returns pending).

    `humans` (see `humans_by_id`/`get_chat_members_map`) maps a lowercased
    member user id to that member's public info -- `GET
    /api/v1/cards/:id`'s interactions carry only `user_id`, never a full
    user object or `account_type`, so this is how an agent's tap is told
    apart from a human's without a second network call: only a `user_id`
    present in `humans` counts as an answer (an id absent from it -- an
    agent, or anyone this ask never resolved to a member -- never
    resolves a pending human ask, mirroring the old outbox check's
    `account_type == "Agent"` rule). `after` advances to the NEWEST
    interaction id seen regardless of whether it matched this ask's own
    buttons or who tapped, so a resumed check (`get_answer`) never re-reads
    a row it has already looked at and rejected.
    """
    response = get_card(client, api_key, card_id, after=after)
    interactions = response.get("interactions") or []
    new_after = after
    if interactions and interactions[0].get("id") is not None:
        new_after = interactions[0]["id"]

    for interaction in interactions:
        action_id = interaction.get("action_id")
        if action_id not in action_map:
            continue
        user_id = str(interaction.get("user_id") or "").lower()
        user = humans.get(user_id)
        if user is None:
            continue  # an agent's tap (or an unresolved id) never resolves a pending human ask
        answered: dict[str, Any] = {
            "status": "answered",
            "answer": action_map.get(str(action_id), action_id),
            "action_id": action_id,
            "user": user,
        }
        # Rides along only on a "pay" button's tap (never one of
        # ask_human's own plain option buttons today) -- see
        # CardInteraction#as_json_for_owner. Threaded through anyway so
        # the next card type that reuses this poll doesn't need its own
        # answer-shaping code, mirroring salt-mcp/saltapp-agentkit.
        if interaction.get("transfer_request_id"):
            answered["transfer_request_id"] = interaction["transfer_request_id"]
            answered["transfer_request_status"] = interaction.get("transfer_request_status")
        return answered, new_after

    return {"status": "pending"}, new_after


def pending_with_retry_hint(ask_id: str, retry_after: int | None) -> dict[str, Any]:
    """`ask_human`/`get_answer`'s response when their one on-demand check
    hits a 429 -- never a loop, never a hard error: this is still a
    `"pending"` result, just carrying `retry_after_seconds` (from
    `SaltApiError.retry_after`, Rack::Attack's `Retry-After` header) when
    Salt sent one, so a caller knows how long to wait before calling
    `get_answer` again rather than hammering it immediately."""
    result: dict[str, Any] = {"status": "pending", "ask_id": ask_id}
    if retry_after:
        result["retry_after_seconds"] = retry_after
        result["hint"] = f"Rate limited -- wait about {retry_after}s before calling get_answer again."
    return result


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
