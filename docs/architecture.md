# ContentAlchemy Architecture

---

## High-Level Design (HLD)

### System Overview

ContentAlchemy is a **multi-agent, conversational content-creation platform**. A user types a natural-language request; the system produces one or more content artifacts (research brief, SEO blog, LinkedIn post, image) through a coordinated pipeline of AI agents, each writing to a shared state object that persists across conversation turns.

### Architectural Style

| Concern | Choice | Rationale |
|---|---|---|
| Orchestration | LangGraph `StateGraph` | Explicit node/edge wiring; conditional edges implement the revise loop deterministically |
| Agent communication | Shared `ContentState` (no direct calls) | Decouples agents; a single checkpointed source of truth; makes multi-turn context free |
| Provider access | Fallback `Chain` facade | Agents never import vendor SDKs; keys, order and pricing live in `config/services.yaml` |
| Persistence | LangGraph `MemorySaver` (swap for Postgres) | Conversation and artifacts survive session restarts; UI syncs from the checkpointer |
| UI | Streamlit | Rapid iteration; layout driven entirely from graph state |

### Subsystem Map

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web UI Layer                            │
│  streamlit_app.py  ┬  components/views.py                       │
│                    ├  components/styles.py (accessible CSS)      │
│                    └  components/media.py  (safe image render)   │
└───────────────────────────────┬─────────────────────────────────┘
                                │ run_turn()
┌───────────────────────────────▼─────────────────────────────────┐
│                     Workflow / Orchestration                      │
│  langgraph_workflow.py  (StateGraph, checkpointer)               │
│  state_management.py    (ContentState, reducers, turn resets)    │
│  node_utils.py          (resilient() wrapper: trace + fallback)  │
└──────┬──────────┬───────────┬──────────┬───────────┬────────────┘
       │          │           │          │           │
┌──────▼──┐ ┌────▼───┐ ┌─────▼──┐ ┌────▼───┐ ┌────▼──────┐
│ router  │ │research│ │  blog  │ │linkedin│ │  image   │
│(query   │ │ agent  │ │ writer │ │ writer │ │generator │
│handler) │ │        │ │        │ │        │ │          │
└──────┬──┘ └────┬───┘ └────┬───┘ └────┬───┘ └────┬─────┘
       │         │           │          │           │
       └─────────┴─────┬─────┴──────────┘           │
                       │  quality_node               │
                  ┌────▼────────────────┐            │
                  │  content_strategist │◄───────────┘
                  └─────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                       Integration Layer                          │
│  services.py  (Services facade: complete/structured/search/image)│
│  Chain        (ordered provider list, fallback, call_all fanout) │
│  resilience.py (RateLimiter, CircuitBreaker, TTLCache, CostTracker)│
│  providers.py  (OpenAI, Anthropic, Gemini, SerpAPI, Perplexity, …)│
└─────────────────────────────────────────────────────────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
    ┌────▼────┐          ┌──────▼──────┐        ┌──────▼──────┐
    │  LLM    │          │   Search    │         │   Image     │
    │providers│          │  providers  │         │  providers  │
    │GPT-4o   │          │ SerpAPI     │         │ DALL-E 3    │
    │Sonnet   │          │ Perplexity  │         │ Branded SVG │
    │Gemini   │          │ OpenAI web  │         │ (fallback)  │
    └─────────┘          └─────────────┘         └─────────────┘
```

### Request Lifecycle (one conversation turn)

```
User message
     │
     ▼
initial_turn_input()   ← resets per-turn fields (step, revisions, quality, …)
     │
     ▼
router (query_handler) ← LLM classifies intent + topic; derives plan deterministically
     │                    fallback: heuristic_route() when all LLMs are down
     ▼
next_node()            ← conditional edge reads plan[step]
     │
     ├─ research ──► next_node()
     │
     ├─ blog / linkedin ──► quality_node ──► after_quality()
     │                            │                │
     │                            │         revise? back to writer
     │                            │         accept? next_node()
     │
     ├─ image ──► next_node()
     │
     └─ strategist ──► END   ← composes final reply, updates messages
