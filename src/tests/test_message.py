import pytest
from perpetua_core.message import ChatMessage, ChatHistory


def test_chat_message_round_trip():
    m = ChatMessage(role="user", content="hi")
    d = m.to_openai_dict()
    assert d == {"role": "user", "content": "hi"}
    assert ChatMessage.from_openai_dict(d) == m


def test_chat_history_appends_and_serializes():
    h = ChatHistory()
    h.append(ChatMessage(role="system", content="be brief"))
    h.append(ChatMessage(role="user", content="hi"))
    payload = h.to_openai_messages()
    assert payload == [
        {"role": "system", "content": "be brief"},
        {"role": "user",   "content": "hi"},
    ]


def test_invalid_role_rejected():
    with pytest.raises(ValueError):
        ChatMessage(role="random", content="x")
