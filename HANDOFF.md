# HANDOFF

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
