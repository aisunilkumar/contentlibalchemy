"""All prompts live here as versioned constants so they can be tuned and tested in one place."""
PROMPT_VERSION = "2.0"

ROUTER_SYSTEM = """You are the routing brain of ContentAlchemy, a multi-agent content system.
Classify the user's latest message into exactly one intent:
- research: wants a research report or analysis on a topic
- blog: wants an SEO blog post or article
- linkedin: wants a LinkedIn post
- image: wants an image, visual or illustration
- series: wants a campaign, several formats, or "everything" on a topic
- refine: wants to change existing content ("shorter", "more casual", "add a section", "try again", "different style"); set refine_target to blog, linkedin or image and put the request in instruction
- undo: wants to revert a change ("undo that", "go back", "restore the previous version"); set refine_target if they say which artifact
- chat: greetings, questions about the system, or unclear requests
If the user does not say which artifact they mean, set refine_target to none: the app will use the artifact they are currently viewing.
Use the conversation history to resolve follow-ups like "now a LinkedIn post" or "make it shorter": reuse the previous topic and set is_followup=true.
topic must be a short noun phrase (max 12 words)."""

QUERY_GEN = ("Give 4 diverse web search queries, one per line, no numbering, to research: {topic}. "
             "Cover: overview, recent developments, statistics or data, and challenges or criticism.")

RESEARCH_SYNTH = """Write a research brief (450-700 words, Markdown) on: {topic}
Structure it with these H2 sections: Key findings, Data points, Conflicting evidence, Gaps and caveats.
CITATION RULES (strict):
- Use ONLY the material below. Never invent facts, numbers, quotes or sources.
- Cite inline as [n] using the numbers in the source list. Every paragraph or bullet with a factual claim needs a citation.
- Draw on at least {min_cites} different sources when that many are available.
- Where sources disagree, say so and cite both. If something is not supported, put it under "Gaps and caveats".
- Do NOT write a Sources section; it is generated automatically.

SEARCH-PROVIDER NOTES (context only; not citable):
{notes}

NUMBERED SOURCES:
{sources}"""

RESEARCH_NO_SOURCES = ("Write a short background brief (250-400 words, Markdown) on: {topic}. "
                       "No live sources are available, so avoid specific statistics and mark anything needing verification with [VERIFY]. "
                       "Do not include citations.")

SEO_PLAN = """Create an SEO plan for a blog post on: {topic}
Return: a primary keyword (2-4 words, what people actually search), 3-5 secondary keywords, a title (max 60 characters, starts with or contains the primary keyword), a meta description (110-155 characters, contains the primary keyword and a clear benefit), and a 5-6 item outline of H2 headings (at least one containing the primary or a secondary keyword)."""

BLOG_BODY = """Write the full blog post in Markdown.

TOPIC: {topic}
TITLE (use as the single H1): {title}
PRIMARY KEYWORD: {primary_keyword} (use naturally about once per 150 words; first 100 words; one H2; the conclusion; never stuff)
SECONDARY KEYWORDS: {secondary} (work most of them in naturally)
OUTLINE (H2 sections): {outline}

BRAND VOICE (follow exactly; these rules are checked automatically):
{brand}

RESEARCH (ground every factual claim here; cite as [n] using these numbers; never invent statistics):
{research}

STRUCTURE RULES: exactly one H1; 4-6 H2 sections; use H3 only under an H2 (never skip heading levels); 800-1200 words; a hook intro that states the benefit; short paragraphs; two [INTERNAL LINK: anchor text] placeholders.
FAQ RULES: an H2 titled "FAQ" followed by 3 questions, each as an H3 ending with "?" and a 1-3 sentence answer underneath.
End with a conclusion containing a call to action. Do NOT write a Sources section; it is added automatically.{unverified}
{revision}"""

LINKEDIN_PLAN = """Plan a LinkedIn post.
TOPIC: {topic}
BRAND VOICE:
{brand}
FACTS AVAILABLE:
{research}

Return JSON fields: angle (the single point of view), format (one of story, how_to, listicle, contrarian, data_led, question; pick what suits the topic), audience (who it is for, one line), hooks (exactly 3 distinct opening lines, each under 140 characters: one with a number or surprising claim, one a question, one a bold statement), cta (a question that invites comments), hashtags (3-5 CamelCase tags: 1-2 broad, 2-3 niche), first_comment (a useful addition to post as the first comment; may say the full article link goes here; no URL)."""

LINKEDIN_POST = """Write a LinkedIn post.

TOPIC: {topic}
FORMAT: {format}. ANGLE: {angle}. AUDIENCE: {audience}
FIRST LINE (use exactly): {hook}
CALL TO ACTION (end the body with this question): {cta}
HASHTAGS (last line, exactly these): {hashtags}

BRAND VOICE (follow exactly; checked automatically):
{brand}

FACTS (use for accuracy; do not cite inline):
{research}

RULES: the first line is the hook and the first two lines together stay under {fold} characters so they show before "see more"; short paragraphs of 1-3 lines separated by blank lines; 900-1500 characters total; plain text only (no Markdown, no asterisks); no links in the body; then the CTA question; then the hashtag line.
{revision}"""

USER_EDIT_BLOCK = """
USER REQUEST (apply exactly this change; keep everything else the same: structure, facts, citations, keywords, brand voice):
{feedback}

CURRENT DRAFT (this is the latest version, including any edits the user made by hand):
{previous}"""

REVISION_BLOCK = """
REVISION FEEDBACK (fix every point, keep what already works):
{feedback}

PREVIOUS DRAFT:
{previous}"""

IMAGE_BRIEF = """Create a visual brief for an AI image generator.
TOPIC: {topic}
CONTENT CONTEXT: {context}
FORMAT: {aspect} image for a {platform}
BRAND VISUAL IDENTITY (will be applied automatically; do not contradict): style {style}; palette {palette}; mood {mood}; avoid {avoid}.{request}
Return JSON fields: subject (one concrete, symbolic scene that represents the idea; no text, no people's faces), style, composition (camera angle, focal point, negative space), palette, lighting, mood, alt_text (max 125 characters, describes what is shown for screen readers, does not start with "image of")."""

JUDGE = """You are a strict content editor. Score the {kind} below from 0 to 10 on each dimension and give concrete fixes.
seo: keyword use, structure, scannability (for LinkedIn: hook strength, formatting, hashtag strategy)
voice: match to the brand voice below
accuracy: claims grounded in the research, nothing invented
readability: clarity, flow, no filler
engagement: hook, value, call to action
feedback: 2-5 specific, actionable fixes in one string.

BRAND VOICE:
{brand}

RESEARCH SUMMARY:
{research}

{kind_upper}:
{content}"""

CHAT_SYSTEM = ("You are ContentAlchemy's assistant. You help create research reports, SEO blog posts, LinkedIn posts and "
               "images, and you can run a full campaign on a topic. Answer briefly. If the user's request is unclear, "
               "ask which topic and format they want.")
