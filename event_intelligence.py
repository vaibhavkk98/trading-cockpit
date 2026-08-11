"""P3A live, informational Event Intelligence.  It never affects trading decisions."""
from __future__ import annotations

import email.utils
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sentiment_agent import fetch_google_news_rss
from bse_announcements import fetch_bse_announcements

UNKNOWN = "UNKNOWN"
NOT_AVAILABLE = "NOT_AVAILABLE"
SOURCE_AUTHORITY = {"EXCHANGE_FILING": 1.0, "COMPANY_IR": .9, "FINANCIAL_NEWS": .7, "SECONDARY": .4}
HIGH_QUALITY_PUBLISHERS = ("reuters", "economic times", "business standard", "mint", "moneycontrol", "cnbc", "livemint")

@dataclass
class Event:
    symbol: str; event_time: Optional[str]; source: str; source_type: str; source_url_or_id: str; headline: str
    scope: str = "COMPANY"; event_type: str = "OTHER MATERIAL EVENT"; direction: str = UNKNOWN
    materiality: str = UNKNOWN; confidence: Optional[float] = None; freshness_hours: Optional[float] = None
    novelty: str = "NEW"; surprise: str = UNKNOWN; expected_horizon: str = UNKNOWN; summary: str = NOT_AVAILABLE
    evidence: str = NOT_AVAILABLE; market_reaction: str = NOT_AVAILABLE; priced_in_assessment: str = NOT_AVAILABLE
    direction_rationale: str = NOT_AVAILABLE; supporting_sources: List[Dict[str, Any]] = None
    def to_dict(self) -> Dict[str, Any]: return asdict(self)

def _time(value: str) -> Optional[datetime]:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception: return None

def _publisher(headline: str) -> str:
    return headline.rsplit(" - ", 1)[-1].strip() if " - " in headline else "Google News RSS"

def _classify(headline: str) -> tuple[str, str, str, str]:
    """Classify only explicit event descriptions; otherwise preserve UNKNOWN."""
    text = headline.lower()
    rules = [
        (r"\b(order|contract|award|tender)\b", "ORDER / CONTRACT", "WEEKS"),
        (r"\b(earnings|quarterly|q[1-4]|results|guidance)\b", "EARNINGS / GUIDANCE", "DAYS"),
        (r"\b(buyback|dividend|split|bonus|rights issue)\b", "CORPORATE ACTION / CAPITAL ALLOCATION", "DAYS"),
        (r"\b(debt|default|funding|loan|bond)\b", "FUNDING / DEBT / BALANCE SHEET", "WEEKS"),
        (r"\b(acquisition|merger|partnership|joint venture)\b", "M&A / STRATEGIC PARTNERSHIP", "MONTHS"),
        (r"\b(capacity|plant|launch|production|operations)\b", "OPERATIONS / CAPACITY / PRODUCT", "WEEKS"),
        (r"\b(resigns|appointment|ceo|board|governance)\b", "MANAGEMENT / GOVERNANCE", "WEEKS"),
        (r"\b(promoter|insider|pledge|stake sale)\b", "PROMOTER / OWNERSHIP / INSIDER", "WEEKS"),
        (r"\b(sebi|regulator|penalty|legal|court|probe)\b", "REGULATORY / LEGAL", "WEEKS"),
        (r"\b(rating|upgrade|downgrade)\b", "CREDIT / RATING", "DAYS"),
    ]
    event_type, horizon = "OTHER MATERIAL EVENT", UNKNOWN
    for pattern, candidate_type, candidate_horizon in rules:
        if re.search(pattern, text):
            event_type, horizon = candidate_type, candidate_horizon
            break
    # Direction requires an explicit event outcome, not generic price/headline polarity.
    direction = UNKNOWN
    if re.search(r"\b(wins?|awarded|approves?|raises? dividend|buyback|beat)\b", text): direction = "POSITIVE"
    if re.search(r"\b(default|penalty|probe|resigns?|cuts? guidance|miss)\b", text): direction = "NEGATIVE"
    if re.search(r"\bbut\b|\bmixed\b", text): direction = "MIXED"
    # A headline alone cannot establish size relative to the issuer or verify
    # the underlying disclosure.  Materiality is deliberately unknown until an
    # exchange filing or IR source is available.
    materiality = UNKNOWN
    return event_type, direction, materiality, horizon