```

### Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Graph orchestration | LangGraph (`StateGraph`, `MemorySaver`) |
| Data validation | Pydantic v2 |
| UI | Streamlit |
| LLM providers | OpenAI (gpt-4o, gpt-4o-mini), Anthropic (Claude Sonnet), Google (Gemini Flash) |
| Search providers | SerpAPI, Perplexity Sonar, OpenAI web search |
| Image providers | DALL-E 3, branded SVG fallback |
| Config | YAML (`config/services.yaml`, `config/<env>.yaml`, `config/brand_voice.yaml`) |
| Testing | pytest, fake providers (no keys, no cost) |
| Containerisation | Docker + docker-compose |

### External Dependencies

```
.env (API keys)
  OPENAI_API_KEY       – required (LLM + search + image)
  ANTHROPIC_API_KEY    – optional (adds Claude to llm_smart chain)
  GEMINI_API_KEY       – optional (adds Gemini to both LLM chains)
  SERPAPI_API_KEY      – optional (adds Google web search)
  PERPLEXITY_API_KEY   – optional (adds Perplexity search)
```

---

## Low-Level Design (LLD)

### 1. ContentState — the shared contract

Defined in [src/workflow/state_management.py](../src/workflow/state_management.py).

```
ContentState (TypedDict)
├── Persisted across turns (LangGraph checkpointer)
│   ├── messages         – full chat history (add_messages reducer)
│   ├── brand_profile    – BrandProfile serialised as dict
│   ├── topic            – current noun-phrase topic (max 12 words)
│   ├── turn             – monotonically increasing counter
│   ├── last_artifact    – blog | linkedin | image (resolves "make it shorter")
│   └── focus            – UI hint: which view the user has open
│
├── Routing fields (written by router each turn)
│   ├── intent           – research | blog | linkedin | image | series | refine | undo | chat
│   ├── plan             – ordered list of agent names, e.g. ["research","blog"]
│   ├── step             – pointer into plan (next_node advances it)
│   ├── instruction      – refinement instruction ("make it shorter")
│   ├── refine_target    – blog | linkedin | image
│   └── note             – router → strategist message for chat/clarification
│
├── Artifacts (persist; reset on new topic)
│   ├── research         – {topic, summary, sources[], unverified}
│   ├── blog             – {title, meta_description, primary_keyword, keywords[],
│   │                       body, slug, cited_sources, quality}
│   ├── linkedin         – {text, hashtags[], hook, hook_variants[], format,
│   │                       first_comment, quality}
│   └── image            – {prompt, brief, alt_text, aspect,
│                           url|data|svg, provider, fallback, error}
│
├── Per-turn control (reset via initial_turn_input)
│   ├── quality          – {score, det_score, llm_score, checks[], feedback, revise, accepted}
│   ├── revisions        – count of quality-loop revisions this turn
│   └── final            – strategist's composed reply
│
├── Version history
│   └── history          – append_history reducer; capped at 12 per artifact kind
│
└── Observability (reset each turn via RESET sentinel)
    ├── errors            – append_or_reset reducer
    └── trace             – [{node, seconds, cost_usd}, …]
```

**Reducers:**

| Field | Reducer | Behaviour |
|---|---|---|
| `messages` | `add_messages` | LangGraph built-in; deduplicates by id |
| `history` | `append_history` | Appends entries; enforces per-kind cap (12) |
| `errors`, `trace` | `append_or_reset` | Appends during a turn; a `RESET` sentinel at the start of the list clears the previous turn's log |

### 2. Router — intent classification and plan derivation

**File:** [src/agents/query_handler.py](../src/agents/query_handler.py), [src/core/router.py](../src/core/router.py)

```
query_handler(state)
  │
  ├─ Build context string (current topic, existing artifacts, focus, last_artifact)
  ├─ Trim message history to last settings.history_window messages
  ├─ svc.structured(Route, messages, tier="cheap")   ← LLM classifies intent + topic
  │     on failure → heuristic_route(text, state)    ← regex keyword matching
  │
  └─ resolve_route(route, state)
        ├─ intent ∈ {refine, undo}
        │   └─ pick_target(): explicit → focus → last_artifact → any artifact
        │       ├─ refine: plan=[target], instruction=route.instruction
        │       └─ undo:   restore prev from history stack; write artifact + history entry
        │
        ├─ intent ∈ CONTENT_INTENTS (research/blog/linkedin/image/series)
        │   ├─ No topic → chat reply
        │   ├─ New topic → reset stale artifacts (research/blog/linkedin/image)
        │   └─ Same topic + research exists → remove "research" from plan (reuse)
        │
        └─ intent == chat → plan=[], note=message
