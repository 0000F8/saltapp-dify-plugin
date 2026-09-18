# AGENTS.md

Notes for whoever maintains this plugin next.

## What this is

A Dify Tool plugin exposing seven of Salt's agent actions
(`send_message`, `ask_human`, `get_answer`, `post_card`,
`request_payment`, `send_invoice`, `get_payment_status`) as Dify tools, so
a Dify agent or workflow can act as a Salt agent. It is built on
[`saltapp`](https://github.com/0000F8/saltapp-python) (source at
`../saltapp-python` in this workspace), Salt's Python SDK -- read that
repo's `README.md`/`AGENTS.md` for the SDK's own ground truth before
changing anything here that touches it.

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
sequence directly: fetch chat members, encrypt for everyone but self
(matched by `agent_id`, case-insensitive), and best-effort also encrypt a
copy for self if this agent happens to appear in the member list with a
known public key. If it doesn't (e.g. the member list came back before
this agent's own key propagated, or an api-key-only setup that never
registered a public key at all), the send still succeeds; it just skips
the sender-copy step silently.

## The ask/poll design, and its file-based store

There is no server-side "wait for an answer" endpoint (see
`saltapp-python/AGENTS.md`'s note on `ctx.ask()` -- the same is true
here, for the same reason: no such endpoint exists yet). `ask_human`
builds this from primitives that DO exist: post a card, then short-poll
`GET /api/v1/agent/updates` (the same socket-mode contract
`saltapp.socket.SocketClient` uses) until either a human's tap on that
card arrives or a deadline passes.

**Why file-based, not in-memory.** Dify's serverless install method can
run a plugin's tools across several worker processes. If `ask_human`
gives up and a LATER `get_answer` call lands on a different process, an
in-memory dict from `ask_human`'s process would simply not have the
entry. `tools/_salt_common.py`'s `save_pending_ask`/`load_pending_ask`/
`delete_pending_ask` persist the pending state to
`~/.salt/dify-plugin/asks/<card_id>.json` instead (0700 dir, 0600 files,
mirroring `saltapp.socket.default_state_dir`'s own convention), so any
process on the same machine can resume it.

**Why a shared `poll_for_answer` helper.** `ask_human`'s initial wait and
`get_answer`'s resume are the same loop with a different deadline and
starting cursor -- factored into `tools/_salt_common.py` once rather than
duplicated, per the same "one place the logic lives" reasoning
`saltapp.integrations._tools.SaltTools` uses for its six shared actions.

**The one deliberate simplification versus `saltapp.socket.SocketClient`.**
That client has to handle a "secret not available yet" TRANSIENT
verification failure (halt without advancing the cursor, retry next
loop) versus a DEFINITIVE rejection (bad signature -- advance past it).
This plugin resolves and caches the webhook secret via `who_am_i` BEFORE
`poll_for_answer` ever starts polling (`get_webhook_secret`, cached per
host+api_key), so by the time a row is being verified, the secret is
already known-good -- there is no per-row "secret not available yet"
case to halt on here. Every row's verification is either a success or a
definitive rejection, and either way the cursor advances past it. See
`tools/_salt_common.py`'s `_verify_row`/`poll_for_answer` docstrings.

**Cross-identity safety check.** `get_answer` refuses (`"status":
"unknown"`) a pending record whose saved `host`/`agent_id` don't match
the CURRENT credentials it's running under, rather than silently polling
Salt with the wrong api key (which would just come back empty forever,
looking like "still pending" when actually it's "wrong account").

**Options/buttons as a comma-separated string, not a structured list.**
Dify's classic tool parameter schema has no native "list of strings" the
way it has `string`/`number`/`boolean`; `array[string]` exists in an
`output_schema` (structured OUTPUT), not as an LLM-fillable `parameters`
entry. The verified, real-world pattern for this (see e.g.
`dify-official-plugins/tools/hackernews/tools/get_multiple_users.py`'s
`usernames` parameter) is a comma-separated string, parsed defensively
(also accepting an already-parsed list, in case some Dify variant ever
hands one over). `tools/_salt_common.py`'s `parse_options` does exactly
that, used by both `ask_human`'s `options` and `post_card`'s `buttons`.

**`line_items` as a JSON string.** Same schema gap, different shape: a
list of records (not scalars) has to be JSON, not comma-separated.
`parse_line_items` accepts a JSON string (or, defensively, an
already-parsed list) and validates the four required keys are present;
`tools/send_invoice.py` itself checks `qty * unit_price == subtotal` per
item and that every subtotal sums to `amount`, matching salt-api's own
`Product::AMOUNT_RE`-style validation described in the workspace
`CLAUDE.md` and `saltapp.cards`' button validator comments.

## The keyless boundary

None of the seven tools in this release ever decrypt a message -- Ask
Human/Get Answer read a card tap's metadata (who tapped, which
`action_id`), never message text; Send Message only ever needs OTHER
members' public keys (fetched live from the chat) to encrypt outgoing
text, never this agent's own private key. That is why every credential
except `host`/`agent_id`/`api_key` is optional: a private key is only
useful the moment a future tool needs to read incoming message text (see
"Known limitations" below), and this release doesn't have one. Keep this
property if you add a tool: check whether it genuinely needs to decrypt
something before wiring the private key into it, and if it doesn't,
don't ask for it.

## Known limitations

- **Pending-ask files are never garbage-collected.** A card nobody ever
  taps (the human left, the chat was archived, the agent's flow was
  abandoned) leaves its JSON file under `~/.salt/dify-plugin/asks/`
  forever. This is a real gap, not an oversight rationalized away: fixing
  it would need either a TTL sweep on `ask_human`/`get_answer` invocation
  (cheap, but only prunes on the next call, not proactively) or a
  separate cron-like mechanism Dify plugins don't have a first-class way
  to run. Left undone here; a future pass should add at least the sweep.
- **The module-level caches (`_client_cache`, `_webhook_secret_cache`,
  `_cursor_cache`) are per-process, not shared across Dify's worker
  processes**, unlike the pending-ask file store. A `who_am_i` call (and
  the resulting webhook secret) gets re-resolved once per process rather
  than truly once per identity -- a minor efficiency loss, not a
  correctness bug, since `get_webhook_secret`/`get_cursor` all degrade
  gracefully to "resolve it again" rather than assuming a shared cache
  hit.
- **The socket-mode cursor (`get_cursor`/`set_cursor`) is also
  process-local**, unlike `saltapp.socket.FileCursorStore`. A brand-new
  worker process's first `ask_human` call starts polling from cursor 0
  instead of wherever a different process last left off. This is safe
  (it will just re-see, and correctly skip, already-resolved
  `card_interaction` rows that don't match its OWN `card_id` -- it never
  mis-fires on someone else's card), just not maximally efficient. Move
  it to a file, matching the pending-ask store's approach, if this
  becomes a real cost.
- **No end-to-end test against a live salt-api.** Every test in
  `tests/` mocks `saltapp.client.SaltClient` (never a real network call);
  `send_message`'s tests use real PGP round-trip crypto and
  `ask_human`/`get_answer`'s tests build genuinely HMAC-signed rows and
  verify them for real, but nobody has run this plugin against a running
  Salt deployment yet. See `HANDOFF.md` for the UAT steps to do that by
  hand.
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
fixture in `tests/conftest.py`). Crypto (`saltapp.crypto`) and webhook
signature verification (`saltapp.webhook`) are exercised for real in
every test that touches them -- `tests/webhook_helpers.py::signed_update_row`
computes a genuine HMAC, and `test_send_message.py` round-trips real PGP
keypairs -- neither is ever stubbed.
