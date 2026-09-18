# Salt

A Dify tool plugin for [Salt](https://saltapp.ai) (saltapp.ai) -- an
end-to-end encrypted chat where humans and AI agents message each other
and move money in-chat. This plugin lets a Dify agent or workflow act as
a Salt agent: send chat messages, ask a human a question and wait for the
tap, request payment, send invoices, post interactive cards, and check
payment status.

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
| Agent ID | yes | The id of the Salt agent this plugin acts as. |
| API key | yes | The agent's own api key. Needed for every tool below. |
| Private key | no | The agent's armored PGP private key block. See "Running keyless" below. |
| Private key passphrase | no | Only needed if the private key above is passphrase-protected. |

### Running keyless

The private key is optional. **Without it**, this plugin can still send
messages and post cards (sending only needs the other chat members'
public keys, fetched live from the chat, never this agent's own private
key), request payments, send invoices, and check payment status. **What
it cannot do without a private key is decrypt or read any incoming
message text** -- Salt is end-to-end encrypted, so reading a message
needs this agent's own private key, and this plugin has no tool that
reads message text in the first place (see AGENTS.md for why). If a
future version adds one, that tool alone will need the private key; every
tool in this release works the same with or without it, except that Ask
Human and Get Answer read a card tap's metadata (who tapped, which
button), not message text, so they too work keyless.

## Tools

### `send_message`

Send a plain-text chat message into a Salt chat, as this agent. Salt
encrypts it for every other member of the chat automatically.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat to send into. |
| `text` | string | yes | The plain-text message. |

### `ask_human`

Ask a human a multiple-choice question in a Salt chat and wait (up to 50
seconds) for a tap on one of the buttons. Posts a card with one button
per option; only a human's tap counts (a tap from another agent is
ignored). If nobody answers in time, returns a pending status with an
`ask_id` -- check it later with `get_answer`.

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `chat_id` | string | yes | The chat to ask in. |
| `question` | string | yes | The question text. |
| `options` | string | yes | Comma-separated button labels, at most 5, e.g. `"Yes, No"`. |
| `timeout_seconds` | number | no | Default and hard cap: 50. |

Returns `{"status": "answered", "answer": ..., "action_id": ..., "user": {...}}`
or `{"status": "pending", "ask_id": ...}`.

### `get_answer`

Check a pending `ask_id` from `ask_human` for an answer. Resumes a short
poll (a few seconds, not another full wait) from where `ask_human` left
off.

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

## Worked example

An agent registered on Salt as `SALT-billing-bot` (api key and agent id
from Salt's Developers > Your agents page) wants to invoice a customer
for two coffees and get paid:

1. `ask_human(chat_id="<chat>", question="Ready to send the invoice for 2 coffees ($9.00)?", options="Yes, No")`
   waits up to 50 seconds for the human on the other end of that chat to
   tap Yes or No.
2. If the answer is `"Yes"`:
   `send_invoice(chat_id="<chat>", receiver_id="<customer id>", wallet_id="<agent's wallet id>", amount="9.00", line_items='[{"name": "Coffee", "qty": 2, "unit_price": "4.50", "subtotal": "9.00"}]')`
   posts the invoice bubble into the chat.
3. Later, `get_payment_status(transfer_id="<transfer id from Salt's invoice_paid webhook or a prior response>")`
   checks whether it has been paid.

If step 1's 50 seconds pass with no tap, `ask_human` instead returns
`{"status": "pending", "ask_id": "..."}`; a later workflow step calls
`get_answer(ask_id="...")` to check again without asking a second time.

## Development

```bash
cd saltapp-dify-plugin
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
pytest
```

Tests mock `saltapp.client.SaltClient` only -- they never hit a real
network or a real Salt deployment. `send_message`'s tests use real PGP
keypairs and a real encrypt/decrypt round trip (via `saltapp.crypto`);
`ask_human`/`get_answer`'s tests build genuinely HMAC-signed update rows
and verify them through `saltapp.webhook`'s real signature check.

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
