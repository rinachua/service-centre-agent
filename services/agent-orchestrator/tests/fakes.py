from dataclasses import dataclass

from app.tools import ServiceError


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeToolUseBlock:
    name: str
    input: dict
    id: str
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list


class _FakeMessages:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kwargs):
        self.outer.calls.append(kwargs)
        return self.outer._responses.pop(0)


class FakeAnthropicClient:
    """Duck-typed stand-in for anthropic.Anthropic — no network calls, no API key."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    @property
    def messages(self):
        return _FakeMessages(self)


class FakeToolExecutor:
    """Duck-typed stand-in for app.tools.ToolExecutor — returns scripted results per
    tool name instead of making real HTTP calls, and can simulate a downstream
    service failure for named tools via `error_on`."""

    def __init__(self, results=None, error_on=None):
        self.results = results or {}
        self.error_on = error_on or set()
        self.calls: list[tuple] = []

    def execute(self, tool_name, tool_input):
        self.calls.append((tool_name, tool_input))
        if tool_name in self.error_on:
            raise ServiceError(tool_name, "simulated failure")
        return self.results.get(tool_name, {})
