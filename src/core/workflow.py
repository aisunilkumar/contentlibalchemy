"""Thin runtime API used by the UI and scripts."""
from src.workflow.state_management import initial_turn_input


def run_turn(graph, text, thread_id, brand_profile=None, focus=None, on_update=None) -> dict:
    """Run one conversational turn, streaming per-node updates. Returns the final graph state."""
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    for chunk in graph.stream(initial_turn_input(text, brand_profile, focus), cfg, stream_mode="updates"):
        for node, update in chunk.items():
            if on_update and isinstance(update, dict):
                on_update(node, update)
    return graph.get_state(cfg).values
