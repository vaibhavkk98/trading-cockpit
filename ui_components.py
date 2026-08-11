"""Central presentation system for the Trading Cockpit V1.1 light product UI."""

from html import escape
from numbers import Number

import streamlit as st


NOT_AVAILABLE = {None, "", "NOT_AVAILABLE", "NOT AVAILABLE", "N/A", "None", "null"}


def _valid_number(value):
    return isinstance(value, Number) and not isinstance(value, bool) and value == value


def apply_theme():
    """Install the V1.1 light institutional analytics visual system."""
    st.markdown(
        """
        <style>
          :root {
            --tc-bg:#F6F8FB; --tc-surface:#FFFFFF; --tc-surface-subtle:#F8FAFC;
            --tc-line:#E2E8F0; --tc-line-strong:#D9E1EB; --tc-text:#172033;
            --tc-muted:#64748B; --tc-muted-light:#94A3B8; --tc-blue:#456FE8;
            --tc-blue-soft:#EEF3FF; --tc-green:#16835A; --tc-green-soft:#EDF8F2;
            --tc-amber:#B7791F; --tc-amber-soft:#FFF7E8; --tc-red:#C44747;
            --tc-red-soft:#FFF1F1; --tc-radius:11px;
          }
          .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background:var(--tc-bg) !important; color:var(--tc-text) !important; }
          [data-testid="stHeader"] { background:rgba(246,248,251,.94) !important; }
          .main .block-container { max-width:1480px; padding:1.35rem 1.6rem 2.5rem; }
          h1,h2,h3,p,label,span { color:var(--tc-text); }
          h1,h2,h3 { letter-spacing:-.02em; }
          .tc-header { display:flex; justify-content:space-between; align-items:flex-start; gap:18px;
            padding:5px 0 17px; margin-bottom:7px; border-bottom:1px solid var(--tc-line); }
          .tc-title { color:var(--tc-text); font-size:1.6rem; line-height:1.15; font-weight:720; letter-spacing:-.035em; }
          .tc-subtitle { color:var(--tc-muted); margin-top:4px; font-size:.88rem; }
          .tc-meta { color:var(--tc-muted); font-size:.77rem; text-align:right; line-height:1.6; white-space:nowrap; }
          .tc-section { display:flex; justify-content:space-between; align-items:center; gap:12px; margin:1.6rem 0 .7rem; }
          .tc-section-title { font-size:1.04rem; color:var(--tc-text); font-weight:680; letter-spacing:-.012em; }
          .tc-section-copy { color:var(--tc-muted); font-size:.79rem; margin-top:3px; }
          .tc-metric { background:var(--tc-surface); border:1px solid var(--tc-line); border-radius:var(--tc-radius);
            padding:13px 14px 12px; min-height:83px; box-shadow:0 1px 2px rgba(15,23,42,.025); }
          .tc-metric-good { border-top:2px solid var(--tc-green); }
          .tc-metric-warn { border-top:2px solid var(--tc-amber); }
          .tc-metric-bad { border-top:2px solid var(--tc-red); }
          .tc-metric.tc-unavailable { background:var(--tc-surface-subtle); }
          .tc-metric-label { color:var(--tc-muted); font-size:.68rem; font-weight:680; text-transform:uppercase; letter-spacing:.065em; }
          .tc-metric-value { color:var(--tc-text); font-size:1.29rem; font-weight:720; line-height:1.28; margin-top:5px;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
          .tc-metric-detail { color:var(--tc-muted); font-size:.72rem; margin-top:3px; }
          .tc-badge { display:inline-block; border-radius:6px; padding:3px 7px; font-size:.67rem; font-weight:700;
            letter-spacing:.035em; border:1px solid transparent; white-space:nowrap; line-height:1.25; }
          .tc-neutral { color:#45607B; background:#F1F5F9; border-color:#E2E8F0; }
          .tc-good { color:var(--tc-green); background:var(--tc-green-soft); border-color:#CBECDD; }
          .tc-warn { color:var(--tc-amber); background:var(--tc-amber-soft); border-color:#F4DFB2; }
          .tc-bad { color:var(--tc-red); background:var(--tc-red-soft); border-color:#F1CACA; }
          .tc-unavailable { color:#64748B; background:#F1F5F9; border-color:#E2E8F0; }
          .tc-card { background:var(--tc-surface); border:1px solid var(--tc-line); border-radius:var(--tc-radius);
            padding:14px 15px; margin-bottom:10px; box-shadow:0 1px 2px rgba(15,23,42,.02); }
          .tc-card-title { color:var(--tc-text); font-weight:650; font-size:.86rem; }
          .tc-card-copy { color:var(--tc-muted); font-size:.78rem; margin-top:5px; line-height:1.45; }
          .tc-value { color:var(--tc-text); font-size:1.05rem; font-weight:700; margin-top:5px; }
          .tc-empty { background:var(--tc-surface); border:1px dashed #CBD5E1; border-radius:var(--tc-radius); padding:22px;
            text-align:center; margin:10px 0; }
          .tc-empty-title { color:var(--tc-text); font-weight:680; }
          .tc-empty-copy { color:var(--tc-muted); font-size:.84rem; margin-top:5px; }
          .tc-event { padding:8px 0; border-bottom:1px solid var(--tc-line); }
          .tc-event:last-child { border-bottom:none; }
          .tc-event-title { color:var(--tc-text); font-size:.84rem; font-weight:600; line-height:1.35; }
          .tc-event-meta { color:var(--tc-muted); font-size:.71rem; margin-top:2px; }
          .stTabs [data-baseweb="tab-list"] { gap:22px; border-bottom:1px solid var(--tc-line); }
          .stTabs [data-baseweb="tab"] { background:transparent !important; border:none !important; padding:9px 0 10px;
            color:var(--tc-muted) !important; font-size:.84rem; font-weight:620; }
          .stTabs [aria-selected="true"] { color:var(--tc-text) !important; border-bottom:2px solid var(--tc-blue) !important; }
          [data-testid="stBaseButton-primary"] { background:var(--tc-blue) !important; border-color:var(--tc-blue) !important;
            color:#FFF !important; box-shadow:0 1px 2px rgba(69,111,232,.18) !important; }
          [data-testid="stBaseButton-primary"]:hover { background:#365ED2 !important; border-color:#365ED2 !important; }
          [data-testid="stBaseButton-secondary"], button[kind="secondary"] { background:#FFF !important; border-color:#D8E1EC !important; color:#334155 !important; }
          [data-testid="stDataFrame"] { border:1px solid var(--tc-line); border-radius:var(--tc-radius); overflow:hidden; background:#FFF; }
          [data-testid="stExpander"] { border:1px solid var(--tc-line); border-radius:var(--tc-radius); background:var(--tc-surface); }
          [data-testid="stExpander"] summary { color:var(--tc-text) !important; font-weight:600; }
          div[data-testid="stAlert"] { border-radius:var(--tc-radius); }
          [data-baseweb="select"] > div, [data-baseweb="input"] > div, input, textarea { background:#FFF !important; color:var(--tc-text) !important; border-color:#D8E1EC !important; }
          [data-baseweb="select"] span, [data-baseweb="input"] input { color:var(--tc-text) !important; }
          [data-baseweb="menu"] { background:#FFF !important; }
          [data-baseweb="menu"] li { color:var(--tc-text) !important; }
          @media (max-width: 900px) {
            .main .block-container { padding-left:1rem; padding-right:1rem; }
            .tc-header { display:block; }
            .tc-meta { text-align:left; margin-top:8px; white-space:normal; }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def display_value(value, unavailable="Not available", not_applicable="—"):
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
    if status == "ALLOCATED":
        return "Allocated", "good"
    if reason_code == "DUPLICATE_POSITION":
        return "Duplicate", "neutral"
    if reason_code == "CAPITAL_CAP":
        return "Capacity", "neutral"
    if reason_code == "INSUFFICIENT_CASH":
        return "Cash", "neutral"
    if status and status != "NOT_AVAILABLE":
        return "Not allocated", "neutral"
    return "Not available", "unavailable"


def status_badge(label, tone="neutral"):
    return f'<span class="tc-badge tc-{escape(tone)}">{escape(display_value(label))}</span>'


def render_metric_card(label, value, detail="", tone="neutral"):
    detail_html = f'<div class="tc-metric-detail">{escape(str(detail))}</div>' if detail else ""
    metric_tone = f"tc-metric-{escape(tone)}" if tone in {"good", "warn", "bad"} else ""
    st.markdown(
        f'<div class="tc-metric {metric_tone}"><div class="tc-metric-label">{escape(label)}</div>'
        f'<div class="tc-metric-value">{escape(display_value(value))}</div>{detail_html}</div>', unsafe_allow_html=True)


def render_section_header(title, subtitle="", badge=None, badge_tone="neutral"):
    badge_html = status_badge(badge, badge_tone) if badge else ""
    subtitle_html = f'<div class="tc-section-copy">{escape(subtitle)}</div>' if subtitle else ""
    st.markdown(f'<div class="tc-section"><div><div class="tc-section-title">{escape(title)}</div>{subtitle_html}</div>{badge_html}</div>', unsafe_allow_html=True)


def render_empty_state(title, body):
    st.markdown(f'<div class="tc-empty"><div class="tc-empty-title">{escape(title)}</div><div class="tc-empty-copy">{escape(body)}</div></div>', unsafe_allow_html=True)


def render_context_card(title, value, detail="", tone="neutral", badge=None):
    badge_html = status_badge(badge, tone) if badge else ""
    st.markdown(
        f'<div class="tc-card"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">'
        f'<div class="tc-card-title">{escape(title)}</div>{badge_html}</div><div class="tc-value">{escape(display_value(value))}</div>'
        f'<div class="tc-card-copy">{escape(str(detail))}</div></div>', unsafe_allow_html=True)


def render_market_risk_card(context):
    """Compact, neutral institutional market-context surface."""
    level = context.get("overall_level", "NOT_AVAILABLE")
    coverage = context.get("source_coverage_status", "INSUFFICIENT")
    level_tone = "bad" if level in {"HIGH", "SEVERE"} else "warn" if level == "ELEVATED" else "unavailable" if level == "NOT_AVAILABLE" else "neutral"
    coverage_tone = "good" if coverage == "SUFFICIENT" else "warn" if coverage == "PARTIAL" else "unavailable"
    age = context.get("snapshot_age_minutes")
    age_text = "Not available" if age is None else f"{age} min ago"
    render_section_header("Market risk", "Forward-looking broad-market context", "Informational", "neutral")
    c1, c2, c3 = st.columns([1.15, 1, 1])
    with c1:
        render_metric_card("Market risk", display_value(level).replace("_", " "), "Broad-market context", level_tone)
    with c2:
        render_metric_card("Coverage", display_value(coverage), "Source coverage", coverage_tone)
    with c3:
        render_metric_card("Updated", age_text, f"{context.get('active_event_count', 0)} active events")
    for event in context.get("top_events", [])[:3]:
        meta = " · ".join([display_value(event.get("category")), display_value(event.get("materiality")), display_value(event.get("freshness"))])
        st.markdown(f'<div class="tc-event"><div class="tc-event-title">{escape(display_value(event.get("headline_or_short_title")))}</div><div class="tc-event-meta">{escape(meta)}</div></div>', unsafe_allow_html=True)
    st.caption("Does not alter qualification, allocation, sizing, or stops.")
