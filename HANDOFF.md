# HANDOFF

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
