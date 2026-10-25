"""Images with real alt text. st.image has no alt attribute, so we render <img> ourselves."""
import base64
import html

import streamlit as st

from src.utils.export_tools import image_bytes_and_ext

_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml"}


def image_src(img: dict) -> str | None:
    data, ext = image_bytes_and_ext(img or {})
    if data:
        return f"data:{_MIME[ext]};base64,{base64.b64encode(data).decode()}"
    url = (img or {}).get("url") or ""
    return url if url.startswith(("https://", "http://")) else None  # never render javascript:/data: URLs from state


def image_html(img: dict, max_width: str = "100%") -> str:
    src = image_src(img)
    if not src:
        return ""
    alt = html.escape((img.get("alt_text") or "").strip(), quote=True)
    return (f'<figure style="margin:0"><img src="{html.escape(src, quote=True)}" alt="{alt}" '
            f'style="max-width:{max_width};height:auto;border-radius:8px"/></figure>')


def render_image(img: dict) -> bool:
    markup = image_html(img)
    if markup:
        st.markdown(markup, unsafe_allow_html=True)
    return bool(markup)
