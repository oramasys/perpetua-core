"""Typed message wrapper. OpenAI-compat shape, no extra deps."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, get_args

Role = Literal["system", "user", "assistant", "tool"]
_VALID = set(get_args(Role))


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self):
        if self.role not in _VALID:
            raise ValueError(f"invalid role: {self.role!r}")

    def to_openai_dict(self) -> dict:
        d: dict = {"role": self.role, "content": self.content}
        if self.name is not None:
            d["name"] = self.name
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        return d

    @classmethod
    def from_openai_dict(cls, d: dict) -> "ChatMessage":
        return cls(role=d["role"], content=d.get("content", ""),
                   name=d.get("name"), tool_call_id=d.get("tool_call_id"))


@dataclass
class ChatHistory:
    messages: list[ChatMessage] = field(default_factory=list)

    def append(self, m: ChatMessage) -> "ChatHistory":
        self.messages.append(m); return self

    def to_openai_messages(self) -> list[dict]:
        return [m.to_openai_dict() for m in self.messages]