def normalize_raw_items(symbol: str, items: Iterable[Dict[str, str]], cutoff: Optional[datetime] = None, now: Optional[datetime] = None) -> List[Event]:
    now = now or datetime.now(timezone.utc); events: List[Event] = []
    for item in items:
        headline, published = item.get("title", "").strip(), _time(item.get("pub_date", ""))
        if not headline or not published or (cutoff and published > cutoff): continue
        publisher = _publisher(headline); source_type = "FINANCIAL_NEWS" if any(p in publisher.lower() for p in HIGH_QUALITY_PUBLISHERS) else "SECONDARY"
        event_type, direction, materiality, horizon = _classify(headline)
        events.append(Event(symbol=symbol.replace(".NS", ""), event_time=published.isoformat(), source=publisher,
            source_type=source_type, source_url_or_id=item.get("link", NOT_AVAILABLE), headline=headline,
            event_type=event_type, direction=direction, materiality=materiality,
            confidence=SOURCE_AUTHORITY[source_type], freshness_hours=round(max(0, (now-published).total_seconds()/3600), 1),
            expected_horizon=horizon, summary=headline, evidence="Published headline; underlying filing/announcement not independently retrieved."))
    return events

def _key(event: Event) -> str:
    words = re.sub(r"[^a-z0-9 ]", "", event.headline.lower()).split()
    stop = {"the", "a", "an", "and", "of", "to", "for", "shares", "stock", "company"}
    return " ".join(w for w in words if w not in stop)[:80]

def deduplicate(events: List[Event]) -> List[Event]:
    """Keep the most authoritative event in an obvious event family."""
    chosen: Dict[str, Event] = {}
    for event in events:
        key = f"{event.symbol}:{event.event_type}:{_key(event)}"
        # Event-type + first material words handles identical syndicated copies conservatively.
        if key not in chosen or SOURCE_AUTHORITY[event.source_type] > SOURCE_AUTHORITY[chosen[key].source_type]: chosen[key] = event
    return list(chosen.values())

def build_event_families(events: List[Event]) -> List[Event]:
    """Collapse exact/syndicated family members; retain evidence links and source precedence."""
    families: Dict[str, Event] = {}
    for event in events:
        key = f"{event.symbol}:{event.event_type}:{_key(event)}"
        current = families.get(key)
        if current is None:
            event.supporting_sources = []
            families[key] = event
            continue
        event.novelty = "DUPLICATE"
        if SOURCE_AUTHORITY[event.source_type] > SOURCE_AUTHORITY[current.source_type]:
            event.supporting_sources = (current.supporting_sources or []) + [{"source": current.source, "source_type": current.source_type, "url": current.source_url_or_id, "headline": current.headline}]
            families[key] = event
        else:
            current.supporting_sources = (current.supporting_sources or []) + [{"source": event.source, "source_type": event.source_type, "url": event.source_url_or_id, "headline": event.headline}]
    return list(families.values())

def add_authoritative_evidence(events: List[Event], evidence_items: Iterable[Dict[str, Any]], cutoff: Optional[datetime] = None) -> List[Event]:
    """Accept provider-supplied NSE/BSE/IR records; no unverified scraping is invented."""
    for item in evidence_items:
        published = _time(item.get("pub_date", ""))
        if not published or (cutoff and published > cutoff): continue
        source_type = item.get("source_type", "SECONDARY")
        if source_type not in SOURCE_AUTHORITY: continue
        typ, direction, _, horizon = _classify(item.get("headline", ""))
        materiality = item.get("materiality", UNKNOWN) if source_type in ("EXCHANGE_FILING", "COMPANY_IR") else UNKNOWN
        if materiality not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"): materiality = UNKNOWN
        events.append(Event(symbol=item["symbol"].replace(".NS", ""), event_time=published.isoformat(), source=item.get("source", source_type), source_type=source_type, source_url_or_id=item.get("url", NOT_AVAILABLE), headline=item.get("headline", ""), event_type=item.get("event_type", typ), direction=item.get("direction", direction), materiality=materiality, confidence=SOURCE_AUTHORITY[source_type], expected_horizon=item.get("horizon", horizon), summary=item.get("summary", item.get("headline", "")), evidence=item.get("evidence", NOT_AVAILABLE), direction_rationale=item.get("direction_rationale", NOT_AVAILABLE)))
    return events

