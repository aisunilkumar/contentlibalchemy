"""Agent 5: Image Generation.
Prompt engineering: structured brief -> brand-consistent prompt -> lint.
Fallback ladder: provider chain -> simplified prompt retry -> branded SVG card (always available)."""
from src.agents import prompts
from src.core.brand import profile_from_state
from src.core.deps import get_services
from src.core.history import entries, make_entry
from src.integrations.services import AllProvidersFailed
from src.utils.content_optimization import trim_at_word
from src.utils.visuals import (ImageBrief, assemble_image_prompt, clean_alt_text, lint_image_prompt, make_svg_card,
                               simplify_image_prompt, template_brief)
from src.workflow.node_utils import resilient


def _fallback(state, exc):
    return {"image": {"prompt": "", "error": str(exc), "fallback": "none"}, "last_agent": "image",
            "step": state.get("step", 0) + 1}


@resilient("image", _fallback)
def image_agent(state):
    svc, topic = get_services(), state["topic"]
    profile, v = profile_from_state(state), profile_from_state(state).visual
    blog, li = state.get("blog") or {}, state.get("linkedin") or {}
    existing, instr = state.get("image") or {}, (state.get("instruction") or "").strip() if state.get("intent") == "refine" else ""
    platform, aspect = ("LinkedIn post", "square") if (li.get("text") and not blog.get("body")) else ("blog header", "landscape")
    if instr and existing.get("aspect"):  # refining: keep the same format
        aspect = existing["aspect"]
        platform = existing.get("platform", platform)
    request = (f"\nUSER REQUEST (apply this change to the previous direction): {instr}\nPREVIOUS DIRECTION: "
               f"{(existing.get('brief') or {}).get('subject', '')}; {existing.get('prompt', '')[:300]}") if instr else ""
    context = (blog.get("title") and f"{blog['title']}. {blog.get('meta_description', '')}") \
        or trim_at_word(li.get("text", ""), 300) or topic
    errors = []

    # 1) structured brief (cheap LLM), with a deterministic template as fallback
    try:
        brief = svc.structured(ImageBrief, [{"role": "user", "content": prompts.IMAGE_BRIEF.format(
            topic=topic, context=context, aspect=aspect, platform=platform, style=v.style, palette=v.palette,
            mood=v.mood, avoid=", ".join(v.avoid), request=request)}], tier="cheap", max_tokens=500)
    except Exception as exc:  # noqa: BLE001
        brief = template_brief(topic, v)
        errors.append(f"image: brief generation failed, used template ({type(exc).__name__})")

    # 2) brand-consistent prompt + lint
    prompt = assemble_image_prompt(brief, v, aspect)
    issues = lint_image_prompt(prompt)
    alt = clean_alt_text(brief.alt_text, f"Illustration representing {topic}")
    image = {"prompt": prompt, "brief": brief.model_dump(), "alt_text": alt, "aspect": aspect, "platform": platform,
             "lint": issues, "provider": None, "model": None, "url": None, "data": None, "svg": None,
             "fallback": "none", "error": None}

    # 3) fallback ladder
    res = None
    try:
        res = svc.image(prompt, aspect)
    except AllProvidersFailed as exc1:
        errors.append(f"image: providers failed ({str(exc1)[:120]}); retrying with a simplified prompt")
        simple = simplify_image_prompt(brief, v)
        try:
            res = svc.image(simple, aspect)
            image.update(prompt=simple, fallback="simplified_prompt")
        except AllProvidersFailed as exc2:
            errors.append(f"image: simplified retry failed ({str(exc2)[:120]}); using branded SVG card")

    if res is not None and (res.url or res.data):
        image.update(provider=res.provider, model=res.model, url=res.url, data=res.data, size=res.size)
    else:
        title = blog.get("title") or topic.title()
        image.update(svg=make_svg_card(title, profile.name, v.colors, aspect), fallback="svg_card", provider="svg_card",
                     alt_text=clean_alt_text(f"Branded graphic titled {title}", title),
                     error="image generation unavailable; showing a branded placeholder and the optimized prompt")
    hist = state.get("history") or []
    reason = f"Refined: {instr[:70]}" if instr else ("First image" if not entries(hist, "image") else "Regenerated")
    return {"image": image, "last_agent": "image", "step": state.get("step", 0) + 1, "errors": errors,
            "last_artifact": "image", "history": [make_entry("image", image, hist, state.get("turn", 0), reason)]}
