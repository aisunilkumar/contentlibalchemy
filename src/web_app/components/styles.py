"""CSS + accessibility helpers injected into the Streamlit page."""
import html

BASE = """
:focus-visible{outline:3px solid #fff !important;outline-offset:3px !important;box-shadow:0 0 0 3px #1D4ED8 !important;border-radius:4px}  /* two-tone ring: visible on light and dark */
.stButton>button,.stDownloadButton>button,.stLinkButton>a{min-height:44px}
.visually-hidden{position:absolute !important;left:-10000px;width:1px;height:1px;overflow:hidden}
.skip-links{display:flex;gap:.75rem;flex-wrap:wrap;margin:0 0 .5rem 0}
.skip-links a{position:absolute;left:-10000px;top:0;background:#0F766E;color:#fff !important;padding:.6rem 1rem;border-radius:6px;z-index:9999;text-decoration:none;font-weight:600}
.skip-links a:focus{position:static}
div[role="radiogroup"][aria-orientation="horizontal"],[data-testid="stRadio"] [role="radiogroup"]{gap:.25rem .75rem;flex-wrap:wrap}
@media (max-width:640px){.block-container{padding:1rem .75rem 4rem !important}}
@media (prefers-reduced-motion:reduce){*{animation:none !important;transition:none !important;scroll-behavior:auto !important}}
"""
LARGE = "html,body,.stApp,.stMarkdown,[data-testid='stCaptionContainer']{font-size:1.18rem !important;line-height:1.6 !important}"
MOTION = "*{animation:none !important;transition:none !important;scroll-behavior:auto !important}"
CONTRAST = """
.stApp,[data-testid="stSidebar"],[data-testid="stHeader"]{background:#000 !important;color:#fff !important}
.stApp p,.stApp span,.stApp label,.stApp li,.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp div{color:#fff}
.stApp a{color:#FFD400 !important;text-decoration:underline !important}
.stApp input,.stApp textarea,.stApp [data-baseweb="select"]>div{background:#000 !important;color:#fff !important;border:2px solid #fff !important}
.stApp button{background:#000 !important;color:#fff !important;border:2px solid #fff !important}
.stApp button[kind="primary"]{background:#FFD400 !important;color:#000 !important}
[data-testid="stMetricValue"]{color:#FFD400 !important}
"""


def build_css(high_contrast: bool = False, large_text: bool = False, reduce_motion: bool = False) -> str:
    css = BASE + (CONTRAST if high_contrast else "") + (LARGE if large_text else "") + (MOTION if reduce_motion else "")
    return f"<style>{css}</style>"


SKIP_LINKS = ('<nav aria-label="Skip links" class="skip-links"><a href="#chat-region">Skip to chat</a>'
              '<a href="#workspace-region">Skip to workspace</a></nav>')


def anchor(region_id: str) -> str:
    return f'<div id="{region_id}" tabindex="-1"></div>'


def live_announcement(text: str) -> str:
    """Polite live region: screen readers announce the outcome of the last turn."""
    return f'<div role="status" aria-live="polite" class="visually-hidden">{html.escape(text or "")}</div>'
