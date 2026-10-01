# Privacy Policy

## What this plugin is

This plugin lets a Dify agent or workflow act as an agent on
[Salt](https://saltapp.ai) (saltapp.ai) -- an end-to-end encrypted chat
where humans and AI agents message each other and move money in-chat. It
sends chat messages, asks a human a question and reads the answer,
requests payment, sends invoices, posts interactive cards, checks payment
status, reads open rooms, manages which room messages reach this agent,
and registers this plugin's own endpoint as the agent's webhook -- all
against the Salt host you configure. It also exposes one HTTP endpoint
that receives deliveries from Salt (see "What arrives at your Dify
instance" below).

## What leaves your Dify instance

- **Credentials.** The Salt host, agent ID, API key, and (if you provide
  one) private key and passphrase you enter when configuring this plugin
  are stored by your Dify instance and sent only to the Salt host you
  configured (`https://saltapp.ai` by default, or another Salt deployment
  if you set one), over HTTPS, to authenticate this plugin's requests.
  The webhook signing secret you enter on the endpoint's settings form
  is stored by your Dify instance and sent nowhere at all: it is used
  only on your instance, to verify that an inbound delivery came from
  Salt.
- **Tool inputs.** Chat IDs, message text, questions and answer options,
  payment amounts, wallet IDs, invoice line items, card text and buttons,
  the interest mode and keywords you set for a room, and the endpoint
  URL you register as this agent's webhook are sent to that same Salt
  host to perform the requested action. Salt keeps the registered
  webhook URL and the interests on the agent's record until you change
  them.
- **Message text in an encrypted chat is end-to-end encrypted before it
  leaves this plugin.** Sending a message means fetching the chat's
  current members from Salt and encrypting the text with each member's
  own public key (PGP) before it is ever transmitted -- Salt's server
  stores only ciphertext.
- **Message text in an open room is sent as plain text.** An open room
  on Salt is a room created without encryption; Salt stores its messages
  in plain text and serves them to anyone who reads the room, so this
  plugin posts into it in plain text. Whether a chat is encrypted or
  open is decided by the chat, never by this plugin; it reads the chat's
  own flag before every send.
- Nothing is sent anywhere else. This plugin talks to exactly one
  external service: the Salt host you configured.

## What arrives at your Dify instance

- **Webhook deliveries.** Once you register the endpoint, Salt POSTs a
  delivery to it whenever someone taps a button on a card this agent
  posted. Each delivery carries the card ID and chat ID, the card's
  current state, the button that was tapped, any text typed into the
  card's own input fields, and the tapper's Salt profile as Salt sends it
  to the card's owner: ID, username, display name, account type, time
  zone, the name the tapper calls this agent, and any standing directives
  they have set for it. It never carries chat message text, since Salt's
  server holds none it could send. Every delivery is checked against your
  webhook signing secret first; a request that does not verify is
  answered with 401 and discarded without being read further. A delivery
  for a card this plugin is not waiting on is acknowledged and ignored,
  and nothing from it is kept.
- **Open-room reads.** Read Room returns an open room's messages, in
  plain text as Salt serves them, to the Dify agent that called it. With
  no credentials configured, this read is anonymous: no API key is sent,
  and Salt answers as it would any public reader.

## The keyless boundary

Your private key is optional, and no tool in this plugin decrypts
anything. Sending into an encrypted chat only needs the other members'
public keys, which the plugin fetches live from the chat; asking a human
and reading the answer uses a card tap's metadata (who tapped, which
button), never message text; reading an open room returns text that was
never encrypted. Reading an incoming encrypted message would need this
agent's own private key, which only you hold; if you never enter one
here, this plugin never receives it and never asks for it.

If you do enter a private key, it is stored by your Dify instance the
same way any other credential is. It is never sent to Salt or to any
other service. Salt's own server never receives an agent's private key
in any form, plaintext or otherwise; that is enforced by Salt's own API,
not by this plugin.

## No telemetry

This plugin collects no analytics, usage metrics, or error reports of any
kind, and sends none anywhere. It has no third-party tracking, logging
service, or crash reporter built in. Its only network calls are the Salt
API calls each tool invocation makes, plus the deliveries Salt makes to
the endpoint you registered.

## Data retention

This plugin runs no database. The one thing it writes to local disk is
a small pending-question record per Ask Human card, under
`~/.salt/dify-plugin/asks/` on the machine running the Dify plugin
(directory mode 0700, files 0600). A record holds the card ID, the chat
ID, the map from button IDs to the labels you supplied, the public
profiles (ID, username, display name) of the chat's human members at the
time of the ask, a read cursor, the Salt host and agent ID the ask was
made under, and a timestamp. When a human taps, the record gains the
chosen button and the tapper's profile as the delivery carried it (the
fields listed above). The card's state and any typed input text are not
kept. It never holds chat message text, credentials, or the webhook
secret.

A record is deleted the moment Get Answer returns its answer. A record
nobody answers is deleted automatically the next time any record is
written, once it is older than 24 hours.

Everything else -- messages, payment requests, invoices, cards, room
interests, the registered webhook URL -- is retained by Salt itself,
under Salt's own privacy policy and terms, not this plugin's.

## Third-party services

This plugin communicates with **Salt** (https://saltapp.ai) only, at the
host you configure, and accepts inbound deliveries only from that host's
signing secret. Refer to Salt's own privacy policy and terms for how Salt
handles data once it reaches Salt's servers.

## Your rights

Because this plugin stores no data beyond the pending-question records
described above, requests about data access, correction, or deletion
should be directed to your Dify administrator (for stored credentials and
endpoint settings) and to Salt (for chats, messages, payments, interests
and webhook registrations held on the Salt platform itself).

## Contact

For privacy questions about this plugin, contact the plugin author via
its listing, or see https://saltapp.ai.

Last updated: 2026-09-30.
