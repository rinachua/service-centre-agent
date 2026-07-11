from dataclasses import dataclass, field


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
