# HANDOFF

## 2026-09-26 ask_human/get_answer poll the card, not the shared agent outbox (lane/card-poll)

Branch `lane/card-poll`. Cross-repo bug: this plugin's `ask_human`/
`get_answer` waited for a human's answer by reading the agent's
socket-mode outbox (`GET /api/v1/agent/updates` -- see
`_salt_common.py::check_for_answer`, as it stood before this pass). That
outbox keeps exactly ONE forward-only cursor PER AGENT server-side
(`after=0` is the same as omitting it entirely; a lower `after` is
silently ignored). A stateless tool call polling it therefore (a) races
every other consumer of that same agent's deliveries -- two concurrent
`ask_human`/`get_answer` checks (or an ask running beside a real
socket-mode client on the same agent) could each advance the SAME cursor
and silently steal each other's rows, stranding one of them pending
forever -- and (b) any `after=` this plugin sent advanced the agent's
server-side ack for good, for every OTHER consumer of that agent's
outbox too. Identical fix landed the same day in salt-mcp
(`getCard`/`pollForCardInteraction`) and saltapp-agentkit
(`ask_poll.py`); this plugin was the third and last of the three.

**The fix.** `check_for_answer` now reads `GET /api/v1/cards/:id`
instead (new `tools/_salt_common.py::get_card`) -- one card's own
interaction log, scoped to that card alone, idempotent, sharing no state
with any other ask. `saltapp.client.SaltClient` has no public `get_card`
method as of this writing (confirmed against `saltapp-python` main,
0.3.2) -- `get_card` goes through `client._request(...)`, the same
underlying method every public `SaltClient` call is already built on,
mirroring saltapp-agentkit's identical choice (see that repo's
`ask_poll.py::_get_card` docstring) rather than editing a sibling package
this repo doesn't own. The design otherwise stayed exactly what it was
(single on-demand check, no retry loop, file-based pending-ask store for
cross-worker-process visibility and webhook push) -- only WHAT
`check_for_answer` reads changed, not the shape of the fix around it;
see "Deviation from the task spec" below for why this plugin did NOT
also move to a fully self-contained `ask_id` token the way the other two
references did.