```

**Static plans** (no LLM involvement in sequencing):

| Intent | Plan |
|---|---|
| `research` | `["research"]` |
| `blog` | `["research", "blog"]` (research skipped if reusable) |
| `linkedin` | `["research", "linkedin"]` |
| `image` | `["image"]` |
| `series` | `["research", "blog", "linkedin", "image"]` |
| `refine` | `[target]` |
| `chat` / `undo` | `[]` |

**Edge functions** in [src/core/router.py](../src/core/router.py):

```python
next_node(state)    → plan[step] if step < len(plan) else "strategist"
after_quality(state)→ last_agent  if quality.revise
                    → next_node() otherwise
```

### 3. Agent Internals

All agents follow the same pattern: decorated with `@resilient(name, fallback)`, read from `ContentState`, call `svc` methods, return a partial state update dict.

#### 3a. Research Agent

[src/agents/research_agent.py](../src/agents/research_agent.py)

```
research_agent(state)
  ├─ Generate N sub-queries (cheap LLM)
  ├─ Fan out: svc.search_all(query, max_providers=search_fanout) per sub-query (parallel)
  ├─ Deduplicate sources: normalise URL (strip tracking params), cap 2 per domain
  ├─ Score credibility: .gov/.edu/trusted outlets ↑; social/Q&A ↓
  ├─ svc.complete(research_prompt, tier="smart")
  ├─ strip_sources_section() + citation cleanup (utils/citations.py)
  └─ Return: {research: {topic, summary, sources[], unverified}, step: step+1}
```

#### 3b. Blog Writer

[src/agents/blog_writer.py](../src/agents/blog_writer.py)

```
blog_writer(state)
  ├─ Build prompt: topic + research summary + brand_profile + quality.feedback (on revision)
  ├─ svc.complete(prompt, tier="smart")
  ├─ SEO plan (cheap LLM): primary_keyword, keywords[], title, meta, slug
  ├─ utils/citations.py: validate [n] markers against sources; renumber; append Sources
  └─ Return: {blog: {title, meta_description, primary_keyword, keywords[], body, slug, cited_sources},
              revisions: revisions+1 (on revision)}
```

#### 3c. LinkedIn Writer

[src/agents/linkedin_writer.py](../src/agents/linkedin_writer.py)

```
linkedin_writer(state)
  ├─ Strategy step (cheap LLM): pick format (story/how-to/listicle/…), audience, angle
  ├─ Generate 3 hook variants; pick one deterministically (prefer concrete numbers)
  ├─ svc.complete(linkedin_prompt, tier="smart")
  ├─ Extract hashtags, first_comment, CTA
  └─ Return: {linkedin: {text, hashtags[], hook, hook_variants[], format, first_comment}}
```

#### 3d. Image Generator

[src/agents/image_generator.py](../src/agents/image_generator.py)

```
image_agent(state)
  ├─ Brief (cheap LLM): subject, composition, lighting, alt_text
  ├─ Assemble prompt: brief + brand_profile.visual (style + palette)
  ├─ Lint prompt (length, banned terms)
  ├─ Fallback ladder:
  │   1. svc.image(full_prompt, aspect)
  │   2. svc.image(simplified_prompt, aspect)   ← on failure
  │   3. Branded SVG card (always available)    ← on failure
  └─ Return: {image: {prompt, brief, alt_text, aspect, url|data|svg, provider, fallback}}
