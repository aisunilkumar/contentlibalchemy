# ContentAlchemy architecture

## Graph

```mermaid
flowchart TD
  S([START]) --> R[router<br/>Query Handler]
  R -->|plan[step]| RS[research]
  R -->|plan[step]| B[blog]
  R -->|plan[step]| L[linkedin]
  R -->|plan[step]| I[image]
  R -->|empty plan / chat| ST[strategist]
  RS -->|plan[step]| B & L & I & ST
  B --> Q{quality}
  L --> Q
  Q -->|score < threshold and revisions < max| B & L
  Q -->|accepted: step+1| B & L & I & ST
  I -->|plan[step]| ST
  ST --> E([END])
```

Agents never call each other. They communicate only through `ContentState`
(`src/workflow/state_management.py`). The router writes a **plan** (an ordered list of agents);
the conditional edge `next_node` walks it with a `step` pointer.

## Who reads and writes what

| Agent | Reads | Writes |
|---|---|---|
| router | messages, topic, existing artifacts | intent, topic, plan, step, instruction, note, (resets stale artifacts on a new topic), brand_profile |
| research | topic | research (ranked + validated sources, citation_report), step+1 |
| blog | topic, research, brand_profile, quality (feedback), instruction, blog (for revisions) | blog (citations finalized), revisions |
| linkedin | topic, research, brand_profile, quality (feedback), instruction | linkedin (plan, hooks, first comment), revisions |
| image | topic, blog / linkedin (context), brand_profile.visual | image, step+1 |
| quality | last_agent artifact, research, brand_profile | quality, artifact.quality, step+1 (on accept) |
| strategist | everything | final, messages |

Every node is wrapped by `resilient()` (`src/workflow/node_utils.py`): exceptions never crash the graph;
the node's fallback produces a degraded result, the error is appended to `errors`, and a `trace` entry
(seconds and cost) is recorded.

## State lifecycle

* **Persist across turns** (checkpointer + `thread_id`): `messages`, `topic`, `brand_profile`, and the artifacts
  `research`, `blog`, `linkedin`, `image`. This is what makes "now a LinkedIn post" and "make it shorter" work.
* **Reset each turn** (`initial_turn_input`): `step`, `revisions`, `quality`, `instruction`, `note`, `final`, and the
  append-only `errors` / `trace` logs (cleared with a sentinel via the `append_or_reset` reducer).
* A **new topic** clears stale artifacts; a **same-topic follow-up** skips research and reuses it.

## Quality loop

`blog | linkedin → quality`. Score = `det_weight × rules + (1 − det_weight) × LLM judge`.
Rules are deterministic and unit-tested (title/meta length, word count, keyword in first 100 words, H1/H2 structure,
FAQ, citations; LinkedIn: length, hashtags, hook, CTA, paragraph length, plain text). If the score is below
`quality_threshold` and `revisions < max_revisions`, the same writer re-runs with the failed checks and judge feedback.
*Critical* checks (banned brand phrase, single H1, Markdown-free LinkedIn, 3000-char limit) cap the score below the
threshold, and more than `max_failed_checks` failing rules forces a revision, so a lenient judge cannot hide rule violations.
If the judge is unavailable, the rules score alone is used. After the cap, the draft is accepted and flagged.

## Provider layer

`src/integrations/services.py` exposes `complete / structured / search / image`. Each capability is a `Chain` of
providers ordered in `config/services.yaml`; a provider whose API key is missing is skipped. Per provider: token-bucket
rate limiter, circuit breaker (3 failures, 60s cooldown), retries with backoff and jitter. Auth errors disable a
provider for the session. Fallback moves across providers; retries stay within one. A `CostTracker` enforces
`budget_usd`; deterministic (`temperature=0`) LLM calls and all searches are cached.

## Performance

* Tiered models: `cheap` for routing, query generation, SEO plan, image prompt and judging; `smart` for final writing.
* Parallel sub-query search in the research agent.
* Caching, `max_tokens` caps per agent, a revision cap, and a hard budget.

## Citation integrity

Research and blog citations pass through `utils/citations.py`: strip any model-written Sources section, drop `[n]`
that match no retrieved source, renumber by first use, then append a Sources section built from the real URLs.
Structure is guaranteed; claim-to-source *support* is not (no page text is fetched), so the UI reports coverage and
the README tells users to spot-check.

## Image fallback ladder

provider chain (full prompt) -> provider chain (simplified prompt) -> branded SVG card. The engineered prompt and alt
text are always returned so the user can retry elsewhere.

## Conversation reliability

* **Single source of truth:** the UI renders chat and every view from the LangGraph checkpoint, never from its own copy.
  Undo, hand edits, restore and session import all write through `graph.update_state`, so views cannot disagree.
* **Resolving "it":** `router.pick_target` uses, in order, an explicit target in the message, the view the user is on
  (`focus`, sent by the UI each turn), then `last_artifact`, then any artifact that exists. An explicit target that does
  not exist yields a clear chat reply instead of silently editing something else.
* **History:** accepted drafts (after the quality loop), image generations, hand edits and restores each append an
  entry (`core/history.py`). Undo replays the log as a stack: normal entries push, restores pop back to the version
  they restored, so repeated undo walks back and never toggles. Capped at 12 entries per kind.
* **Hand edits** call `apply_edit`: the text is saved, re-scored by the deterministic rules (no LLM), and logged. The next
  refinement prompt carries it as the current draft, with "change only what is asked; keep everything else".
* **Resume:** `session.snapshot` / `restore_snapshot` serialize artifacts, history and chat (images as base64). Imports are
  validated for schema, types and size.

## UI structure

`web_app/streamlit_app.py` (layout, chat, sidebar, session) + `components/views.py` (one function per view),
`styles.py` (CSS, skip links, live region), `media.py` (images with real alt text; only http(s) and generated data URIs
are rendered). Views are a native radio group rather than `st.tabs` because the app must know which view is active
(it is sent to the router as `focus`).
