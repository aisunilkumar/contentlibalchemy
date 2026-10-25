# ContentAlchemy

Multi-agent content creation system built on LangGraph: research briefs, SEO blog posts, LinkedIn posts and images,
from one conversational interface, with a scored revise loop and provider fallbacks.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add OPENAI_API_KEY (the only required key)
python scripts/smoke_test.py    # verifies your key through the abstraction layer (costs a few cents)
streamlit run src/web_app/streamlit_app.py
```

No UI? `python scripts/run_cli.py`.

With only an OpenAI key the chains resolve to: LLM `gpt-4o → gpt-4o-mini`, search `OpenAI web search`,
image `dall-e-3` (then simplified prompt, then a branded SVG card). Add `SERPAPI_API_KEY`, `PERPLEXITY_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`
to `.env` and those providers join the chains automatically (`pip install anthropic` for Claude).

## Using the interface

**Chat on the left, workspace on the right** (on a phone they stack: chat first, then workspace; use the "Skip to workspace" link).
The workspace has eight views, switched with the row of options at its top (arrow keys work):

| View | What it is for |
|---|---|
| Overview | What exists for this topic, scores, brand-voice status, next steps, one-click "Create" for missing pieces |
| Research | Findings with clickable `[n]` citations, a sources table (cited vs further reading, credibility, which provider found it), CSV/Markdown export |
| Blog | Edit title, meta and body; instant rule feedback while you edit; SEO panel; export `.md` / `.html` / `.txt` |
| LinkedIn | Edit the post; copy-ready text; first comment; hook options; plain-text export |
| Image | The image with real alt text (editable), the engineered prompt, fallback status, download |
| Quality | Score breakdown, every rule check as Pass/Fix in words, cross-format voice report, per-agent trace |
| History | Every version of every artifact, diff between any two, restore |
| Export | One zip with everything, plus a session file |

A turn automatically takes you to what changed. Items marked with a check in the view row exist.

**Conversations that keep context**
* *"Make it shorter"* applies to the view you are on (or the last thing you changed). Naming the target always wins:
  *"make the blog shorter"* works from any view. It never silently switches to a different artifact.
* **Your hand edits are the new baseline.** Save them (or just click a quick-change button, which saves first) and the
  next AI change starts from your text, not the old draft. Edits are re-checked against the SEO and brand rules instantly,
  with no AI call.
* **Undo from chat or a button.** *"Undo that"* works with the AI offline too. Repeated undo keeps stepping back; it never
  toggles between two versions. Restore any older version from the History view.
* Text refinements report what changed (words before and after, plus a full diff in History).
* **Save and resume.** Export a session file and load it from the sidebar later; drafts, history and chat come back
  exactly as they were. Files are validated before loading and are never executed.

## Accessibility

What is built in: every input has a visible label; status is always spelled out (Pass / Fix, Ready / Not created), never
colour alone; images are rendered with real `alt` text you can edit; a polite live region announces the result of each
turn; skip links to chat and workspace; a two-tone focus ring visible on light and dark backgrounds; 44px minimum touch
targets; reduced motion (follows your system setting, plus a switch); a larger-text option; a high-contrast mode; all
navigation uses native keyboard-operable controls.

What I could **not** verify, so please check before relying on it: I have not audited with a screen reader (NVDA,
VoiceOver) or an automated tool (axe, Lighthouse), nor viewed it at phone widths in a real browser. Streamlit controls
the page shell, so I cannot set the document language, define ARIA landmarks, or guarantee tab order inside its own
components. Treat this as a solid base, not a WCAG conformance claim.

## Try this conversation

1. *Write an SEO blog on cloud cost optimization*
2. *Now a LinkedIn post* (reuses the topic and research)
3. *Create an image* (square when only a LinkedIn post exists)
4. Open the LinkedIn view and say *make it punchier* (applies to the LinkedIn post)
5. *Undo that*
6. *Run a full campaign about FinOps for startups*

## What each capability does

**Research with checkable citations**
* Each sub-query fans out to up to `search_fanout` search providers (SerpAPI, Perplexity, OpenAI web search, whichever have keys).
* Sources are deduplicated by normalized URL (tracking params ignored), scored with a transparent credibility heuristic
  (`.gov/.edu`, trusted outlets up, social/Q&A sites down) and capped at 2 per domain.
* The brief must cite as `[n]`. Afterwards the system **removes any `[n]` that matches no retrieved source, renumbers
  citations by first use, and builds the Sources section itself from real URLs**. The model never writes the source list.
* Blogs get the same treatment, and the dashboard shows sources found / cited / domains / % of paragraphs cited.
* Limit: this proves every citation points at a real retrieved URL. It does **not** prove the page supports the claim.
  Spot-check anything important.

**SEO**: title (<=60) and meta (110-160) both containing the primary keyword, keyword in the first 100 words and in an H2,
keyword density, secondary keyword coverage, heading hierarchy (no skipped levels), FAQ as H3 questions, reading ease,
slug, Open Graph tags, and Article + FAQPage JSON-LD. Export `.md` (front matter) or `.html` (meta tags + JSON-LD).

**LinkedIn strategy**: a planning step picks the format (story, how-to, listicle, contrarian, data-led, question),
audience and angle, writes 3 hook options (one is chosen deterministically, preferring concrete numbers), a CTA, a
hashtag mix and a first comment for the link. Checks: hook length, hook visible before "see more" (`linkedin_fold_chars`,
an approximation you can tune), no links in the body, 3-5 hashtags, plain text. Platform rules change, so treat these as
tunable heuristics, not guarantees.

**Visuals**: LLM brief (subject, composition, lighting, alt text) -> prompt assembled with your **brand visual identity**
(style and palette always come from the profile, so images look consistent) -> lint -> provider chain. Fallback ladder:
providers -> simplified prompt retry -> **branded SVG card** (always available). Sized per platform (landscape / square).

**Brand voice**: one `config/brand_voice.yaml` profile (tone, POV, banned phrases, preferred terms, reading ease, per-platform
sentence length / emoji / exclamation limits, visual identity). It feeds every writer prompt (adapted per platform), the
judge, and deterministic checks run on every format. A cross-format voice report flags drift between blog and LinkedIn.
Edit the profile live in the UI sidebar (validated).

**Quality gate**: score = 40% rules + 60% LLM judge. A judge cannot average away rule violations: *critical* checks
(banned phrase, single H1, Markdown-free LinkedIn, 3000-char limit) cap the score below the threshold, and more than
`max_failed_checks` failing rules forces a revision. Cost lever: set `max_failed_checks` higher to revise less often.

## Layout

```
src/agents/        query_handler, research_agent, blog_writer, linkedin_writer, image_generator,
                   content_strategist, prompts.py (all prompts, versioned)