```

#### 3e. Content Strategist

[src/agents/content_strategist.py](../src/agents/content_strategist.py)

Runs last. Reads all artifacts + note + quality. Composes the assistant reply message and assembles `final`.

### 4. Quality Pipeline

**Files:** [src/utils/quality_validation.py](../src/utils/quality_validation.py), [src/utils/checks.py](../src/utils/checks.py)

```
quality_node(state)
  ├─ kind = state.last_agent  (blog | linkedin)
  ├─ Deterministic checks (blog_checks / linkedin_checks) → list[Check]
  │   Blog checks: title_length, title_keyword, meta_length, meta_keyword, word_count,
  │                keyword_in_intro, keyword_density, secondary_keywords, single_h1 (critical),
  │                h2_structure, heading_hierarchy, keyword_in_h2, faq_section, citations, …
  │   LinkedIn checks: length, hook_length, linkedin_fold, no_links_body,
  │                    hashtag_count, plain_text (critical), 3000_char_limit (critical), …
  │   Brand checks: banned_phrase (critical), preferred_terms, …
  │
  ├─ det_score = mean of passing check weights
  ├─ LLM judge (cheap tier, temperature=0, cached):
  │   Judge {seo, voice, accuracy, readability, engagement (each 0-10), feedback}
  │   llm_score = mean of 5 dimensions / 10
  │
  ├─ Blended: score = det_weight × det_score + (1 − det_weight) × llm_score
  │            default det_weight = 0.4  (40% rules, 60% LLM)
  │
  ├─ Critical cap: any critical check fail → score capped below quality_threshold
  ├─ should_revise(score, revisions, n_failed, critical):
  │     revise = (score < threshold OR critical OR n_failed > max_failed_checks)
  │              AND revisions < max_revisions
  │
  └─ Return: {quality: {score, det_score, llm_score, checks[], feedback, revise, accepted},
              step: step+1 (when accepted)}
```

**Quality thresholds** (configurable per environment):

| Setting | Default | Effect |
|---|---|---|
| `quality_threshold` | 7.5 | Score below this triggers revision |
| `max_revisions` | 2 | Hard cap on revision loops |
| `det_weight` | 0.4 | Deterministic rules share of blended score |
| `max_failed_checks` | 1 | More failing rules → force revision even if judge is lenient |

### 5. Integration / Provider Layer

**Files:** [src/integrations/services.py](../src/integrations/services.py), [src/integrations/resilience.py](../src/integrations/resilience.py), [src/integrations/providers.py](../src/integrations/providers.py)

#### Services facade

```python
class Services:
    complete(messages, tier="smart")     → LLMResult
    search(query, num=6)                 → SearchResult        # single provider, cached
    search_all(query, num, max_providers)→ list[SearchResult]  # fanout, cached
    image(prompt, aspect)                → ImageResult
    structured(schema, messages, tier)   → Pydantic model      # JSON mode + repair
```

#### Chain execution model

```
Chain.call(method, *args)          ← first-success fallback
  for each Entry (provider):
    1. CircuitBreaker.allow()?        skip if open
    2. RateLimiter.acquire()?         skip if local limit hit
    3. provider.method(*args)
       on ProviderError: retry with exponential backoff + jitter (up to `retries` times)
       on AuthError:     trip_permanent (disable provider for session)
    4. CostTracker.add(provider, cost)
    5. return result

Chain.call_all(method, *args, max_providers)  ← fanout for search
  collect up to max_providers successes; fail only if none succeed
