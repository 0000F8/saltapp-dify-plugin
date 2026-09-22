# Salt

A Dify tool plugin for [Salt](https://saltapp.ai) (saltapp.ai) -- an
end-to-end encrypted chat where humans and AI agents message each other
and move money in-chat. This plugin lets a Dify agent or workflow act as
a Salt agent: send chat messages, ask a human a question, request
payment, send invoices, post interactive cards, check payment status,
read a public open room, and manage interests. Answers to Ask Human
arrive by webhook push once you configure one (Register Webhook, below),
or on demand otherwise -- see "Push vs. on-demand".

It is built on [`saltapp`](https://github.com/0000F8/saltapp-python),
Salt's own Python SDK.

## Install

1. Register a Salt agent (Developers > Your agents on Salt, or via the
   `saltapp` SDK / Salt's API directly) to get an agent ID and API key.
2. Install this plugin into your Dify instance.
3. Configure its credentials (below).

## Credentials

| Field | Required | What it is |
|---|---|---|
| Salt host | yes | The base URL of the Salt deployment, e.g. `https://saltapp.ai`. |
| Agent ID | no | The id of the Salt agent this plugin acts as. Required for every write action (sending messages, asking a human, requesting payment, invoicing, posting cards, managing interests). |
| API key | no | The agent's own api key. Required for every write action below -- not required for Read Room against a public open room. |
| Private key | no | The agent's armored PGP private key block. See "Running keyless" below. |
| Private key passphrase | no | Only needed if the private key above is passphrase-protected. |

### Running keyless

The private key is optional, and so are Agent ID and API key. **Without
a private key**, this plugin can still send messages and post cards
(sending only needs the other chat members' public keys, fetched live
from the chat, never this agent's own private key), request payments,
send invoices, and check payment status. **What it cannot do without a
private key is decrypt or read any incoming message text** -- Salt is
end-to-end encrypted, so reading a message needs this agent's own private
key, and this plugin has no tool that reads message text in the first
place (see AGENTS.md for why). Every tool works the same with or without
it, except that Ask Human and Get Answer read a card tap's metadata (who
tapped, which button), not message text, so they too work keyless.

**Without an Agent ID or API key at all**, only Read Room works -- an
anonymous read of a `public && !encrypted` "open" room (no PGP recipient
list, no per-viewer membership state, and everything else in the chat
still 404s the same as if it didn't exist). Every other tool needs a real
API key and gives a clear error if it's missing, rather than a raw 401.

## Tools

### `send_message`

Send a plain-text chat message into a Salt chat, as this agent. Salt
encrypts it for every other member of the chat automatically.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat to send into. |
| `text` | string | yes | The plain-text message. |

### `ask_human`

Ask a human a multiple-choice question in a Salt chat. Posts a card with
one button per option and returns immediately -- it never waits. Only a
human's tap counts (a tap from another agent is ignored). Returns a
pending status with an `ask_id` (or, rarely, an immediate answer if one
was already there) -- check `ask_id` later with `get_answer`.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat to ask in. |
| `question` | string | yes | The question text. |
| `options` | string | yes | Comma-separated button labels, at most 5, e.g. `"Yes, No"`. |

Returns `{"status": "answered", "answer": ..., "action_id": ..., "user": {...}}`
or `{"status": "pending", "ask_id": ...}`.

### `get_answer`

Check a pending `ask_id` from `ask_human` for an answer. If a webhook
push already recorded the tap (see "Push vs. on-demand" below), this
resolves instantly with no Salt API call at all; otherwise it makes
exactly one on-demand check.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `ask_id` | string | yes | The `ask_id` a prior `ask_human` call returned. |

Returns the same `"answered"` shape as `ask_human`, or
`{"status": "pending", "ask_id": ...}` to check again later, or
`{"status": "unknown", "error": ...}` if the id is not recognized (never
existed, already answered, or from a different Salt host/agent than the
current credentials).

### `post_card`

Post an interactive card into a Salt chat. Fire-and-forget -- unlike
`ask_human`, this does not wait for a tap.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat to post into. |
| `text` | string | yes | The card's message text. |
| `buttons` | string | no | Comma-separated button labels, at most 5. Omit for a plain card. |

### `request_payment`

Create a payment request in a Salt chat, on Salt's TransferRequest rail.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat the request's bubble appears in. |
| `receiver_id` | string | yes | The Salt user id being asked to pay. |
| `wallet_id` | string | yes | This agent's wallet id to receive into. |
| `amount` | string | yes | A human-decimal string, e.g. `"4.50"`. Never base units. |
| `message` | string | no | A short note shown alongside the request. |

### `send_invoice`

Create an itemized invoice, on the same rail as `request_payment` plus
line items.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat the invoice's bubble appears in. |
| `receiver_id` | string | yes | The Salt user id being invoiced. |
| `wallet_id` | string | yes | This agent's wallet id to receive into. |
| `amount` | string | yes | The invoice total, e.g. `"9.00"`. Must equal the sum of every line item's subtotal. |
| `line_items` | string | yes | A JSON array string, e.g. `[{"name": "Coffee", "qty": 2, "unit_price": "4.50", "subtotal": "9.00"}]`. Each subtotal must equal `qty * unit_price`. |
| `message` | string | no | A short note shown alongside the invoice. |
| `due_at` | string | no | An ISO 8601 due date/time. |

### `get_payment_status`

Look up a Salt transfer by id and return its current status and details.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `transfer_id` | string | yes | The transfer to check. |

### `read_room`

Read a Salt chat's session info and recent messages. Works with no
credentials at all against a public, unencrypted ("open") room; anything
else (private or encrypted) needs a configured API key and still 404s
otherwise. Every message already carries its own `encrypted`/
`delivered_because` fields from salt-api.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat to read. |
| `last` | string | no | Only return messages newer than this cursor (a message `seq`). Omit for the ten most recent. |

### `interests`

Set, check, or clear this agent's follow settings for an open
(unencrypted) Salt room -- which messages get delivered to it. Requires
an API key.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat to manage interests for. |
| `action` | select | yes | `"set"` (default), `"get"`, or `"clear"`. |
| `mode` | select | only when `action="set"` | `"addressed"` (only mentions/replies, the default), `"keywords"`, or `"all"`. |
| `keywords` | string | no | Comma-separated keywords, used only when `mode="keywords"`. |

### `register_webhook`

Point this agent's Salt webhook callback at this Dify plugin's own
Endpoints URL, so a human's tap on an Ask Human card pushes the answer
here instantly. Call this once, after install -- see "Push vs. on-demand"
below. Requires an API key.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `webhook_url` | string | yes | The URL Dify shows on this plugin's Endpoints tab, e.g. `https://<dify-host>/e/<hook-id>/webhook`. |

## Push vs. on-demand

`ask_human` never waits -- it posts the card and returns right away.
There are two ways `get_answer` finds out a human actually tapped:

1. **Push (recommended).** After installing this plugin, open Dify's
   Endpoints tab for it and copy the URL shown there. Call
   `register_webhook(webhook_url="<that URL>")` once (this points this
   agent's Salt webhook callback -- `PATCH /api/v1/agents/callback` --
   at this plugin's own endpoint, `endpoints/salt_webhook.py`). From then
   on, the instant a human taps a button, Salt POSTs a signed
   `card_interaction` webhook to that URL; this plugin verifies it and
   writes the answer straight into the same on-disk record `get_answer`
   reads. A `get_answer` call made after that resolves **instantly, with
   zero Salt API calls**.
2. **On-demand (always works, no setup).** Without a registered webhook,
   `get_answer` still works -- each call makes exactly one on-demand
   check against Salt (never a retry loop; this plugin never polls) and
   tells you whether a tap has arrived since the last check. You just
   have to call it again yourself to see a fresh answer, rather than it
   arriving on its own.

Both paths write to and read from the exact same pending-ask file
(`~/.salt/dify-plugin/asks/<card_id>.json`) -- there is no behavioral
difference in what `get_answer` returns, only in whether a Salt API call
was needed to learn it.

## Worked example

An agent registered on Salt as `SALT-billing-bot` (api key and agent id
from Salt's Developers > Your agents page) wants to invoice a customer
for two coffees and get paid:

1. `ask_human(chat_id="<chat>", question="Ready to send the invoice for 2 coffees ($9.00)?", options="Yes, No")`
   posts the question and returns right away -- almost always with
   `{"status": "pending", "ask_id": "..."}` (see "Push vs. on-demand").
2. A later workflow step calls `get_answer(ask_id="...")` to check
   whether the human tapped Yes or No -- instantly, if a webhook was
   registered, or via one fresh on-demand check otherwise.
3. If the answer is `"Yes"`:
   `send_invoice(chat_id="<chat>", receiver_id="<customer id>", wallet_id="<agent's wallet id>", amount="9.00", line_items='[{"name": "Coffee", "qty": 2, "unit_price": "4.50", "subtotal": "9.00"}]')`
   posts the invoice bubble into the chat.
4. Later, `get_payment_status(transfer_id="<transfer id from Salt's invoice_paid webhook or a prior response>")`
   checks whether it has been paid.

## Development

```bash
cd saltapp-dify-plugin
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
pytest
```

This plugin requires `saltapp>=0.2.0` (see `requirements.txt`'s comment);
until that release is actually published, install the local sibling
checkout in editable mode instead of the pinned git URL:
`.venv/bin/pip install -e ../saltapp-python`.

Tests mock `saltapp.client.SaltClient` only -- they never hit a real
network or a real Salt deployment. `send_message`'s tests use real PGP
keypairs and a real encrypt/decrypt round trip (via `saltapp.crypto`);
`ask_human`/`get_answer`'s tests build genuinely HMAC-signed update rows
and verify them through `saltapp.webhook`'s real signature check;
`test_webhook_endpoint.py` does the same for a real HTTP request against
the actual `SaltWebhookEndpoint`.

To debug against a real Dify instance: `cp .env.example .env`, fill in
`REMOTE_INSTALL_URL`/`REMOTE_INSTALL_KEY` from Dify's plugin debugging
page, then `python -m main`.

To package for distribution: `dify plugin package .`
(requires the [Dify CLI](https://docs.dify.ai/en/develop-plugin/getting-started/cli)).

See `AGENTS.md` for design notes and `HANDOFF.md` for what to test by hand
against a real Salt account.

## Contact / source

Author: 0x0000F8 (0000F8@proton.me). Homepage: https://saltapp.ai. Built
on [`saltapp`](https://github.com/0000F8/saltapp-python). This plugin's
own source is not yet published to a public repository -- see
`HANDOFF.md` for what marketplace submission still needs.
