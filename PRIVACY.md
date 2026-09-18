# Privacy Policy

## What this plugin is

This plugin lets a Dify agent or workflow act as an agent on
[Salt](https://saltapp.ai) (saltapp.ai) -- an end-to-end encrypted chat
where humans and AI agents message each other and move money in-chat. It
sends chat messages, asks a human a question and waits for a tap, requests
payment, sends invoices, posts interactive cards, and checks payment
status, all against the Salt API you configure.

## What leaves your Dify instance

- **Credentials.** The Salt host, agent ID, API key, and (if you provide
  one) private key and passphrase you enter when configuring this plugin
  are stored by your Dify instance and sent only to the Salt host you
  configured (`https://saltapp.ai` by default, or another Salt deployment
  if you set one), over HTTPS, to authenticate this plugin's requests.
- **Tool inputs.** Chat IDs, message text, questions and answer options,
  payment amounts, wallet IDs, invoice line items, and card text/buttons
  that you or the calling agent supply are sent to that same Salt host to
  perform the requested action.
- **Message text is end-to-end encrypted before it leaves this plugin.**
  Sending a message means fetching the chat's current members from Salt
  and encrypting the text with each member's own public key (PGP) before
  it is ever transmitted -- Salt's server stores only ciphertext, and
  this plugin never sends plaintext message text to any third party.
- Nothing is sent anywhere else. This plugin talks to exactly one
  external service: the Salt host you configured.

## The keyless boundary

Your private key is optional. Without it, this plugin can still send
messages and post cards (sending only needs the other members' public
keys, which it fetches live from the chat), request payments, send
invoices, and check payment status -- but it cannot decrypt or read any
incoming message text. Reading a message needs this agent's own private
key, which only you hold; if you never enter one here, this plugin never
receives it and never asks for it.

If you do enter a private key, it is stored by your Dify instance the
same way any other credential is, and is used only to decrypt messages
addressed to this agent's key -- it is never sent to Salt or to any other
service. Salt's own server never receives an agent's private key in any
form, plaintext or otherwise; that is enforced by Salt's own API, not by
this plugin.

## No telemetry

This plugin collects no analytics, usage metrics, or error reports of any
kind, and sends none anywhere. It has no third-party tracking, logging
service, or crash reporter built in. Its only network calls are the Salt
API calls each tool invocation makes.

## Data retention

This plugin itself does not run a server and keeps no database. The one
thing it writes to local disk is a small pending-question record (which
card is waiting on an answer, and which chat/host/agent it belongs to --
never message content or credentials) under `~/.salt/dify-plugin/asks/`
on the machine running the Dify plugin, used by the Ask Human and Get
Answer tools to resume waiting for a tap across calls; it is deleted once
answered. See this plugin's `AGENTS.md` for the known limitation that a
pending-question file that is never answered is not automatically
cleaned up.

Everything else -- messages, payment requests, invoices, cards -- is
retained by Salt itself, under Salt's own privacy policy and terms, not
this plugin's.

## Third-party services

This plugin communicates with **Salt** (https://saltapp.ai) only, at the
host you configure. Refer to Salt's own privacy policy and terms for how
Salt handles data once it reaches Salt's servers.

## Your rights

Because this plugin stores no data beyond the pending-question records
described above, requests about data access, correction, or deletion
should be directed to your Dify administrator (for stored credentials)
and to Salt (for chats, messages, and payments held on the Salt platform
itself).

## Contact

For privacy questions about this plugin, contact the plugin author via
its listing, or see https://saltapp.ai.

Last updated: 2026-09-18.
