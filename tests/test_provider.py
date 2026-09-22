"""SaltProvider._validate_credentials: with and without a private key, an
invalid api key, an agent_id that doesn't match the api key, a private
key that fails to parse, and the now-optional agent_id/api_key (a
host-only credential set, for read_room's anonymous use, must validate)."""
from __future__ import annotations

import pytest
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from saltapp import crypto
from saltapp.errors import SaltApiError

from provider.salt import SaltProvider

BASE_CREDENTIALS = {"host": "https://saltapp.ai", "agent_id": "agent-1", "api_key": "key-1"}


def _provider() -> SaltProvider:
    return object.__new__(SaltProvider)


def test_validate_credentials_without_a_private_key_is_fine(fake_client):
    client = fake_client(BASE_CREDENTIALS)
    client.stub("who_am_i", {"agent_id": "agent-1", "webhook_secret": "whsec_x"})

    _provider()._validate_credentials(dict(BASE_CREDENTIALS))  # must not raise


def test_validate_credentials_with_a_valid_private_key_is_fine(fake_client):
    client = fake_client(BASE_CREDENTIALS)
    client.stub("who_am_i", {"agent_id": "agent-1", "webhook_secret": "whsec_x"})

    keypair = crypto.generate_keypair("a passphrase")
    credentials = dict(BASE_CREDENTIALS, private_key=keypair.private_key, passphrase="a passphrase")

    _provider()._validate_credentials(credentials)  # must not raise


def test_validate_credentials_rejects_a_private_key_that_does_not_parse(fake_client):
    client = fake_client(BASE_CREDENTIALS)
    client.stub("who_am_i", {"agent_id": "agent-1", "webhook_secret": "whsec_x"})

    credentials = dict(BASE_CREDENTIALS, private_key="not a pgp key at all")

    with pytest.raises(ToolProviderCredentialValidationError, match="does not parse"):
        _provider()._validate_credentials(credentials)


def test_validate_credentials_rejects_an_api_key_who_am_i_refuses(fake_client):
    client = fake_client(BASE_CREDENTIALS)
    client.stub(
        "who_am_i",
        SaltApiError("GET", "https://saltapp.ai/api/v1/agents/webhook_secret", 401, {"error": "invalid api key"}),
    )

    with pytest.raises(ToolProviderCredentialValidationError):
        _provider()._validate_credentials(dict(BASE_CREDENTIALS))


def test_validate_credentials_rejects_a_mismatched_agent_id(fake_client):
    client = fake_client(BASE_CREDENTIALS)
    client.stub("who_am_i", {"agent_id": "agent-DIFFERENT", "webhook_secret": "whsec_x"})

    with pytest.raises(ToolProviderCredentialValidationError, match="different agent id"):
        _provider()._validate_credentials(dict(BASE_CREDENTIALS))


def test_validate_credentials_requires_only_host():
    provider = _provider()
    with pytest.raises(ToolProviderCredentialValidationError, match="host"):
        provider._validate_credentials({"agent_id": "a", "api_key": "k"})


def test_validate_credentials_with_only_a_host_is_fine(fake_client):
    """agent_id/api_key are optional now (read_room works anonymously) --
    a provider configured with just a host must validate successfully,
    and must never even attempt a who_am_i call (there is no api_key to
    check it with)."""
    client = fake_client({"host": "https://saltapp.ai"})

    _provider()._validate_credentials({"host": "https://saltapp.ai"})  # must not raise

    assert client.calls == []


def test_validate_credentials_with_an_agent_id_but_no_api_key_is_fine(fake_client):
    client = fake_client({"host": "https://saltapp.ai", "agent_id": "agent-1"})

    _provider()._validate_credentials({"host": "https://saltapp.ai", "agent_id": "agent-1"})  # must not raise

    assert client.calls == []
