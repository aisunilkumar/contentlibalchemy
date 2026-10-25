def to_chat(messages) -> list[dict]:
    """LangChain messages / tuples -> provider-neutral [{role, content}]."""
    out = []
    for m in messages:
        if isinstance(m, tuple):
            role, content = m
        else:
            role = {"human": "user", "ai": "assistant", "system": "system"}.get(getattr(m, "type", "user"), "user")
            content = m.content
        role = {"human": "user", "ai": "assistant"}.get(role, role)
        out.append({"role": role, "content": str(content)})
    return out


def last_user_text(messages) -> str:
    for m in reversed(to_chat(messages)):
        if m["role"] == "user":
            return m["content"]
    return ""