src/core/          config, router (intents, plans, edge functions), deps, workflow (run_turn)
src/integrations/  base, providers, resilience, services (fallback chains + facade)
src/workflow/      state_management, langgraph_workflow, node_utils
src/utils/         quality_validation, citations, seo, visuals, content_optimization, export_tools, checks, messages
src/core/brand.py  brand profile, per-platform prompts, voice checks, cross-format report
src/core/history.py  version log + undo stack; src/core/session.py  save/resume, hand edits, undo/restore
src/web_app/       streamlit_app.py + components/ (views, styles, accessible media)
config/            services.yaml, development.yaml, production.yaml, brand_voice.yaml
tests/             unit / integration / e2e (all run offline with fake providers)
docs/architecture.md
```

## Configuration

* `config/services.yaml`: fallback order per capability, models, rate limits, prices, `budget_usd`, cache TTL.
  Prices are placeholders: verify them on each provider's pricing page.
* `config/<APP_ENV>.yaml`: `quality_threshold`, `max_revisions`, `min_blog_words`, `det_weight`.
* `config/brand_voice.yaml`: tone, do/avoid lists and a style sample, injected into every writer prompt.
  You can also edit the voice live in the UI sidebar.

## Tests

```bash
pytest --cov=src --cov-report=term-missing
```

The suite uses fake providers, so it needs no keys and costs nothing. It covers routing, the revise loop and its cap,
multi-turn memory, refinement, provider fallback, search/image outages, router degradation, budget exhaustion,
rate limiting, circuit breaking, caching and the vendor wrappers (with mocked SDKs).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Research shows "unverified" | No search provider worked. Check that the web search tool name in `services.yaml` (`tool_type`) matches the current OpenAI docs, or add a `SERPAPI_API_KEY`. |
| Image shows a branded placeholder | Image providers failed even with the simplified prompt. Check the key/model (`dall-e-3` vs `gpt-image-1`, which may need org verification). The engineered prompt is kept in the Image tab. |
| "budget reached" | Raise `budget_usd` in `services.yaml`. |
| A provider never gets used | Its key is missing or it hit an auth error (check logs). |
| 429s | Lower that provider's `rpm` in `services.yaml`. |

## Cost tips

Routing, query generation, the SEO plan, image prompts and judging run on the cheap tier; only final writing uses the
smart tier. Repeated deterministic calls and searches are cached. `max_revisions` bounds the quality loop, and
`budget_usd` is a hard stop. The sidebar shows live spend.
