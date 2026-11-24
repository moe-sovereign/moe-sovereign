"""The expert node must send real chat roles to Ollama, not the repr of message dicts."""

from types import SimpleNamespace

from graph.expert import _ollama_chat_messages


def test_dict_messages_keep_their_roles_and_content():
    msgs = [{"role": "system", "content": "role prompt"}, {"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    assert _ollama_chat_messages(msgs) == msgs


def test_unknown_dict_role_becomes_user_and_missing_content_is_empty():
    assert _ollama_chat_messages([{"role": "function"}]) == [{"role": "user", "content": ""}]


def test_langchain_style_messages_are_mapped_by_type():
    msgs = [SimpleNamespace(type="system", content="s"), SimpleNamespace(type="human", content="h"), SimpleNamespace(type="ai", content="a")]
    assert [m["role"] for m in _ollama_chat_messages(msgs)] == ["system", "user", "assistant"]


def test_no_message_contains_a_dict_repr():
    out = _ollama_chat_messages([{"role": "system", "content": "role prompt"}, {"role": "user", "content": "/no_think\nq"}])
    assert not any(m["content"].startswith("{'role'") for m in out)
