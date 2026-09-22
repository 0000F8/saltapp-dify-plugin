"""Test doubles shared by every test module in this package.

`FakeSaltClient` stands in for `saltapp.client.SaltClient` -- it is
substituted for the real class via `monkeypatch.setattr(_salt_common,
"SaltClient", FakeSaltClient)`, so `tools._salt_common.get_client()`
constructs one of these instead of opening a real httpx connection. Every
call is recorded (so a test can assert on exactly what was sent to
"salt-api") and its return value or raised exception is configured per
test via `.stub(name, value_or_exception)`.

`make_tool` builds a real Dify `Tool` subclass instance the same way
`dify-official-plugins/tools/google/tests/test_google.py` does:
`object.__new__(cls)` bypasses `Tool.__init__` (which needs a live plugin
runtime session we don't have in a unit test) and then sets `.runtime`
directly -- this is a real `dify_plugin.Tool` instance, not a stub of one,
so `self.create_text_message`/`self.create_json_message` are the SDK's
own real implementations.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dify_plugin.entities.invoke_message import InvokeMessage
from dify_plugin.entities.tool import ToolInvokeMessage


class FakeSaltClient:
    """Drop-in stand-in for `saltapp.client.SaltClient`. Constructed the
    same way the real class is (`SaltClient(host)`), so `_salt_common.get_client`
    doesn't need to know it's talking to a fake."""

    def __init__(self, host: str) -> None:
        self.host = host.rstrip("/")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._stubs: dict[str, Any] = {}

    def stub(self, name: str, value: Any) -> "FakeSaltClient":
        self._stubs[name] = value
        return self

    def _record(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, kwargs))
        result = self._stubs.get(name)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(**kwargs)
        return result

    # -- the subset of SaltClient this plugin actually calls --

    def who_am_i(self, api_key: str) -> dict[str, Any]:
        return self._record("who_am_i", api_key=api_key)

    def get_chat(self, api_key: str, chat_id: str, *, last: Any = None) -> dict[str, Any]:
        return self._record("get_chat", api_key=api_key, chat_id=chat_id, last=last)

    def get_chat_members(self, api_key: str, chat_id: str) -> list[dict[str, Any]]:
        return self._record("get_chat_members", api_key=api_key, chat_id=chat_id)

    def post_message(self, api_key: str, chat_id: str, message: str, sender_message: str | None = None, **_: Any) -> dict[str, Any]:
        return self._record(
            "post_message", api_key=api_key, chat_id=chat_id, message=message, sender_message=sender_message,
        )

    def post_plain_message(self, api_key: str, chat_id: str, message: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("post_plain_message", api_key=api_key, chat_id=chat_id, message=message, **kwargs)

    def get_chat_subscription(self, api_key: str, chat_id: str) -> dict[str, Any]:
        return self._record("get_chat_subscription", api_key=api_key, chat_id=chat_id)

    def set_chat_subscription(self, api_key: str, chat_id: str, mode: str, *, keywords: list[str] | None = None) -> dict[str, Any]:
        return self._record("set_chat_subscription", api_key=api_key, chat_id=chat_id, mode=mode, keywords=keywords)

    def clear_chat_subscription(self, api_key: str, chat_id: str) -> dict[str, Any]:
        return self._record("clear_chat_subscription", api_key=api_key, chat_id=chat_id)

    def set_callback(self, api_key: str, webhook: str) -> dict[str, Any]:
        return self._record("set_callback", api_key=api_key, webhook=webhook)

    def post_card(self, api_key: str, chat_id: str, blocks: list[dict[str, Any]], text: str) -> dict[str, Any]:
        return self._record("post_card", api_key=api_key, chat_id=chat_id, blocks=blocks, text=text)

    def update_card(self, api_key: str, card_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        return self._record("update_card", api_key=api_key, card_id=card_id, blocks=blocks)

    def request_payment(self, api_key: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("request_payment", api_key=api_key, **kwargs)

    def create_invoice(self, api_key: str, **kwargs: Any) -> dict[str, Any]:
        return self._record("create_invoice", api_key=api_key, **kwargs)

    def get_transfer(self, api_key: str, transfer_id: str) -> dict[str, Any]:
        return self._record("get_transfer", api_key=api_key, transfer_id=transfer_id)

    def get_agent_updates(self, api_key: str, *, after: int = 0, timeout: int = 2, limit: int = 100) -> dict[str, Any]:
        return self._record("get_agent_updates", api_key=api_key, after=after, timeout=timeout, limit=limit)


def make_tool(tool_cls: type, credentials: dict[str, Any]):
    """A real `tool_cls` instance (a `dify_plugin.Tool` subclass) with a
    working `.runtime.credentials` and none of the rest of the plugin
    runtime -- exactly the pattern the google plugin's own real test suite
    uses (`object.__new__` skips `Tool.__init__`, which expects a live
    session this test never opens). `Tool.__init__` is also what sets
    `response_type` (used by `create_json_message`/`create_text_message`),
    so that's set by hand here too."""
    tool = object.__new__(tool_cls)
    tool.runtime = SimpleNamespace(credentials=credentials)
    tool.response_type = ToolInvokeMessage
    return tool


def collect(generator) -> list[Any]:
    return list(generator)


def json_messages(messages: list[Any]) -> list[Any]:
    """Every `create_json_message(...)` result's `json_object` payload, in
    order -- read back through the real `dify_plugin` shape
    (`ToolInvokeMessage(type=MessageType.JSON, message=JsonMessage(...))`),
    not an assumed one."""
    return [m.message.json_object for m in messages if m.type == InvokeMessage.MessageType.JSON]


def text_messages(messages: list[Any]) -> list[str]:
    """Every `create_text_message(...)` result's plain text, in order."""
    return [m.message.text for m in messages if m.type == InvokeMessage.MessageType.TEXT]