def aggregate(symbol: str, events: List[Event]) -> Dict[str, Any]:
    reliable = [e for e in events if e.source_type in ("EXCHANGE_FILING", "COMPANY_IR", "FINANCIAL_NEWS")]
    if not events: context = "NOT_AVAILABLE"
    elif not reliable: context = "NOT_AVAILABLE"
    else:
        directions = {e.direction for e in reliable if e.materiality in ("MEDIUM", "HIGH", "CRITICAL")}
        context = "MIXED" if "POSITIVE" in directions and "NEGATIVE" in directions else (next(iter(directions)) if directions else "NO_MATERIAL_EVENT")
    material = [e for e in reliable if e.materiality in ("MEDIUM", "HIGH", "CRITICAL")]
    top = max(material, key=lambda e: (SOURCE_AUTHORITY[e.source_type], e.event_time or ""), default=None)
    return {"event_context": context, "event_materiality": top.materiality if top else NOT_AVAILABLE,
        "event_confidence": top.confidence if top else None, "event_summary": top.summary if top else NOT_AVAILABLE,
        "event_type": top.event_type if top else NOT_AVAILABLE, "event_time": top.event_time if top else NOT_AVAILABLE,
        "event_source": top.source if top else NOT_AVAILABLE, "event_primary_source": top.source if top else NOT_AVAILABLE, "event_supporting_sources": top.supporting_sources if top else [], "event_novelty": top.novelty if top else NOT_AVAILABLE, "event_direction": top.direction if top else UNKNOWN, "event_market_reaction": top.market_reaction if top else NOT_AVAILABLE, "event_horizon": top.expected_horizon if top else UNKNOWN,
        "event_risk_flags": [e.headline for e in material if e.direction in ("NEGATIVE", "MIXED")],
        "material_event_count": len(material), "highest_materiality": top.materiality if top else NOT_AVAILABLE,
        "top_event": top.to_dict() if top else None, "supporting_events": [e.to_dict() for e in reliable if e is not top]}

class EventIntelligenceService:
    provider_name = "Google News RSS (secondary-source discovery)"
    def get_context(self, symbol: str, cutoff: Optional[datetime] = None, authoritative_items: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
        cutoff = cutoff or datetime.now(timezone.utc)
        raw = fetch_google_news_rss(symbol, max_items=10)
        events = normalize_raw_items(symbol, raw, cutoff=cutoff)
        checked, official = fetch_bse_announcements(symbol, cutoff)
        events = add_authoritative_evidence(events, list(authoritative_items or []) + official, cutoff)
        events = build_event_families(events)
        result=aggregate(symbol, events)
        if not checked and not any(e.source_type in ("EXCHANGE_FILING","COMPANY_IR") for e in events): result["event_context"]="NOT_AVAILABLE"
        elif checked and not any(e.source_type in ("EXCHANGE_FILING","COMPANY_IR") and e.materiality in ("LOW","MEDIUM","HIGH","CRITICAL") for e in events): result["event_context"]="NO_MATERIAL_EVENT"
        return {**result, "authoritative_checked": checked, "raw_item_count": len(raw), "deduplicated_event_count": len(events), "events": [e.to_dict() for e in events]}
    def enrich_candidates(self, candidates: List[Dict[str, Any]], cutoff: Optional[datetime] = None) -> List[Dict[str, Any]]:
        for candidate in candidates: candidate.update(self.get_context(candidate["symbol"], cutoff))
        return candidates
