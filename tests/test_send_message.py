"""send_message: real PGP round-trip (never a mocked crypto layer -- only
the Salt HTTP client is faked), the no-recipients error path, and the
open-room plain-text branch."""
from __future__ import annotations

from saltapp import crypto

from tests.fakes import collect, json_messages, make_tool, text_messages
from tools.send_message import SendMessageTool

CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}


def test_send_message_encrypts_for_every_other_member_and_a_sender_copy(fake_client):
    self_kp = crypto.generate_keypair("self-pass")
    other_kp = crypto.generate_keypair("other-pass")

    client = fake_client(CREDENTIALS)
    # No "users" key on session -- send_message must fall back to a real
    # get_chat_members call for the member list (see _salt_common.send_message's
    # docstring).
    client.stub("get_chat", {"session": {"encrypted": True}})
    client.stub(
        "get_chat_members",
        [
            {"id": "agent-1", "public_key": self_kp.public_key},
            {"id": "user-2", "public_key": other_kp.public_key},
        ],
    )
    client.stub("post_message", lambda **kwargs: {"id": "msg-1", **kwargs})

    tool = make_tool(SendMessageTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "text": "hello world"}))

    results = json_messages(messages)
    assert len(results) == 1
    result = results[0]

    # The recipient copy really decrypts, with the recipient's OWN key,
    # to the original plaintext -- proves encrypt_for used the recipient's
    # real public key, not a stand-in.
    decrypted_for_recipient = crypto.decrypt(result["message"], other_kp.private_key, "other-pass")
    assert decrypted_for_recipient == "hello world"

    # And the sender copy decrypts with the SENDER's own key.
    decrypted_for_sender = crypto.decrypt(result["sender_message"], self_kp.private_key, "self-pass")
    assert decrypted_for_sender == "hello world"

    # The recipient's key must NOT be able to read the sender's own copy
    # (they're different ciphertexts, each addressed to one key).
    try:
        crypto.decrypt(result["sender_message"], other_kp.private_key, "other-pass")
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
    assert raised, "the recipient's key should not decrypt the sender-only copy"

    post_message_calls = [c for c in client.calls if c[0] == "post_message"]
    assert len(post_message_calls) == 1
    assert post_message_calls[0][1]["chat_id"] == "chat-1"


def test_send_message_with_no_recipient_keys_raises_a_clear_error(fake_client):
    self_kp = crypto.generate_keypair("self-pass")
    client = fake_client(CREDENTIALS)
    client.stub("get_chat", {"session": {"encrypted": True}})
    # Only self is a member (e.g. everyone else has no public key on file).
    client.stub("get_chat_members", [{"id": "agent-1", "public_key": self_kp.public_key}])

    tool = make_tool(SendMessageTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "text": "hello?"}))

    texts = text_messages(messages)
    assert len(texts) == 1
    assert "no recipient public keys in this chat; nothing to send to" in texts[0]
    assert not any(c[0] == "post_message" for c in client.calls)


def test_send_message_posts_plain_text_into_an_open_unencrypted_room(fake_client):
    client = fake_client(CREDENTIALS)
    client.stub("get_chat", {"session": {"encrypted": False, "users": [{"id": "agent-1"}]}})
    client.stub("post_plain_message", lambda **kwargs: {"id": "msg-1", **kwargs})

    tool = make_tool(SendMessageTool, CREDENTIALS)
    messages = collect(tool._invoke({"chat_id": "chat-1", "text": "hello open room"}))

    results = json_messages(messages)
    assert len(results) == 1
    assert results[0]["message"] == "hello open room"

    # The plain-text rail was used, never encryption or the PGP-post path.
    assert any(c[0] == "post_plain_message" for c in client.calls)
    assert not any(c[0] == "post_message" for c in client.calls)
    assert not any(c[0] == "get_chat_members" for c in client.calls)


def test_send_message_requires_chat_id_and_text(fake_client):
    tool = make_tool(SendMessageTool, CREDENTIALS)

    messages = collect(tool._invoke({"text": "hi"}))
    assert text_messages(messages) == ["chat_id is required."]

    messages = collect(tool._invoke({"chat_id": "chat-1"}))
    assert text_messages(messages) == ["text is required."]
