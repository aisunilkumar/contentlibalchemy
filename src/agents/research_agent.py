"""Agent 2: Deep Research. Sub-queries -> multi-provider parallel search -> ranked sources ->
cited synthesis -> citation validation (renumber by first use, drop refs that match no source)."""
from concurrent.futures import ThreadPoolExecutor

from src.agents import prompts
from src.core.config import settings
from src.core.deps import get_services
from src.utils.citations import (citation_report, rank_sources, renumber_citations, strip_sources_section)
from src.utils.content_optimization import parse_lines
from src.workflow.node_utils import resilient


def _fallback(state, exc):
    return {"research": {"topic": state.get("topic", ""), "summary": "", "sources": [], "unverified": True},
            "last_agent": "research", "step": state.get("step", 0) + 1}


@resilient("research", _fallback)
def research_agent(state):
    svc, topic, errors = get_services(), state["topic"], []

    try:  # 1) diverse sub-queries (cheap, deterministic -> cached)
        raw = svc.complete([{"role": "user", "content": prompts.QUERY_GEN.format(topic=topic)}],
                           tier="cheap", temperature=0, max_tokens=200).text
        queries = parse_lines(raw, 4)
    except Exception as exc:  # noqa: BLE001
        queries = []
        errors.append(f"research: query generation failed ({type(exc).__name__})")
    queries = queries or [topic, f"{topic} statistics", f"{topic} trends"]

    # 2) parallel search; each sub-query fans out to up to `search_fanout` providers
    def one(q):
        try:
            return svc.search_all(q, max_providers=settings.search_fanout), None
        except Exception as exc:  # noqa: BLE001
            return [], f"search '{q[:40]}': {type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=min(4, len(queries))) as ex:
        outcomes = list(ex.map(one, queries))
    results = [r for rs, _ in outcomes for r in rs]
    errors += [e for _, e in outcomes if e]
    providers = sorted({r.provider for r in results})

    # 3) rank: dedupe by normalized URL, credibility heuristic, max 2 per domain
    raw_sources = [{**s, "provider": r.provider, "rank": i} for r in results for i, s in enumerate(r.sources)]
    ranked = rank_sources(raw_sources, max_per_domain=2, limit=settings.max_sources)
    notes = "\n\n".join(r.summary for r in results if r.summary)

    # 4) synthesize with citations, then validate them
    if ranked or notes:
        src_txt = "\n".join(f"[{i}] {s.get('title') or s['link']} - {s.get('snippet', '')} ({s['link']})"
                            for i, s in enumerate(ranked, 1)) or "(none)"
        draft = svc.complete([{"role": "user", "content": prompts.RESEARCH_SYNTH.format(
            topic=topic, notes=notes[:6000] or "(none)", sources=src_txt, min_cites=min(5, max(len(ranked), 1)))}],
            tier="smart", temperature=0.2, max_tokens=1300).text
        summary, used, invalid = renumber_citations(strip_sources_section(draft), ranked)
        report = citation_report(summary, used, len(ranked), invalid)
        if ranked and not used:  # nothing cited: keep top sources for reference, flag it
            used = ranked[:5]
        others = [s for s in ranked if s not in used]
        unverified = not ranked
        errors += [f"research: {f}" for f in report["flags"]]
    else:
        summary = svc.complete([{"role": "user", "content": prompts.RESEARCH_NO_SOURCES.format(topic=topic)}],
                               tier="smart", temperature=0.2, max_tokens=700).text
        used, others, unverified = [], [], True
        report = citation_report(summary, [], 0, [])
        errors.append("research: no live sources available; brief is unverified")

    return {"research": {"topic": topic, "summary": summary, "sources": used, "other_sources": others,
                         "unverified": unverified, "citation_report": report, "providers": providers},
            "last_agent": "research", "step": state.get("step", 0) + 1, "errors": errors}
