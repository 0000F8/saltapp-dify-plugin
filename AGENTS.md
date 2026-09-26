# AGENTS.md

Notes for whoever maintains this plugin next.

## What this is

A Dify Tool plugin exposing ten of Salt's agent actions (`send_message`,
`ask_human`, `get_answer`, `post_card`, `request_payment`,
`send_invoice`, `get_payment_status`, `read_room`, `interests`,
`register_webhook`) as Dify tools, plus one Dify Endpoint
(`endpoints/salt_webhook.py`) that receives Salt's push delivery, so a
Dify agent or workflow can act as a Salt agent. It is built on
[`saltapp`](https://github.com/0000F8/saltapp-python) (source at
`../saltapp-python` in this workspace), Salt's Python SDK -- read that
repo's `README.md`/`AGENTS.md` for the SDK's own ground truth before
changing anything here that touches it. This plugin requires
`saltapp>=0.2.0` (open rooms, interests, `get_chat`'s `last=` cursor --
see that repo's `CHANGELOG.md` "0.2.0" entry); see `requirements.txt`'s
comment for why the pin itself still points at `@main` until that SDK
release actually merges.

## Why not `saltapp.integrations`/`Identity`/`Agent`

`saltapp.integrations._tools.SaltTools` and `saltapp.agent.Agent` both
assume a persistent, handler-registering process: `Agent` hosts one
identity for its whole lifetime and dispatches incoming webhooks/socket
events to `@agent.on_message`-style handlers; the framework integrations
fix a `chat_id` (and everything else about "where this conversation is
happening") at construction time. A Dify `Tool._invoke` is the opposite
shape: one stateless call per invocation, with no server-held identity
object and no "current chat" the tool can assume -- the calling agent
must say which chat every single time. That's why every tool here takes
`chat_id` explicitly, and why this plugin calls `SaltClient` methods
directly with plain strings from `self.runtime.credentials` rather than
constructing a `saltapp.identity.Identity`/`saltapp.agent.Agent`.

## `send_message`'s hand-rolled recipient resolution

`SaltClient.send_message` (the SDK's own convenience wrapper) takes an
`Identity`, which carries its own `public_key` -- something this plugin
doesn't have as a credential (the provider form only asks for the
private key, optionally, never the public one; asking for both would be
redundant since the public key is derivable from the private key, and
Salt already knows this agent's own public key server-side). So
`tools/_salt_common.py`'s `send_message()` re-implements the same
sequence directly: fetch the chat, and either post plain text (an open,
`encrypted: false` room -- see the open-rooms note below) or encrypt for
everyone but self (matched by `agent_id`, case-insensitive) and
best-effort also encrypt a copy for self if this agent happens to appear
in the member list with a known public key. If it doesn't (e.g. the
member list came back before this agent's own key propagated, or an
api-key-only setup that never registered a public key at all), the send
still succeeds; it just skips the sender-copy step silently.

**Open rooms.** `send_message` reads `session.encrypted` off the same
`get_chat` response it needs anyway to decide the branch, and for the
member list it reads `session.users` straight off that response too
(saving a second `get_chat_members` round trip) rather than always
re-fetching -- falling back to a real `get_chat_members` call only on the
unusual response that has a `session` but no `users` key. `request_payment`,
`send_invoice`, and `post_card` are NOT given this treatment: they ride
separate rails (TransferRequest, cards) that are not PGP-encrypted
regardless of room type -- confirmed by reading
`saltapp-python/src/saltapp/client.py`'s `request_payment`/
`create_invoice`/`post_card`, none of which reference `encrypted`
anywhere. Only `send_message` has an encrypted-vs-plain branch.

## The ask/answer design: no polling, push via a real Dify Endpoint

**This changed on 2026-09-22 -- a real behavior change from the previous
synchronous-wait design.** `ask_human` used to short-poll
`GET /api/v1/agent/updates` in a `while True` + `time.sleep()` loop for
up to `MAX_ASK_TIMEOUT_SECONDS` (50s) before giving up, and `get_answer`
resumed the same loop for a shorter budget. The owner's explicit rule for
this plugin: **a Dify tool call is one stateless HTTP round trip -- it
must never loop or sleep client-side.** `timeout=2` on one
`GET /api/v1/agent/updates` request is fine (that is salt-api holding the
connection briefly server-side, same as before); a client-side retry
loop wrapped around it is not, no matter the ceiling.

So `ask_human` now posts the card, makes exactly ONE
`check_for_answer` check (covering the rare case a human already tapped
by the time this runs), and otherwise returns `{"status": "pending",
"ask_id": ...}` immediately -- there is no more waiting at all.
`get_answer` mirrors this: at most one on-demand
`check_for_answer` call, never a resume-loop.

**Push makes this not feel slower.** Since there is no more clientside
wait, instant delivery has to come from somewhere else: Salt's own
webhook. `endpoints/salt_webhook.py` (a real Dify Endpoint, wired up via
`endpoints/salt_events.yaml` + `manifest.yaml`'s `plugins.endpoints`/
`resource.permission.endpoint.enabled`) verifies each inbound
`card_interaction` delivery and calls
`tools/_salt_common.py::apply_pushed_card_interaction`, which writes
`status: "answered"` straight into the SAME on-disk pending-ask record
`check_for_answer`'s on-demand path reads. `tools/register_webhook.py`
is the one-time setup step (`client.set_callback`) that points this
agent's Salt webhook at that endpoint's URL (shown on Dify's Endpoints
tab after install). Once that's done, `get_answer` resolves a tap
INSTANTLY off disk with zero Salt API calls -- see
`tools/get_answer.py`'s early `record.get("status") == "answered"`
check. Without it, `get_answer` still works, just purely on-demand: call
it again yourself to check.

**Why file-based, not in-memory.** Dify's serverless install method can
run a plugin's tools (and its endpoint!) across several worker
processes. If `ask_human` returns pending and a LATER `get_answer` call
(or the webhook delivery itself) lands on a different process, an
in-memory dict from `ask_human`'s process would simply not have the
entry. `tools/_salt_common.py`'s `save_pending_ask`/`load_pending_ask`/
`delete_pending_ask` persist the pending state to
`~/.salt/dify-plugin/asks/<card_id>.json` instead (0700 dir, 0600 files,
mirroring `saltapp.socket.default_state_dir`'s own convention), so any
process on the same machine -- tool or endpoint -- can read and write it.

**Why a shared `check_for_answer` helper.** `ask_human`'s initial look and
`get_answer`'s on-demand fallback are the exact same one-shot check with
a different starting cursor -- factored into `tools/_salt_common.py` once
rather than duplicated, per the same "one place the logic lives"
reasoning `saltapp.integrations._tools.SaltTools` uses for its six shared
actions.

**2026-09-26: `check_for_answer` stopped reading the shared per-agent
socket-mode outbox.** Until this pass, its one on-demand check was still
`GET /api/v1/agent/updates` -- the same outbox `saltapp.socket.SocketClient`
drains, verified via `who_am_i`'s cached webhook secret
(`get_webhook_secret`) and `saltapp.webhook.handle`. That outbox keeps
exactly ONE forward-only cursor PER AGENT server-side (`after=0` is the
same as omitting it; a lower `after` is silently ignored), so two
concurrent `ask_human`/`get_answer` checks for the same agent -- or an
ask running beside anything else consuming that agent's deliveries, such
as a real socket-mode client -- could each advance the SAME cursor and
silently steal each other's rows, stranding one of them pending forever.
This is the identical bug class fixed the same day in salt-mcp's
`getCard`/`pollForCardInteraction` and saltapp-agentkit's `ask_poll.py`.

`check_for_answer` now reads `GET /api/v1/cards/:id` instead
(`tools/_salt_common.py::get_card`) -- one card's own interaction log,
scoped to that card alone. Reading a card by id is idempotent and shares
no state with any other ask, this agent's other checks, or any other
consumer of this agent's outbox: any number of concurrent
`ask_human`/`get_answer` calls, for this agent or any other, now each
resolve their own ask independently. `saltapp.client.SaltClient` has no
public `get_card` method as of this writing (confirmed against
`saltapp-python` main, version 0.3.2) -- `get_card` goes through
`_request`, the same underlying method every public `SaltClient` call is
already built on, rather than adding one to a sibling package this repo
doesn't own.

One real consequence: `GET /api/v1/cards/:id`'s interaction rows carry
only `{id, user_id, action_id, value, created_at}` (plus
`transfer_request_id`/`_status` on a "pay" tap) -- never a full `user`
object or `account_type`, unlike the old outbox event body. Telling a
human's tap from an agent's therefore needs the chat's member list;
`tools/ask_human.py` fetches it ONCE, at ask time
(`_salt_common.get_chat_members_map`/`humans_by_id`), and persists the
resulting `humans` map in the pending-ask record alongside `action_map`,
so `get_answer`'s one on-demand check never needs a second chat read.
`get_webhook_secret`, `_verify_row`, and the socket-mode signature
verification machinery are gone from this on-demand path entirely (a
`GET /api/v1/cards/:id` call is an ordinary authenticated REST read, not
a signed webhook envelope needing verification) -- they were used for
NOTHING else in this plugin, so they were deleted rather than left as
dead code. The webhook PUSH path (`endpoints/salt_webhook.py`,
`apply_pushed_card_interaction`) is entirely unaffected: a real webhook
delivery already carries the full `user` object
(`CardInteractionJob#perform`'s payload), verified via the endpoint's own
`settings["webhook_secret"]`, never the outbox.

**429 handling.** A rate limit on the one-shot check is never retried and
never raised as a hard tool error -- `_salt_common.pending_with_retry_hint`
turns it into an ordinary `{"status": "pending", "ask_id": ...,
"retry_after_seconds": N, "hint": "..."}` result, so a caller waits and
tries `get_answer` again rather than being told the ask failed.

**Cross-identity safety check.** `get_answer` refuses (`"status":
"unknown"`) a pending record whose saved `host`/`agent_id` don't match
the CURRENT credentials it's running under, rather than silently checking
Salt with the wrong api key (which would just come back empty forever,
looking like "still pending" when actually it's "wrong account"). This
check runs before the pushed-answer shortcut, so a wrong-identity record
is refused even if some other process's webhook already answered it.

**Options/buttons as a comma-separated string, not a structured list.**
Dify's classic tool parameter schema has no native "list of strings" the
way it has `string`/`number`/`boolean`; `array[string]` exists in an
`output_schema` (structured OUTPUT), not as an LLM-fillable `parameters`
entry. The verified, real-world pattern for this (see e.g.
`dify-official-plugins/tools/hackernews/tools/get_multiple_users.py`'s
`usernames` parameter) is a comma-separated string, parsed defensively
(also accepting an already-parsed list, in case some Dify variant ever
hands one over). `tools/_salt_common.py`'s `parse_options` does exactly
that, used by `ask_human`'s `options`, `post_card`'s `buttons`, and
`interests`'s `keywords`.

**Conditional-required, validated in code.** `interests`'s `mode`
parameter is only required when `action="set"` -- Dify's classic tool
parameter schema has no "required only if another field equals X" (its
`show_on` is display-only, not a validation rule), so `interests.py`
checks this itself and yields a clear text message rather than relying
on schema-level enforcement that doesn't exist.

**`line_items` as a JSON string.** Same schema gap, different shape: a
list of records (not scalars) has to be JSON, not comma-separated.
`parse_line_items` accepts a JSON string (or, defensively, an
already-parsed list) and validates the four required keys are present;
`tools/send_invoice.py` itself checks `qty * unit_price == subtotal` per
item and that every subtotal sums to `amount`, matching salt-api's own
`Product::AMOUNT_RE`-style validation described in the workspace
`CLAUDE.md` and `saltapp.cards`' button validator comments.

## The keyless boundary

None of these tools ever decrypt a message -- Ask Human/Get Answer read a
card tap's metadata (who tapped, which `action_id`), never message text;
Send Message only ever needs OTHER members' public keys (fetched live
from the chat) to encrypt outgoing text, never this agent's own private
key; Read Room reads whatever salt-api hands back for an OPEN room, which
is plain text by construction (`encrypted: false`), so there is nothing
to decrypt there either. That is why every credential except `host` is
optional now: a private key is only useful the moment a future tool needs
to read incoming ciphertext (see "Known limitations" below), and this
release doesn't have one. Keep this property if you add a tool: check
whether it genuinely needs to decrypt something before wiring the private
key into it, and if it doesn't, don't ask for it.

**`agent_id`/`api_key` went from required to optional in the open-rooms
release.** `read_room` is the one tool that works with neither -- an
anonymous read of a `public && !encrypted` room, via `SaltClient.get_chat`
sending no `api-key` header at all when the credential is blank (see
`saltapp-python`'s own docstring on that). Every other tool still needs a
real `api_key` (and most need `agent_id` too, to know whose identity
they're acting as) and checks for it itself with a clear message rather
than letting a blank credential 401 opaquely -- see `interests.py` and
`register_webhook.py` for the pattern, and `_salt_common.client_and_identity`'s
docstring for why it reads credentials with `.get(...) or ""` rather than
`credentials[...]`.

## Known limitations

- **Pending-ask files are never garbage-collected.** A card nobody ever
  taps (the human left, the chat was archived, the agent's flow was
  abandoned) leaves its JSON file under `~/.salt/dify-plugin/asks/`
  forever. This is a real gap, not an oversight rationalized away: fixing
  it would need either a TTL sweep on `ask_human`/`get_answer` invocation
  (cheap, but only prunes on the next call, not proactively) or a
  separate cron-like mechanism Dify plugins don't have a first-class way
  to run. Left undone here; a future pass should add at least the sweep.
- **The module-level `_client_cache` is per-process, not shared across
  Dify's worker processes**, unlike the pending-ask file store. Each
  process opens its own `SaltClient`/httpx connection pool per host
  rather than truly sharing one -- a minor efficiency loss, not a
  correctness bug. (This bullet used to also cover a `_webhook_secret_cache`
  and, before that, a socket-mode cursor cache `get_cursor`/`set_cursor` --
  both are gone entirely as of the 2026-09-26 card-polling fix: nothing
  in this plugin resolves or caches a webhook secret for the on-demand
  check any more, since `GET /api/v1/cards/:id` is an ordinary
  authenticated REST read, not a signed envelope needing verification;
  each pending ask carries its own `after` cursor -- scoped to its own
  card, never shared -- in its file, so there is no module-level cursor
  to worry about being process-local either.)
- **No end-to-end test against a live salt-api, including the new
  webhook endpoint.** Every test in `tests/` mocks
  `saltapp.client.SaltClient` (never a real network call);
  `send_message`'s tests use real PGP round-trip crypto,
  `ask_human`/`get_answer`'s tests assert the exact `GET
  /api/v1/cards/:id` call count and its `after` cursor
  (`FakeSaltClient._request`, since the real `SaltClient` has no named
  `get_card` method to stub -- see `tests/fakes.py`), and
  `test_webhook_endpoint.py` builds a real werkzeug `Request` with a
  genuinely HMAC-signed body and calls `SaltWebhookEndpoint._invoke`
  directly -- but nobody has installed this
  plugin into a real Dify instance, copied its real Endpoints URL into
  `register_webhook`, and watched a real Salt webhook delivery land yet.
  See `HANDOFF.md` for the UAT steps to do that by hand.
- **No `Idempotency-Key`** is set on `send_invoice`'s `create_invoice`
  call, even though `SaltClient.create_invoice` accepts one -- and per
  that method's own docstring (ported from `client.ts`), salt-api does
  NOT dedupe on it for this endpoint anyway, so a caller-side retry is
  still the caller's own responsibility either way. Not added here since
  a Dify tool call has no natural retry key to reuse across invocations.

## Testing

```bash
cd saltapp-dify-plugin
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
pytest
```

Every test constructs a REAL `dify_plugin.Tool` subclass instance the
same way `dify-official-plugins/tools/google/tests/test_google.py` does
(`object.__new__(cls)` to skip `Tool.__init__`, which needs a live plugin
runtime session; `response_type`/`runtime` set by hand instead -- see
`tests/fakes.py::make_tool`), and only fakes `saltapp.client.SaltClient`
(via `tests/fakes.py::FakeSaltClient`, installed by the `fake_client`
fixture in `tests/conftest.py`). Since the real `SaltClient` has no
public `get_card` method (see `_salt_common.get_card`'s docstring),
`FakeSaltClient._request` parses the card id/`after` straight out of the
path it's given so a test can `.stub("get_card", ...)` and assert on
`c[1]["after"]` exactly like every other named method on the fake; any
other `_request` call is an `AssertionError`, on purpose (this plugin
only ever calls `_request` for `get_card`). `tests/test_webhook_endpoint.py`
uses the same `object.__new__` trick for the real `SaltWebhookEndpoint`
(its `Endpoint.__init__` is `@final` and needs a live `Session`;
`_invoke` never touches `self.session`, so this is safe), and builds a
real werkzeug `Request` via `werkzeug.test.EnvironBuilder` with a
genuinely HMAC-signed body, verified through `saltapp.webhook`'s real
signature check -- that machinery now lives ONLY in this one test file
(and in `endpoints/salt_webhook.py` itself): `ask_human`/`get_answer`'s
own tests stopped needing it as of the 2026-09-26 card-polling fix, since
a `GET /api/v1/cards/:id` read is an ordinary authenticated REST call,
never a signed envelope (the now-removed `tests/webhook_helpers.py::signed_update_row`
built the old socket-mode row's HMAC; nothing calls it any more).
`test_send_message.py` round-trips real PGP keypairs -- never stubbed.
