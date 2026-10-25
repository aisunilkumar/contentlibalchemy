import pytest

from src.core.deps import set_services
from src.workflow.langgraph_workflow import build_graph
from tests.fakes import FakeLLM, make_services


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def svc(llm):
    s = make_services(llms=[llm])
    set_services(s)
    yield s
    set_services(None)


@pytest.fixture
def graph(svc):
    return build_graph()