Concurrency claim, now true: this plugin polls its own card; nothing is
shared; unlimited concurrent asks per agent (each `ask_human`/`get_answer`
call reads and advances only its own card's own interaction cursor).

**A second, independent bug found and fixed along the way**:
`extract_card_id` read `response.get("id") or response.get("card_id")`
-- but a real `POST /api/v1/cards` response (salt-api 0.96.1,
`Api::V1::CardsController#create`) has NO top-level `id` at all, only
`message_id` (the chat bubble) and `resource_id`/`resource.id` (the
card itself). This plugin's own test fakes had stubbed a fictional
`{"id": "card-123"}` shape from day one, which is exactly how the bug
went unnoticed -- the same bug the task coordinator flagged as present
in saltapp-agentkit's mocks too. Fixed `extract_card_id` to read
`resource_id`/`resource.id`, and every test fake now models the real
response shape (`{message_id, resource_id, resource: {id, ...}}`).

**Human-vs-agent filtering, without the outbox's free `user` object.**
`GET /api/v1/cards/:id`'s interaction rows carry only `{id, user_id,
action_id, value, created_at}` (plus `transfer_request_id`/`_status` on
a "pay" tap; see `CardInteraction#as_json_for_owner`) -- never a full
`user` object or `account_type`, unlike the old outbox event body
(`CardInteractionJob#perform`'s payload). This plugin's `ask_human` asks
in a CHAT, not of one named human (no `to`/`human_id` parameter, unlike
salt-mcp's `askHuman`/saltapp-agentkit's `salt_ask_human` -- a
deliberate, pre-existing difference in this plugin's own contract, left
untouched), so telling a human's tap from an agent's needs the whole
chat's member list, not one comparison. New `tools/_salt_common.py::humans_by_id`/
`get_chat_members_map`: `ask_human` fetches the chat's members ONCE (one
new `get_chat` call it didn't make before) and persists the resulting
`humans` map (non-agent members only, keyed by lowercased id) in the
pending-ask record alongside `action_map`, so `get_answer`'s one
on-demand check never needs a second chat read -- it stays exactly one
`GET /api/v1/cards/:id` call, per the owner's no-polling rule.

**429 handling**, newly explicit: a rate limit on the one-shot check
(`SaltApiError` with `status == 429`) is never retried and never
surfaced as a hard tool error in `ask_human`/`get_answer` -- new
`_salt_common.py::pending_with_retry_hint` turns it into an ordinary
`{"status": "pending", "ask_id": ..., "retry_after_seconds": N, "hint":
"..."}` result (only when Salt sent a `Retry-After`), so a caller waits
and tries `get_answer` again instead of being told the ask failed.
`ask_human` still persists the pending-ask record on a 429 (the card was
already posted -- losing the `ask_id` here would strand it).

**Deviation from the task spec.** The task's `ask_id` shape
(`{v, chat_id, card_id, message_id, human_id, options/actionMap, after}`,
self-contained, no server-side state) matches salt-mcp/saltapp-agentkit,
which have no push feature and no reason to persist anything between
calls. This plugin's `ask_id` stayed `card_id`, backed by the existing
on-disk pending-ask file (`~/.salt/dify-plugin/asks/<card_id>.json`), on
purpose: this plugin's whole "Push vs. on-demand" design (see README.md)
depends on `endpoints/salt_webhook.py` being able to write a REAL
webhook's answer into a record `get_answer` can find later, from a
DIFFERENT worker process, without that process having seen the original
`ask_human` call at all -- a self-contained token `ask_human` alone knows
about cannot receive an update from an event that arrives afterward, in
a different process, addressed only by card id. The bug being fixed (a
SHARED per-agent cursor) is orthogonal to this: the per-card file's own
`cursor` field was never shared across asks either, before or after this
fix -- what made the OLD design unsafe was the RESOURCE it pointed the
cursor at (the shared outbox), not where the cursor value itself lived.
Redirecting `check_for_answer` at `GET /api/v1/cards/:id` (scoped to one
card) removes the actual hazard while keeping the file store, which
remains load-bearing for push. Flagged here explicitly rather than
silently narrowing the task.

**Files changed**: `tools/_salt_common.py` (module docstring; removed
`get_webhook_secret`/`_webhook_secret_cache`/`_cache_key`/`_verify_row`/
`SOCKET_SIGNATURE_TOLERANCE_SECONDS`/`POLL_ROUND_TIMEOUT` and the
`saltapp.webhook` import entirely; added `get_card`, `humans_by_id`,
`get_chat_members_map`, `pending_with_retry_hint`; rewrote
`check_for_answer`'s signature and body; fixed `extract_card_id`),
`tools/ask_human.py` (fetches `humans`, no more `get_webhook_secret`,
429 handling), `tools/get_answer.py` (same), `tests/fakes.py`
(`FakeSaltClient._request` replaces `get_agent_updates`; parses
`get_card`'s path/`after`), `tests/conftest.py` (`_webhook_secret_cache`
cleanup removed), `tests/test_ask_human.py`/`test_get_answer.py`
(rewritten for card reads; real `post_card`/`get_chat` response shapes;
new 429 tests), `tests/test_webhook_endpoint.py` (docstring only --
this test itself needed no changes, since real webhook delivery never
touched the outbox), README.md, AGENTS.md.

**Files added**: `tests/test_check_for_answer.py` (direct unit tests of
`check_for_answer`/`get_card`: tap answered, an agent's tap ignored, a
human's tap resolves even with an agent's tap ahead of it in the same
check, an unrelated action_id ignored, `after` threaded through, a
garbage `after` still answers per salt-api's fail-open contract, a pay
tap's `transfer_request_id`/`_status` ride along, a 429 propagates with
`retry_after` intact, and a `hasattr` check pinning that `FakeSaltClient`
has no `get_agent_updates` method at all any more), `tests/test_salt_common_cards.py`
(`extract_card_id`'s real-shape parsing and its former bug reproduced
against the real shape; `humans_by_id`/`get_chat_members_map`).

**Files removed**: `tests/webhook_helpers.py` (`signed_update_row` built
socket-mode outbox rows' HMAC signature; nothing reads the outbox any
more, so nothing called it -- `test_webhook_endpoint.py` has its own
local signing helper for a real webhook POST body and never depended on
this file).

**Tests**: 60 -> 80 passing (`.venv/bin/python -m pytest -q`):

```
$ .venv/bin/python -m pytest -q
................................................................................ [100%]
80 passed, 19 warnings in 1.06s
```

(Confirmed the 60-passing baseline by `git stash`-ing the tracked
changes and re-running with only the new, untracked test files present:
17 failures from the new tests against the OLD `_salt_common.py` -- i.e.
the new tests genuinely exercise the fix, not just the surface area --
plus the original 60 passing.)

**No-outbox grep, confirmed**:

```
$ grep -rn "agent/updates" tools/ endpoints/
tools/_salt_common.py:28:/api/v1/agent/updates` (the socket-mode outbox) at all.** That outbox
tools/_salt_common.py:264:# THIS NO LONGER READS `GET /api/v1/agent/updates` (the socket-mode
tools/_salt_common.py:282:    Replaces the old outbox poll (`GET /api/v1/agent/updates`): that
```

Every hit is a comment explaining the removal; zero functional reads.
There was never a standalone "read my deliveries" tool in this plugin to
remove or warn on -- `get_agent_updates` was called from exactly one
place (`check_for_answer`), never exposed as its own Dify tool.

**Left undone / out of scope**: no live run against a real salt-api (same
standing gap as every prior pass); the pre-existing "Pending-ask files
are never garbage-collected" bullet in `AGENTS.md`'s "Known limitations"
is stale (a GC pass shipped in the 2026-09-22 alignment pass, see that
entry below) but fixing that unrelated doc drift was out of scope for
this pass and is left for whoever touches that section next. Did not
push this branch (per the task's instructions) and did not touch any
other repo.

## 2026-09-22 open rooms, interests, no-polling push (lane/open-rooms)

Branch `lane/open-rooms`. Builds against `saltapp-python`'s own
`lane/open-rooms` branch (version 0.2.0 -- open rooms, interests, and
`get_chat`'s `last=` catch-up cursor; see that repo's `CHANGELOG.md`
"0.2.0" entry). Three things, per the task coordinator's spec:

**1. Open rooms.** `send_message` now branches on the chat's
`session.encrypted`: an open (`encrypted: false`) room gets a plain-text
`post_plain_message` call instead of PGP encrypt-and-post; the member
list, when needed, is read straight off `session.users` (falling back to
a real `get_chat_members` call only if that key is missing), saving a
round trip. `request_payment`/`send_invoice`/`post_card` were checked
against `saltapp-python/src/saltapp/client.py` and confirmed to have no
`encrypted` branching at all (they ride separate, never-PGP rails) --
left unchanged, as specified. New `read_room` tool: a thin wrapper around
`get_chat` that works with a blank/missing `api_key` (sends no api-key
header at all, per `SaltClient._request`'s own docstring), enough to read
a `public && !encrypted` room anonymously.

**2. Interests.** New `interests` tool: `action` set/get/clear against
`GET`/`PUT`/`DELETE /api/v1/chats/:id/subscription`; `mode` is required
only when `action="set"` (validated in code -- Dify's classic schema has
no conditional-required), `keywords` only matters when `mode="keywords"`.

**3. No polling, ever -- replaced with a real Dify Endpoint.** The
owner's explicit rule via the task coordinator: *"DO NOT USE POLLING as a
mechanic EVER... they must never loop or sleep. Receiving is by webhook...
otherwise reads are ON DEMAND with a cursor."* `poll_for_answer`'s
`while True` + `time.sleep()` loop is gone. `ask_human` now posts the
card, makes exactly ONE `check_for_answer` check (covers the rare
already-answered-instantly case), and otherwise returns pending
immediately -- `timeout_seconds` is removed from the tool entirely, there
is nothing left to wait for. `get_answer` makes at most one on-demand
check too. Instant delivery instead comes from a real Dify Endpoint,
`endpoints/salt_webhook.py` (`endpoints/salt_events.yaml` declares its
own `webhook_secret` settings form, separate from the tool provider's
credentials; `manifest.yaml` wires `plugins.endpoints` and
`resource.permission.endpoint.enabled`): it verifies each inbound
`card_interaction` delivery and calls the new
`apply_pushed_card_interaction`, which writes `status: "answered"`
straight into the SAME on-disk pending-ask file `check_for_answer`'s
on-demand path reads. New `register_webhook` tool is the one-time setup
(`client.set_callback`) that points an agent's Salt webhook at that
endpoint's URL. Once registered, `get_answer` resolves a tap **instantly,
with zero Salt API calls** by checking `record["status"] == "answered"`
before ever touching the network; without it, `get_answer` still works
purely on-demand.

**Other changes**: `agent_id`/`api_key` are now optional provider
credentials (`read_room` needs neither); `SaltProvider._validate_credentials`
only runs the `who_am_i` identity check when an `api_key` was actually
given, so a host-only provider validates. `tools/_salt_common.py`'s
`client_and_identity` reads credentials with `.get(...) or ""` instead of
`credentials[...]`, so a blank/missing `agent_id`/`api_key` never raises
a `KeyError`; every tool that genuinely needs a real `api_key`
(`interests`, `register_webhook`) checks for it itself and gives a plain
"An API key is required..." message rather than letting a blank key 401
opaquely. The now-unused module-level socket cursor cache
(`get_cursor`/`set_cursor`/`_cursor_cache`) was removed along with the
poll loop it existed to support -- each pending-ask file carries its own
cursor instead. `manifest.yaml` bumped `0.0.1` -> `0.1.0` (first real
feature release beyond the initial scaffold).

**Files added**: `tools/read_room.py`/`.yaml`, `tools/interests.py`/
`.yaml`, `tools/register_webhook.py`/`.yaml`, `endpoints/salt_events.yaml`,
`endpoints/salt_webhook.yaml`, `endpoints/salt_webhook.py`,
`tests/test_read_room.py`, `tests/test_interests.py`,
`tests/test_register_webhook.py`, `tests/test_webhook_endpoint.py`.

**Files changed**: `requirements.txt` (documents the `saltapp>=0.2.0`
floor in a comment -- PEP 508 forbids combining a version specifier with
a direct URL reference, so the pin itself is untouched), `provider/salt.py`,
`provider/salt.yaml`, `manifest.yaml`, `tools/_salt_common.py` (send_message's
open-room branch, `read_room`, `check_for_answer` replacing
`poll_for_answer`, `apply_pushed_card_interaction`, `client_and_identity`
hardening, cursor-cache removal), `tools/ask_human.py`/`.yaml` (no more
`timeout_seconds`, one-shot check), `tools/get_answer.py`/`.yaml`
(pushed-answer shortcut, one-shot fallback), `tests/fakes.py`
(`FakeSaltClient` gained `get_chat(last=)`, `post_plain_message`,
`get_chat_subscription`/`set_chat_subscription`/`clear_chat_subscription`,
`set_callback`), `tests/conftest.py` (cursor-cache cleanup removed),
`tests/test_ask_human.py`/`test_get_answer.py` (rewritten for the
one-shot/push semantics, asserting exact `get_agent_updates` call counts
to prove there's no loop), `tests/test_send_message.py` (open-room case
added), `tests/test_provider.py` (host-only credentials case added),
`README.md`, `AGENTS.md`.

**Tests**: 37 -> 60 passing (`.venv/bin/python -m pytest -q`):

```
$ .venv/bin/python -m pytest -q
............................................................             [100%]
60 passed, 19 warnings in 1.3s
```

**No-polling grep, confirmed**: `grep -rn "sleep" tools/ endpoints/` finds
only docstring/comment prose explaining the absence of a loop (e.g. "never
retries or sleeps") -- zero actual `time.sleep()`/`asyncio.sleep()` calls
and zero `while`/`for`-based retry loops anywhere in `tools/` or
`endpoints/`.

**Deviations from the spec, and why**:
- The spec's `ask_human` rewrite said "then ALWAYS save the pending-ask
  record and return pending (or the answered shape if that one-shot
  happened to match)" -- read literally this could mean persisting a
  pending-ask file even on an instant answer. That was NOT done: an
  instantly-answered ask returns and is never saved, matching the
  original design (`get_answer` deletes a pending file the moment it
  sees "answered", so a file that starts "answered" would just be
  immediately-stale). Interpreted "ALWAYS" as describing the now-dominant
  case (the one-shot rarely matches, so the pending path is what
  "always" happens now), not as a literal instruction to persist
  redundant answered-and-done state.
- `check_for_answer`'s guard in `apply_pushed_card_interaction` against
  `record.get("status") == "answered"` (skip re-applying a push to an
  already-answered record) wasn't explicitly asked for, but was added as
  a small idempotency guard against Salt redelivering the same webhook
  (the workspace's own note on `X-Salt-Delivery-Id` retries) -- without
  it, a redelivered `card_interaction` for an already-resolved ask would
  silently overwrite `answer`/`user` with (in practice identical, but not
  guaranteed) data a second time.
- Removed `get_cursor`/`set_cursor`/`_cursor_cache` entirely rather than
  leaving them as unused dead code -- the spec's deletion list didn't
  name them, but nothing calls them after the rewrite (ask_human no
  longer starts from a cached module-level cursor; each pending-ask file
  carries its own cursor instead), and `AGENTS.md`'s "Known limitations"
  bullet about them would have gone stale if they'd stayed.
- `read_room.yaml`'s `last` parameter is typed `string`, not `number`:
  matches every other id-like parameter in this plugin (`chat_id`,
  `ask_id`, `transfer_id`) and avoids any float-vs-int coercion risk on a
  cursor value passed straight into a query string.

**Left undone / blocked** (same standing gaps as before, none newly
introduced by this pass): no live run against a real salt-api or a real
Dify instance (so the webhook endpoint has never received an actual
delivery from Salt, only a synthetic signed request in
`test_webhook_endpoint.py`); pending-ask files still rely on
`save_pending_ask`'s opportunistic GC, not a real scheduler; this
plugin's source still has no public repository. The "UAT steps" section
further down this file is from the PREVIOUS pass and still mostly
applies (steps 1-4, 6-8), except step 5's "tap within 50 seconds" no
longer describes `ask_human`'s behavior -- it returns immediately now.
Additional steps for this pass's new tools, to run after step 4 above:

9. Add `read_room` with the `chat_id` of a real public, unencrypted
   ("open") Salt room and NO api key configured on the provider (a
   second, host-only credential set, or temporarily blank the api key on
   the existing one) -- confirm it returns the room's recent messages
   with no 401/403.
10. Add `interests` with `action="set"`, `mode="keywords"`,
    `keywords="urgent, invoice"` against an open room this agent is a
    member of -- confirm `get_chat_subscription` (via `action="get"`)
    reflects it; then `action="clear"` and confirm it reverts to
    `"addressed"`.
11. After installing this plugin build into a real Dify instance, open
    its Endpoints tab, copy the shown URL, fill in the endpoint group's
    own `webhook_secret` setting (from `GET /api/v1/agents/webhook_secret`
    or Developers > Your agents on Salt -- note this is a SEPARATE
    settings form from the Salt tool provider's credentials), then call
    `register_webhook(webhook_url="<that URL>")`. Run `ask_human` again,
    tap the button in the Salt app, and confirm a SUBSEQUENT `get_answer`
    call resolves instantly (check CloudWatch/local logs, if reachable,
    to confirm no `GET /api/v1/agent/updates` call was made for that
    specific `get_answer` invocation).

---

## 2026-09-22 alignment pass (round-4 socket contract, real brand icon, GC)

- **Fixed a real round-3/4 contract violation**: `tools/_salt_common.py`'s
  `SOCKET_SIGNATURE_TOLERANCE_SECONDS` was still the pre-round-4 widened
  value (7 days + 1h), matching `saltapp.socket`'s own (still unfixed as of
  this pass) constant. LANES.md's "fix A" (serve-time signing) means
  salt-api now re-signs every outbox row fresh at the moment it's actually
  served, so a row that sat unpolled for the full 7-day retention window
  verifies with a signature timestamped as if written just now -- the
  standard ~300s tolerance is correct and sufficient, and the wide one is a
  real weakness (it would accept a signature far older than any genuine
  serve-time one could be). Fixed to `300`.
- **This plugin's own poll loop (`poll_for_answer`) was already correctly
  aligned**: `POLL_ROUND_TIMEOUT = 2` (already noted "clamps to 0..2s
  anyway"), 1s between rounds, bounded by a real deadline (max 50s for
  `ask_human`, 8s for `get_answer`) -- no changes needed there. NOT fixed
  (out of this repo's scope): `saltapp.client.SaltClient.get_agent_updates`
  (in the separate `saltapp-python` package this plugin depends on via git)
  still always sends `after=<cursor>` literally, including `after=0` on a
  fresh cursor, instead of omitting it so salt-api's server-side ack
  applies. Flagged to the coordinator; not this repo's file to fix.
- **Real brand icon**: `_assets/icon.svg` was a hand-drawn approximation
  (rounded-rect tile, wrong shape); it's now an exact copy of
  `salt-fe/brand/salt-tile.svg`.
- **Pending-ask garbage collection** (`gc_stale_pending_asks`, new): a Dify
  tool plugin has no scheduler of its own, so this runs opportunistically
  inside `save_pending_ask` -- every time a new ask is saved, any pending-ask
  file older than 24h (`STALE_ASK_MAX_AGE_SECONDS`) is swept. 5 new tests in
  `tests/test_pending_ask_gc.py`.
- 32 -> 37 tests passing (`pytest`, via `.venv`).

---

## What this is

A brand-new repository (`saltapp-dify-plugin`), built from scratch in
this pass -- there is no prior version and no "migration" concept here.
Everything below is new.

## What changed / files

```
saltapp-dify-plugin/
  manifest.yaml              # plugin identity, permissions, runner
  main.py                    # Plugin() / plugin.run()
  requirements.txt           # dify_plugin + saltapp (from GitHub, saltapp-python isn't on PyPI yet)
  .env.example               # remote-debug install vars
  .python-version            # 3.12
  .gitignore / .difyignore
  provider/
    salt.yaml                # identity + 5 credentials + tools list
    salt.py                  # SaltProvider._validate_credentials
  tools/
    send_message.yaml / .py
    ask_human.yaml / .py
    get_answer.yaml / .py
    post_card.yaml / .py
    request_payment.yaml / .py
    send_invoice.yaml / .py
    get_payment_status.yaml / .py
    _salt_common.py          # shared client/cache/poll-loop/pending-ask-store helpers
  tests/
    __init__.py
    conftest.py               # cache isolation, fake state dir, POLL_ROUND_SLEEP_SECONDS speedup
    fakes.py                  # FakeSaltClient, make_tool, json_messages/text_messages
    webhook_helpers.py         # signed_update_row (real HMAC, for tests)
    test_send_message.py
    test_ask_human.py
    test_get_answer.py
    test_post_card.py
    test_request_payment.py
    test_send_invoice.py
    test_get_payment_status.py
    test_provider.py
  _assets/
    icon.svg
  README.md
  PRIVACY.md
  AGENTS.md
  HANDOFF.md                  # this file
```

32 tests across 8 files, all real assertions (real PGP round-trip crypto
for `send_message`, real HMAC-signature verification for `ask_human`/
`get_answer`, a fake only for the Salt HTTP client itself). See
`AGENTS.md`'s "Testing" section for the exact pattern used.

## How to test

```bash
cd saltapp-dify-plugin
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
pytest -q
```

Real command run for this handoff, real output:

```
$ python -m pytest -q
................................                                         [100%]
32 passed, 19 warnings in 1.57s
```

(The warnings are all upstream deprecation notices from `pgpy`/`dify_plugin`'s
own dependencies -- `imghdr`, `TripleDES`/`Camellia` cipher module paths,
a gevent monkey-patch timing notice, and a Pydantic v2 migration notice.
None are from this plugin's own code, and none affect correctness.)

## Dify CLI

Installed successfully via `brew tap langgenius/dify && brew install dify`
(v0.6.10; the tap needed an explicit `brew trust --formula
langgenius/dify/dify` first -- this machine's Homebrew refuses untrusted
taps by default, a local safety gate, not a warning about the tap
itself).

Used `dify plugin init --quick` once, in a throwaway scratch directory, to
confirm the real scaffold's conventions (manifest shape, credential
`author` field's actual constraint -- lowercase/digits/dash/underscore
only, confirmed empirically against the CLI, which is why every
`author:` field in this plugin is `"0x0000f8"`, not the fuller
`0x0000F8 <0000F8@proton.me>` form used in git commit trailers and
`README.md`'s contact line) -- then wrote every real file in this repo by
hand against that ground truth, not by editing the scaffold's generated
files.

`dify plugin package .` succeeds:

```
$ dify plugin package . -o /tmp/salt-plugin-test.difypkg
2026/09/18 03:20:45 INFO plugin packaged successfully output_path=/tmp/salt-plugin-test.difypkg
```

`dify plugin checksum .` and `dify plugin readme list .` also run clean
against the finished repo (the earlier `readme list` failure during
development was simply because `README.md` didn't exist yet at that
point in the build).

## Marketplace submission steps (verified against the real repo)

Not done in this pass (the task said not to push, open a PR, or create a
GitHub repo) -- these are the real steps, cited from
`https://raw.githubusercontent.com/langgenius/dify-plugins/main/README.md`
(fetched and read directly, not from memory), for whoever does this next:

1. Package: `dify plugin package .` -> one `.difypkg` file.
2. Fork `https://github.com/langgenius/dify-plugins`.
3. Create an organization directory, then a subdirectory named after this
   plugin (e.g. `<org>/salt/`); place the source and the `.difypkg` there
   (multiple versions can share the directory).
4. Open a PR against `langgenius/dify-plugins` following their PR
   template; wait for review.
5. Once approved and merged, it lists automatically on
   https://marketplace.dify.ai/.

Blocking items before that PR could actually go out, beyond "don't do it
yet": this plugin's own source isn't in a public repository yet (the task
scope stopped at a local git repo), and the marketplace's own submission
checklist (surfaced by `dify plugin init`'s generated `GUIDE.md`, read
during this build) additionally wants the `_assets/icon.svg` replaced
with something more considered than this pass's placeholder (a plain
blue rounded square with a simplified salt-grain glyph -- deliberately
generic, not Salt's real brand mark, so as not to imply this is an
official Salt-authored plugin) and the plugin version bumped off `0.0.1`
once it has actually shipped once.

