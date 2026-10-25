from src.core.history import (MAX_PER_KIND, append_history, current_stack, current_version, make_entry, next_version,
                              undo_target)
from src.utils.diffing import change_stats, text_diff


def log(*ops):
    """ops: ('new', text) or ('restore', version)."""
    h = []
    for i, (op, arg) in enumerate(ops, 1):
        if op == "new":
            h.append(make_entry("blog", {"body": arg}, h, i, f"edit {arg}"))
        else:
            src = next(e for e in h if e["version"] == arg)
            h.append(make_entry("blog", src["artifact"], h, i, f"restored {arg}", restored_from=arg))
    return h


def test_versions_are_numbered_per_kind():
    h = [make_entry("blog", {}, [], 1, "a")]
    h.append(make_entry("linkedin", {}, h, 1, "b"))
    assert next_version(h, "blog") == 2 and next_version(h, "linkedin") == 2 and next_version(h, "image") == 1


def test_undo_walks_back_one_step_at_a_time_without_toggling():
    h = log(("new", "v1"), ("new", "v2"), ("new", "v3"))
    assert undo_target(h, "blog")["version"] == 2
    h += log(("new", "v1"), ("new", "v2"), ("new", "v3"), ("restore", 2))[3:]
    h = log(("new", "v1"), ("new", "v2"), ("new", "v3"), ("restore", 2))
    assert current_version(h, "blog")["artifact"]["body"] == "v2" and undo_target(h, "blog")["version"] == 1
    h = log(("new", "v1"), ("new", "v2"), ("new", "v3"), ("restore", 2), ("restore", 1))
    assert current_version(h, "blog")["artifact"]["body"] == "v1" and undo_target(h, "blog") is None


def test_new_edit_after_undo_undoes_to_the_restored_state():
    h = log(("new", "v1"), ("new", "v2"), ("restore", 1), ("new", "v4"))
    assert undo_target(h, "blog")["artifact"]["body"] == "v1"


def test_restoring_a_discarded_version_is_treated_as_new():
    h = log(("new", "v1"), ("new", "v2"), ("restore", 1), ("restore", 2))
    assert current_version(h, "blog")["artifact"]["body"] == "v2" and undo_target(h, "blog")["artifact"]["body"] == "v1"


def test_single_version_has_no_undo():
    assert undo_target(log(("new", "v1")), "blog") is None and undo_target([], "blog") is None


def test_history_is_capped_per_kind_and_keeps_order():
    h = []
    for i in range(MAX_PER_KIND + 5):
        h = append_history(h, [make_entry("blog", {"body": str(i)}, h, i, "x")])
    h = append_history(h, [make_entry("image", {"prompt": "p"}, h, 1, "x")])
    blog = [e for e in h if e["kind"] == "blog"]
    assert len(blog) == MAX_PER_KIND and blog[-1]["artifact"]["body"] == str(MAX_PER_KIND + 4) and h[-1]["kind"] == "image"


def test_entries_are_deep_copies():
    art = {"body": "x"}
    e = make_entry("blog", art, [], 1, "r")
    art["body"] = "mutated"
    assert e["artifact"]["body"] == "x"


def test_change_stats_and_diff():
    st = change_stats("one two three four", "one three four five six")
    assert st == {"words_before": 4, "words_after": 5, "delta": 1, "added": 2, "removed": 1}
    d = text_diff("a\nb\nc", "a\nB\nc")
    assert "-b" in d and "+B" in d and "(diff truncated)" in text_diff("x\n" * 5000, "y\n" * 5000, 100)
