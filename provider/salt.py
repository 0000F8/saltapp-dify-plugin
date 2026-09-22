from typing import Any

import pgpy

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from tools._salt_common import get_client


class SaltProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        """`host` is always required. `agent_id`/`api_key` are optional as
        of the open-rooms release -- read_room works with neither (an
        anonymous read of a public, unencrypted room), so a provider
        configured with just a host must validate successfully. The
        who_am_i identity check below only runs when an api_key was
        actually given; every other tool in this plugin still needs one
        and checks for it itself (see e.g. interests.py), same as before."""
        host = credentials.get("host")
        agent_id = credentials.get("agent_id")
        api_key = credentials.get("api_key")
        if not host:
            raise ToolProviderCredentialValidationError("Salt host is required.")

        if not api_key:
            return

        client = get_client(credentials)
        try:
            info = client.who_am_i(str(api_key))
        except Exception as exc:  # noqa: BLE001
            raise ToolProviderCredentialValidationError(
                f"Could not verify this agent's api key against {host}: {exc}"
            ) from exc

        resolved_agent_id = info.get("agent_id")
        if agent_id and resolved_agent_id and str(resolved_agent_id).lower() != str(agent_id).lower():
            raise ToolProviderCredentialValidationError(
                "This api key belongs to a different agent id than the one entered above."
            )

        # Optional: if a private key was given, confirm it parses as a PGP
        # key. This never attempts to decrypt anything -- just a parse
        # check, so a validation pass never depends on there being any
        # ciphertext addressed to this key yet.
        private_key = credentials.get("private_key")
        if private_key:
            try:
                pgpy.PGPKey.from_blob(str(private_key))
            except Exception as exc:  # noqa: BLE001
                raise ToolProviderCredentialValidationError(
                    f"Private key does not parse as a PGP key: {exc}"
                ) from exc
