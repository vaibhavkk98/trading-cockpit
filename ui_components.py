"""Presentation-only helpers for the frozen Streamlit trading cockpit."""

from html import escape
from numbers import Number

import streamlit as st


NOT_AVAILABLE = {None, "", "NOT_AVAILABLE", "NOT AVAILABLE", "N/A", "None", "null"}


def _valid_number(value):
    return isinstance(value, Number) and not isinstance(value, bool) and value == value


def apply_theme():
    """Install the cockpit's single, restrained dark-dashboard visual system."""
    st.markdown(
        """
        <style>
          :root { --tc-bg:#0b1220; --tc-panel:#121c2c; --tc-panel-2:#17243a; --tc-line:#293a54;
            --tc-text:#e5edf8; --tc-muted:#91a0b8; --tc-blue:#5b9dff; --tc-green:#42c891;
            --tc-amber:#e9b44c; --tc-red:#ee7373; --tc-radius:10px; }
          .stApp { background:var(--tc-bg); color:var(--tc-text); }
          .main .block-container { max-width:1480px; padding-top:1.1rem; padding-bottom:2.25rem; }
          h1,h2,h3 { letter-spacing:-0.02em; }
          h3 { margin-top:.25rem !important; }
          .tc-header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px;
            padding:4px 0 13px; margin-bottom:5px; border-bottom:1px solid var(--tc-line); }
          .tc-title { color:var(--tc-text); font-size:1.5rem; line-height:1.2; font-weight:700; }
          .tc-subtitle { color:var(--tc-muted); margin-top:3px; font-size:.88rem; }
          .tc-meta { color:var(--tc-muted); font-size:.78rem; text-align:right; white-space:nowrap; }
          .tc-section { display:flex; justify-content:space-between; align-items:center; gap:12px;
            margin:1.35rem 0 .55rem; }
          .tc-section-title { font-size:1.02rem; color:var(--tc-text); font-weight:700; }
          .tc-section-copy { color:var(--tc-muted); font-size:.78rem; margin-top:2px; }
          .tc-metric { background:var(--tc-panel); border:1px solid var(--tc-line); border-radius:var(--tc-radius);
            padding:12px 13px; min-height:78px; }
          .tc-metric-label { color:var(--tc-muted); font-size:.72rem; font-weight:600; text-transform:uppercase;
            letter-spacing:.055em; }
          .tc-metric-value { color:var(--tc-text); font-size:1.28rem; font-weight:700; line-height:1.35;
            margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
          .tc-metric-detail { color:var(--tc-muted); font-size:.72rem; margin-top:2px; }
          .tc-badge { display:inline-block; border-radius:999px; padding:3px 8px; font-size:.69rem;
            font-weight:700; letter-spacing:.035em; border:1px solid transparent; white-space:nowrap; }
          .tc-neutral { color:#b9c8dc; background:#1d2a3d; border-color:#344864; }
          .tc-good { color:#7ce0b2; background:#17382e; border-color:#2c785c; }
          .tc-warn { color:#f4cb70; background:#41351c; border-color:#806326; }
          .tc-bad { color:#f39a9a; background:#432327; border-color:#824047; }
          .tc-unavailable { color:#a7b2c2; background:#202938; border-color:#3a4659; }
          .tc-card { background:var(--tc-panel); border:1px solid var(--tc-line); border-radius:var(--tc-radius);
            padding:14px 15px; margin-bottom:10px; }
          .tc-card-title { color:var(--tc-text); font-weight:700; font-size:.88rem; }
          .tc-card-copy { color:var(--tc-muted); font-size:.79rem; margin-top:5px; line-height:1.45; }
          .tc-kicker { color:var(--tc-muted); font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; }
          .tc-value { color:var(--tc-text); font-size:1.08rem; font-weight:650; margin-top:3px; }
          .tc-empty { background:#101a29; border:1px dashed #3c4f6b; border-radius:var(--tc-radius);
            padding:20px; text-align:center; margin:10px 0; }
          .tc-empty-title { color:var(--tc-text); font-weight:700; }
          .tc-empty-copy { color:var(--tc-muted); font-size:.84rem; margin-top:5px; }
          .tc-note { color:var(--tc-muted); font-size:.76rem; margin-top:6px; }
          .tc-event { padding:7px 0; border-bottom:1px solid rgba(41,58,84,.65); }
          .tc-event:last-child { border-bottom:none; }
          .tc-event-title { color:var(--tc-text); font-size:.84rem; font-weight:600; line-height:1.35; }
          .tc-event-meta { color:var(--tc-muted); font-size:.71rem; margin-top:2px; }
          .stTabs [data-baseweb="tab-list"] { gap:22px; border-bottom:1px solid var(--tc-line); }
          .stTabs [data-baseweb="tab"] { background:transparent !important; border:none !important; padding:9px 0 10px;
            color:var(--tc-muted); font-size:.83rem; font-weight:650; }
          .stTabs [aria-selected="true"] { color:var(--tc-text) !important; border-bottom:2px solid var(--tc-blue) !important; }
          [data-testid="stBaseButton-primary"] { background:#356fae !important; border-color:#4b87ca !important; color:#f8fbff !important; box-shadow:none !important; }
          [data-testid="stBaseButton-primary"]:hover { background:#417fc0 !important; border-color:#659ddd !important; }
          [data-testid="stDataFrame"] { border:1px solid var(--tc-line); border-radius:var(--tc-radius); overflow:hidden; }
          [data-testid="stExpander"] { border:1px solid var(--tc-line); border-radius:var(--tc-radius); background:var(--tc-panel); }
          div[data-testid="stAlert"] { border-radius:var(--tc-radius); }
          @media (max-width: 900px) { .tc-header { display:block; } .tc-meta { text-align:left; margin-top:7px; white-space:normal; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def display_value(value, unavailable="Not available", not_applicable="—"):
    """Normalize availability semantics for consumer-facing presentation."""
    if value in {"NOT_APPLICABLE", "NOT APPLICABLE", "—"}:
        return not_applicable
    if isinstance(value, Number) and value != value:
        return unavailable
    return unavailable if value in NOT_AVAILABLE else str(value)


def format_price(value):
    return f"₹{value:,.2f}" if _valid_number(value) else "Not available"


def format_currency(value, compact=True):
    if not _valid_number(value):
        return "Not available"
    if compact and abs(value) >= 100_000:
        return f"₹{value / 100_000:.2f}L"
    return f"₹{value:,.0f}" if compact else f"₹{value:,.2f}"


def format_percent(value, decimals=2):
    return f"{value:.{decimals}f}%" if _valid_number(value) else "Not available"


def format_volume(value):
    return f"{value:.2f}×" if _valid_number(value) else "Not available"


def allocation_display(status, reason_code=None):
    """Human labels only; preserve raw codes in the live decision object."""
    if status == "ALLOCATED":
        return "Allocated", "good"
    if reason_code == "DUPLICATE_POSITION":
        return "Not allocated · duplicate", "warn"
    if reason_code == "CAPITAL_CAP":
        return "Not allocated · capacity", "warn"
    if status and status != "NOT_AVAILABLE":
        return "Not allocated", "neutral"
    return "Not available", "unavailable"


def status_badge(label, tone="neutral"):
    return f'<span class="tc-badge tc-{escape(tone)}">{escape(display_value(label))}</span>'


def render_metric_card(label, value, detail="", tone="neutral"):
    value_text = escape(display_value(value))
    detail_html = f'<div class="tc-metric-detail">{escape(str(detail))}</div>' if detail else ""
    st.markdown(
        f'<div class="tc-metric tc-{escape(tone)}"><div class="tc-metric-label">{escape(label)}</div>'
        f'<div class="tc-metric-value">{value_text}</div>{detail_html}</div>',
        unsafe_allow_html=True,
    )


def render_section_header(title, subtitle="", badge=None, badge_tone="neutral"):
    badge_html = status_badge(badge, badge_tone) if badge else ""
    subtitle_html = f'<div class="tc-section-copy">{escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f'<div class="tc-section"><div><div class="tc-section-title">{escape(title)}</div>{subtitle_html}</div>{badge_html}</div>',
        unsafe_allow_html=True,
    )


def render_empty_state(title, body):
    st.markdown(
        f'<div class="tc-empty"><div class="tc-empty-title">{escape(title)}</div>'
        f'<div class="tc-empty-copy">{escape(body)}</div></div>',
        unsafe_allow_html=True,
    )


def render_context_card(title, value, detail="", tone="neutral", badge=None):
    badge_html = status_badge(badge, tone) if badge else ""
    st.markdown(
        f'<div class="tc-card"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">'
        f'<div class="tc-card-title">{escape(title)}</div>{badge_html}</div>'
        f'<div class="tc-value">{escape(display_value(value))}</div>'
        f'<div class="tc-card-copy">{escape(str(detail))}</div></div>',
        unsafe_allow_html=True,
    )


def render_market_risk_card(context):
    """Render C3 data as a compact informational consumer card."""
    level = context.get("overall_level", "NOT_AVAILABLE")
    coverage = context.get("source_coverage_status", "INSUFFICIENT")
    level_tone = "bad" if level in {"HIGH", "SEVERE"} else "warn" if level == "ELEVATED" else "unavailable" if level == "NOT_AVAILABLE" else "neutral"
    coverage_tone = "good" if coverage == "SUFFICIENT" else "warn" if coverage == "PARTIAL" else "unavailable"
    age = context.get("snapshot_age_minutes")
    age_text = "Not available" if age is None else f"{age} min ago"
    render_section_header("Market risk", "Forward-looking broad-market context", "Informational", "neutral")
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        render_metric_card("Market risk", display_value(level).replace("_", " "), "Broad-market context", level_tone)
    with c2:
        render_metric_card("Coverage", display_value(coverage), "Source coverage", coverage_tone)
    with c3:
        render_metric_card("Last updated", age_text, f"{context.get('active_event_count', 0)} active broad risks")
    note = context.get("gap_risk_note", "Market Risk data unavailable.")
    render_context_card("Gap-risk note", note, "Does not alter qualification, allocation, sizing, or stops.", level_tone)
    for event in context.get("top_events", [])[:3]:
        title = escape(display_value(event.get("headline_or_short_title")))
        meta = " · ".join(
            [display_value(event.get("category")), display_value(event.get("materiality")), display_value(event.get("freshness"))]
        )
        st.markdown(f'<div class="tc-event"><div class="tc-event-title">{title}</div><div class="tc-event-meta">{escape(meta)}</div></div>', unsafe_allow_html=True)