## What's new (candidate line, in Salt's own trust-conventional style)

Per the workspace's `feedback_whats_new_users_only` memory, an internal
tool like this wouldn't get a line in Salt's own in-app changelog (it's
not user-facing inside the Salt app) -- if this ships as a developer
announcement instead, something like:

> Salt agents can now run inside Dify: send messages, ask a question and
> wait for the answer, request or invoice payment, and post interactive
> cards, all from a Dify workflow or agent.

## UAT steps (against a real Salt account)

1. On Salt (a real account, or a test one named `SALT-...`/
   `salt-...@example.test` per this workspace's own convention), register
   an agent: Developers > Your agents > create one. Note its agent id and
   api key.
2. Install this plugin into a Dify instance (local dev: `cp .env.example
   .env`, fill in `REMOTE_INSTALL_URL`/`REMOTE_INSTALL_KEY` from Dify's
   plugin debugging page, `python -m main`; or package + upload).
3. Configure the Salt provider credentials: host `https://saltapp.ai`,
   the agent id and api key from step 1. Leave the private key blank for
   a first pass (keyless).
4. In a Dify workflow or agent, add the `post_card` tool and call it with
   a `chat_id` from a real Salt chat this agent is a member of, and some
   `text`. Confirm the card appears in that chat in the Salt app.
5. Add `ask_human` with the same `chat_id`, a `question`, and
   `options="Yes, No"`. Run it, then tap a button in the Salt app within
   50 seconds -- confirm the tool returns the answered shape with the
   right `answer`/`action_id`/`user`. Run it again and let it time out
   (don't tap) -- confirm it returns `{"status": "pending", "ask_id":
   ...}`, then call `get_answer` with that `ask_id` and tap the button
   before/while it runs -- confirm it resolves.
6. Add `send_message` with a `chat_id` and some `text`. Confirm the
   message appears in the Salt app, decrypted correctly, for a human
   member of that chat.
7. With a real wallet id on the agent's account, try `request_payment`
   and `send_invoice` (line_items summing to amount) into a chat with a
   real counterpart; confirm the Pay/invoice bubble appears in Salt, and
   that `get_payment_status` with the resulting transfer id (once paid)
   reports a confirmed status.
8. Only after the above: add a private key (from the same agent's PGP
   keypair) to the credentials and confirm nothing regresses -- this
   release has no tool that reads message text, so this step is mostly
   confirming `_validate_credentials`' PGP parse check accepts a real key.

## Left undone / blocked

- **No live run against a real salt-api** was performed in this pass
  (no live Salt account/agent was created or reachable from this
  environment) -- see `AGENTS.md`'s "Known limitations" and the UAT
  steps above for exactly what a human should run by hand before calling
  this production-ready.
- **Pending-ask files are never garbage-collected** (see `AGENTS.md`) --
  a real, stated gap, not silently rounded away.
- **This plugin's source has no public repository yet** and is not
  pushed anywhere, per the task's explicit scope -- marketplace
  submission needs that first (see the steps above).
- **The plugin icon is a generic placeholder**, not a considered design
  pass -- fine functionally, but the marketplace checklist expects the
  default template icon replaced with something more deliberate before a
  real submission.
- Nothing else was skipped, stubbed, or faked: every tool's Python file
  is real working logic against the real `saltapp` SDK, every test is a
  real assertion (no `.skip`/`.only`, no mocked-into-meaninglessness
  test), and the CLI packaging step actually ran and actually succeeded.
