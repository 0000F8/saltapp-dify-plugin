"""Send guard: fingerprints ride the post, one retry on stale_recipient_keys re-encrypting to the
returned keys, no loop on a second refusal, no guard when a fingerprint is missing."""
from __future__ import annotations

import pytest
from saltapp import crypto
from saltapp.errors import SaltApiError

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.send_message import SendMessageTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}


def _members(prints=True):
    self_kp = crypto.generate_keypair("s")
    other_kp = crypto.generate_keypair("o")
    other = {"id": "user-2", "public_key": other_kp.public_key}
    if prints:
        other["public_fingerprint"] = crypto.fingerprint_of(other_kp.public_key)
    return self_kp, other_kp, [{"id": "agent-1", "public_key": self_kp.public_key}, other]


def _send(fake_client, members, post):
    client = fake_client(CREDENTIALS)
    client.stub("get_chat", {"session": {"encrypted": True, "users": members}})
    client.stub("post_message", post)
    tool = make_tool(SendMessageTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "text": "hi"}))
    return client, messages


def _posts(client):
    return [c[1] for c in client.calls if c[0] == "post_message"]


def test_fingerprints_are_sent(fake_client):
    self_kp, other_kp, members = _members()
    client, _ = _send(fake_client, members, lambda **kw: {"id": "m"})
    (kw,) = _posts(client)
    assert kw["recipient_fingerprints"] == {"user-2": crypto.fingerprint_of(other_kp.public_key).upper()}
    assert kw["sender_fingerprint"] == crypto.fingerprint_of(self_kp.public_key).upper()


def test_no_guard_when_a_fingerprint_is_missing(fake_client):
    _, _, members = _members(prints=False)
    client, _ = _send(fake_client, members, lambda **kw: {"id": "m"})
    (kw,) = _posts(client)
    assert kw["recipient_fingerprints"] is None and kw["sender_fingerprint"] is None


def test_one_retry_on_stale_recipient_keys(fake_client):
    _, _, members = _members()
    new_kp = crypto.generate_keypair("n")
    fresh = [{"id": "user-2", "public_key": new_kp.public_key,
              "public_fingerprint": crypto.fingerprint_of(new_kp.public_key), "key_generation": 2}]
    seen = []

    def post(**kw):
        seen.append(kw)
        if len(seen) == 1:
            raise SaltApiError("POST", "u", 409, {"code": "stale_recipient_keys", "members": fresh})
        return {"id": "m"}

    client, messages = _send(fake_client, members, post)
    assert len(seen) == 2
    assert crypto.decrypt(seen[1]["message"], new_kp.private_key, "n") == "hi"
    assert seen[1]["recipient_fingerprints"] == {"user-2": crypto.fingerprint_of(new_kp.public_key).upper()}
    assert json_messages(messages)


def test_second_refusal_does_not_loop(fake_client):
    _, _, members = _members()
    new_kp = crypto.generate_keypair("n")
    body = {"code": "stale_recipient_keys", "members": [
        {"id": "user-2", "public_key": new_kp.public_key, "public_fingerprint": crypto.fingerprint_of(new_kp.public_key)}]}
    seen = []

    def post(**kw):
        seen.append(kw)
        raise SaltApiError("POST", "u", 409, body)

    _send(fake_client, members, post)
    assert len(seen) == 2
