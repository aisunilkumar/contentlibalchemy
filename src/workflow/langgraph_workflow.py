"""LangGraph wiring: router -> plan-driven agents -> quality loop -> strategist."""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.agents.blog_writer import blog_writer
from src.agents.content_strategist import content_strategist
from src.agents.image_generator import image_agent
from src.agents.linkedin_writer import linkedin_writer
from src.agents.query_handler import query_handler
from src.agents.research_agent import research_agent
from src.core.router import ROUTABLE, after_quality, next_node
from src.utils.quality_validation import quality_node
from src.workflow.state_management import ContentState


def build_graph(checkpointer=None):
    g = StateGraph(ContentState)
    g.add_node("router", query_handler)
    g.add_node("research", research_agent)
    g.add_node("blog", blog_writer)
    g.add_node("linkedin", linkedin_writer)
    g.add_node("image", image_agent)
    g.add_node("quality", quality_node)
    g.add_node("strategist", content_strategist)

    route_map = {n: n for n in ROUTABLE}
    g.add_edge(START, "router")
    g.add_conditional_edges("router", next_node, route_map)
    g.add_conditional_edges("research", next_node, route_map)
    g.add_conditional_edges("image", next_node, route_map)
    g.add_edge("blog", "quality")        # every written piece is scored
    g.add_edge("linkedin", "quality")
    g.add_conditional_edges("quality", after_quality, route_map)  # revise loop or next plan step
    g.add_edge("strategist", END)
    # Swap MemorySaver for a Postgres/Redis/SQLite saver in production.
    return g.compile(checkpointer=checkpointer or MemorySaver())
