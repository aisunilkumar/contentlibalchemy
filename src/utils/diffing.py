import difflib
import re


def _words(t: str) -> list[str]:
    return re.findall(r"\S+", t or "")


def change_stats(old: str, new: str) -> dict:
    a, b = _words(old), _words(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return {"words_before": len(a), "words_after": len(b), "delta": len(b) - len(a), "added": added, "removed": removed}


def text_diff(old: str, new: str, max_chars: int = 6000) -> str:
    d = "\n".join(difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(),
                                       "before", "after", lineterm="", n=1))
    return d if len(d) <= max_chars else d[:max_chars] + "\n... (diff truncated)"