```

#### Resilience primitives

| Class | Algorithm | Configuration |
|---|---|---|
| `RateLimiter` | Token bucket; burst = rpm | `rpm` per provider in services.yaml |
| `CircuitBreaker` | Count failures → open at threshold; half-open after cooldown | threshold=3, cooldown=60s |
| `TTLCache` | SHA-256 keyed dict; LRU eviction at maxsize | ttl=3600s, maxsize=512 |
| `CostTracker` | Accumulates USD spend per provider; raises `BudgetExceeded` | `budget_usd` in services.yaml |

**What is cached:** deterministic (`temperature=0`) LLM calls, all search calls. Image generation is never cached.

#### Provider chains (from `config/services.yaml`)

```
llm_smart:  openai_gpt4o → anthropic_sonnet → gemini_flash → openai_mini
llm_cheap:  openai_mini  → gemini_flash      → openai_gpt4o
search:     serpapi      → perplexity_sonar  → openai_websearch
image:      openai_image → (simplified retry) → branded SVG card
```

### 6. Resilient Node Wrapper

[src/workflow/node_utils.py](../src/workflow/node_utils.py)

```python
@resilient("node_name", fallback_fn)
def my_agent(state) -> dict:
    ...
```

On any unhandled exception:
1. `fallback_fn(state, exc)` is called — returns a degraded-but-valid state update.
2. The exception message is appended to `state.errors`.
3. A `trace` entry `{node, seconds, cost_usd}` is always recorded (success or failure).

The graph never crashes; every turn produces a strategist reply.

### 7. Session and History Management

**Files:** [src/core/session.py](../src/core/session.py), [src/core/history.py](../src/core/history.py)

#### History log

Every accepted draft, hand edit, undo and restore appends one entry:
```
{kind, version, artifact (snapshot), reason, score, restored_from, turn}
```
Capped at 12 entries per artifact kind. The undo stack is a pointer-based replay (not toggle):
- Normal entries push onto the stack.
- Restore entries pop back to the version they restored.
- Repeated undo always steps further back.

#### Hand edits (`apply_edit`)

```
apply_edit(graph, thread_id, kind, changes, reason)
  ├─ Merge changes into current artifact
  ├─ Re-derive slug (blog) or re-extract hashtags (linkedin)
  ├─ rescore_deterministic(kind, artifact, values)   ← rules only, no LLM
  ├─ Append history entry
  └─ graph.update_state(..., as_node="strategist")   ← single source of truth
```

#### Session snapshot (export / import)

```
snapshot(values) → dict
  ├─ schema: 1
  ├─ messages: serialised chat
  └─ topic, turn, brand_profile, research, blog, linkedin, image, history
     (images as base64 bytes)

parse_snapshot(raw) → dict   ← validates schema, types, size (≤ 8 MB); never executes
restore_snapshot(graph, thread_id, snap) → graph.update_state(...)
```

### 8. UI Layer

**Files:** [src/web_app/streamlit_app.py](../src/web_app/streamlit_app.py), [src/web_app/components/](../src/web_app/components/)

```
streamlit_app.py
  ├─ build_graph() once; thread_id per session
  ├─ Sidebar: brand voice editor, session import/export, cost display
  ├─ Left column: chat input, message history
  └─ Right column (workspace):
       ├─ Native radio group (not st.tabs) → active view sent as `focus` each turn
       ├─ Views: Overview, Research, Blog, LinkedIn, Image, Quality, History, Export
       ├─ views.py: one render function per view; all data from graph.get_state()
       ├─ styles.py: CSS tokens, skip links, live region (polite ARIA announcer)
       └─ media.py: only http(s) and generated data URIs are rendered (no arbitrary src)
```

The UI never holds its own copy of artifact text. Every action (hand edit, undo, restore, brand voice change) calls `graph.update_state`, so the workspace and chat are always in sync with the checkpointer.

### 9. Configuration System

```
config/
  services.yaml      – provider chains, models, rate limits, prices, budget, cache TTL
  development.yaml   – quality_threshold, max_revisions, min_blog_words, det_weight
  production.yaml    – same keys, production-tuned values
  brand_voice.yaml   – tone, do/avoid lists, style sample, per-platform limits, visual identity
```

`src/core/config.py` loads `APP_ENV` → selects the right YAML; the `settings` singleton is imported by agents and router. Brand profile is loaded once per session and stored in `ContentState.brand_profile`.

---

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
