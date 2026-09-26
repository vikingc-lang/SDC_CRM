"""Ambient Quick-Log ingestion engine (Functional Solution Specification section 8).

Raw notes / emails / dictations -> validated ``QuickLogResponse``.

The configured private LLM is tried first with the specification's system
prompt and JSON-schema enforcement. If no model is configured or the model
fails, a deterministic extractor produces the same contract so the product
works fully offline.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import List, Literal, Optional

from dateutil import parser as dateparser
from pydantic import BaseModel

from app.schemas.ai import ActionItem, ContactExtracted, DealExtracted, QuickLogResponse
from app.services import llm

SYSTEM_PROMPT = """You are Aiden, the AI assistant in the Cirra CRM, acting as its deterministic data extraction engine.
Your task is to parse raw, unformatted sales notes and return a valid JSON object matching the schema below.
Follow these rules strictly:
1. Extract or infer: Account name, contacts mentioned, deal values, timeline, and next action items.
2. For "buying_role", select exactly one: 'Champion', 'Decision Maker', 'Economic Buyer', 'Blocker', 'Evaluator'
3. For "sentiment", select exactly one: 'positive', 'neutral', 'negative'.
4. Do NOT hallucinate values. If not mentioned, set fields to null or empty lists.
5. Return ONLY pure JSON without markdown wrappers or conversational preamble.
JSON SCHEMA EXPECTED:
{
  "account_name": string | null,
  "domain": string | null,
  "contacts": [
    {"first_name": string, "last_name": string, "job_title": string | null, "email": string | null, "buying_role": string}
  ],
  "deal": {
    "title": string | null,
    "amount": number | null,
    "target_close_date": "YYYY-MM-DD" | null,
    "suggested_stage": "Discovery" | "Pain Fit" | "Solution Demo" | "Proposal/InfoSec" | null
  },
  "activity": {
    "activity_type": "meeting" | "call" | "note" | "email",
    "summary": string,
    "action_items": [{"task": string, "due_date": "YYYY-MM-DD" | null}],
    "sentiment": "positive" | "neutral" | "negative"
  }
}"""


# ---- LLM wire contract (mirrors the JSON schema in the system prompt) -------
class _LLMContact(BaseModel):
    first_name: str
    last_name: str
    job_title: Optional[str] = None
    email: Optional[str] = None
    buying_role: Literal["Champion", "Decision Maker", "Economic Buyer", "Blocker", "Evaluator"]


class _LLMDeal(BaseModel):
    title: Optional[str] = None
    amount: Optional[float] = None
    target_close_date: Optional[str] = None
    suggested_stage: Optional[Literal["Discovery", "Pain Fit", "Solution Demo", "Proposal/InfoSec"]] = None


class _LLMActionItem(BaseModel):
    task: str
    due_date: Optional[str] = None


class _LLMActivity(BaseModel):
    activity_type: Literal["meeting", "call", "note", "email"]
    summary: str
    action_items: List[_LLMActionItem]
    sentiment: Literal["positive", "neutral", "negative"]


class _LLMQuickLog(BaseModel):
    account_name: Optional[str] = None
    domain: Optional[str] = None
    contacts: List[_LLMContact]
    deal: Optional[_LLMDeal] = None
    activity: _LLMActivity


def _safe_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _from_llm(out: _LLMQuickLog) -> QuickLogResponse:
    contacts = []
    for c in out.contacts:
        try:
            contacts.append(ContactExtracted.model_validate(c.model_dump()))
        except ValueError:
            contacts.append(ContactExtracted(**{**c.model_dump(), "email": None}))
    deal = None
    if out.deal and any([out.deal.title, out.deal.amount, out.deal.target_close_date, out.deal.suggested_stage]):
        deal = DealExtracted(
            title=out.deal.title,
            amount=out.deal.amount,
            target_close_date=_safe_date(out.deal.target_close_date),
            suggested_stage=out.deal.suggested_stage,
        )
    return QuickLogResponse(
        account_name=out.account_name,
        domain=(out.domain or "").lower() or None,
        contacts=contacts,
        deal=deal,
        activity_type=out.activity.activity_type,
        summary=out.activity.summary,
        action_items=[ActionItem(task=a.task, due_date=_safe_date(a.due_date)) for a in out.activity.action_items],
        sentiment=out.activity.sentiment,
        engine=llm.provider_name(),
    )


async def extract(raw_text: str, known_accounts: list[tuple[str, str]] | None = None, today: date | None = None) -> QuickLogResponse:
    """Parse notes into a QuickLogResponse. ``known_accounts`` = [(name, domain)]."""
    today = today or date.today()
    result: QuickLogResponse | None = None
    parsed = await llm.complete_json(
        SYSTEM_PROMPT,
        f"Today's date is {today.isoformat()}.\n\nRAW SALES NOTES:\n{raw_text}",
        _LLMQuickLog,
    )
    if parsed is not None:
        result = _from_llm(parsed)
    if result is None:
        result = heuristic_extract(raw_text, known_accounts or [], today)
    # Deterministic signals are cheap and always useful to the UI.
    result.signals = {**detect_signals(raw_text), **result.signals}
    return result


# =============================================================================
# Deterministic extractor
# =============================================================================
COMPETITORS = [
    "Salesforce", "HubSpot", "Microsoft Dynamics", "Dynamics 365", "Pipedrive", "Zoho", "SAP", "Oracle",
    "Freshworks", "Monday.com", "Attio", "Copper", "Close.com", "Zendesk Sell", "Insightly",
]
_TITLE_WORDS = (
    r"VP|SVP|EVP|AVP|Vice President|Director|Head|Chief|CEO|CFO|CTO|CIO|COO|CISO|CRO|CMO|President|Founder|"
    r"Co-Founder|Manager|Lead|Principal|Engineer|Architect|Analyst|Controller|Counsel|Officer|Owner|Specialist|"
    r"Buyer|Procurement|Partner|Administrator|Coordinator|Consultant|Treasurer|Comptroller"
)
_TITLE_RE = re.compile(rf"\b(?:{_TITLE_WORDS})\b")
_NAME = r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
_PERSON_RE = re.compile(rf"\b({_NAME})\s+((?:O'|Mc|Mac|De|Van )?[A-Z][a-zA-Z'\-]+)\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_GENERIC_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com", "proton.me", "protonmail.com"}
_NOT_NAMES = {
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
    "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Next", "Action",
    "Items", "Pain", "Fit", "Solution", "Demo", "Proposal", "Closed", "Discovery", "Follow", "The", "They", "We",
    "Our", "Their", "This", "That", "Met", "Call", "Meeting", "Notes", "Great", "Good", "Also", "Need", "Send",
    "Security", "Review", "Legal", "Team", "Todo", "Steps", "Budget", "Pricing", "Q1", "Q2", "Q3", "Q4", "Mr", "Ms",
    "Mrs", "Dr", "Just", "Quick", "Spoke", "Had", "Sync", "Update", "Deal", "Account", "Contract", "North",
    "South", "East", "West", "New", "San", "Los", "Las", "United", "States", "Supply", "Chain", "Industrial",
    "Analytics", "Platform", "Cloud", "Data", "Sales", "Customer", "Success", "Finance", "Operations",
} | {c.split()[0] for c in COMPETITORS}

_POSITIVE = {
    "great", "excited", "love", "loved", "happy", "impressed", "strong", "positive", "enthusiastic", "aligned",
    "agreed", "approved", "keen", "interested", "productive", "excellent", "promising", "win", "champion",
    "supportive", "thrilled", "confident", "green", "momentum", "ready", "yes", "resonated", "fantastic",
}
_NEGATIVE = {
    "concern", "concerned", "concerns", "worried", "unhappy", "frustrated", "angry", "delay", "delayed", "pushback",
    "blocker", "blocked", "risk", "risky", "expensive", "cancel", "churn", "skeptical", "objection", "objections",
    "stalled", "slip", "slipped", "postponed", "lost", "problem", "issue", "issues", "disappointed", "cut", "freeze",
    "competitor", "negative", "no-show", "ghosted", "unresponsive", "tight", "hesitant", "doubt", "doubts",
}
_ACTION_LEADS = re.compile(
    r"^(?:next steps?|action items?|todo|to-do|to do|follow[- ]?ups?|ai)\s*[:\-–]\s*", re.IGNORECASE
)
_ACTION_VERBS = re.compile(
    r"\b(?:need to|needs to|will|i'll|we'll|i will|we will|going to|should|must|to send|follow up|follow-up|"
    r"send|schedule|book|set up|share|prepare|draft|loop in|get back|circle back|confirm|review|deliver|submit)\b",
    re.IGNORECASE,
)
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTHS}(?:,?\s+\d{{4}})?\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
]
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    for line in re.split(r"\n+", text):
        line = line.strip().lstrip("-*•·>").strip()
        if not line:
            continue
        parts.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", line) if s.strip())
    return parts


def _parse_date(fragment: str, today: date) -> date | None:
    f = fragment.lower()
    if "tomorrow" in f:
        return today + timedelta(days=1)
    if re.search(r"\bnext week\b", f):
        return today + timedelta(days=7)
    m = re.search(r"\bin (\d+|a|one|two|three|four|six) (day|week|month)s?\b", f)
    if m:
        n = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "six": 6}.get(m.group(1)) or int(m.group(1))
        return today + timedelta(days=n * {"day": 1, "week": 7, "month": 30}[m.group(2)])
    if re.search(r"\b(end of (the )?month|eom)\b", f):
        nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt - timedelta(days=1)
    m = re.search(r"\b(?:end of (?:the )?quarter|eoq|end of q([1-4])|by q([1-4])|in q([1-4]))\b", f)
    if m:
        q = next((int(g) for g in m.groups() if g), (today.month - 1) // 3 + 1)
        year = today.year + (1 if q < (today.month - 1) // 3 + 1 else 0)
        end_month = q * 3
        return (date(year, end_month, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    m = re.search(r"\b(?:end of (?:the )?year|eoy|year[- ]end)\b", f)
    if m:
        return date(today.year, 12, 31)
    m = re.search(r"\b(next|this|by|on)?\s*(" + "|".join(_WEEKDAYS) + r")\b", f)
    if m:
        target = _WEEKDAYS.index(m.group(2))
        delta = (target - today.weekday()) % 7 or 7
        if m.group(1) == "next" and delta < 7:
            delta += 7 if (target - today.weekday()) % 7 == 0 else 0
        return today + timedelta(days=delta)
    for pat in _DATE_PATTERNS:
        m = pat.search(fragment)
        if m:
            try:
                d = dateparser.parse(m.group(0), default=dateparser.parse(today.isoformat()), fuzzy=True).date()
            except (ValueError, OverflowError):
                continue
            if d < today - timedelta(days=60) and not re.search(r"\d{4}", m.group(0)):
                d = d.replace(year=d.year + 1)
            return d
    return None


def _amounts(text: str) -> list[float]:
    found: list[float] = []
    pattern = re.compile(
        r"(?:\$|usd\s?|€|£)\s?(\d[\d,]*(?:\.\d+)?)\s*(k|m|mm|bn|million|thousand|grand)?\b"
        r"|\b(\d[\d,]*(?:\.\d+)?)\s*(k|m|mm|million|thousand)\s*(?:usd|dollars|arr|acv|tcv|deal|contract|budget|/yr|per year)?\b",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").lower()
        value *= {"k": 1e3, "thousand": 1e3, "grand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "bn": 1e9}.get(unit, 1)
        if value >= 100:
            found.append(value)
    return found


def _sentiment(text: str) -> str:
    words = re.findall(r"[a-z\-']+", text.lower())
    score = 0
    for i, w in enumerate(words):
        negated = i > 0 and words[i - 1] in {"not", "no", "never", "isn't", "wasn't", "don't", "didn't"}
        if w in _POSITIVE:
            score += -1 if negated else 1
        elif w in _NEGATIVE:
            score += 1 if negated else -1
    if score >= 2:
        return "positive"
    if score <= -2:
        return "negative"
    return "neutral"


def _activity_type(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(e-?mail(ed)?|replied|inbox|wrote back|subject:)\b", t):
        return "email"
    if re.search(r"\b(call|called|phone|dialed|dial|voicemail)\b", t):
        return "call"
    if re.search(r"\b(meeting|met|demo|onsite|on-site|workshop|lunch|dinner|zoom|teams|visit|session|presentation)\b", t):
        return "meeting"
    return "note"


def _suggest_stage(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(proposal|pricing|quote|security review|infosec|legal|redlines?|msa|soc ?2|procurement|questionnaire)\b", t):
        return "Proposal/InfoSec"
    if re.search(r"\b(demo|demoed|walkthrough|poc|pilot|trial|evaluation criteria|proof of concept)\b", t):
        return "Solution Demo"
    if re.search(r"\b(pain|challenge|struggl\w*|problem|bottleneck|economic buyer|champion|budget)\b", t):
        return "Pain Fit"
    return "Discovery"


def _buying_role(context: str, title: str | None) -> str:
    c = context.lower()
    t = (title or "").lower()
    if re.search(r"\b(blocker|skeptic\w*|pushback|push back|resist\w*|against|concern\w*|objection\w*)\b", c):
        return "Blocker"
    if re.search(r"\bchampion\w*\b|\binternal advocate\b|\bsponsor\b", c):
        return "Champion"
    if re.search(r"\b(economic buyer|controls (the )?budget|owns (the )?budget|signs the check|holds the purse)\b", c) or re.search(
        r"\b(cfo|finance|treasurer|controller|comptroller)\b", t
    ):
        return "Economic Buyer"
    if re.search(r"\b(decision[- ]maker|final say|signs off|sign-off|final approv\w*|approver)\b", c) or re.search(
        r"\b(ceo|coo|president|chief|founder|svp|evp)\b", t
    ):
        return "Decision Maker"
    if re.search(r"\binfluenc\w*\b", c):
        return "Influencer"
    return "Evaluator"


def _clean_title(raw: str) -> str | None:
    raw = raw.strip(" ,.;:()-–")
    raw = re.sub(r"^(?:the|their|our|a|an|is|who is|is the)\s+", "", raw, flags=re.IGNORECASE)
    raw = re.split(r"\s+(?:at|from|of the company|who|and|,)\s+", raw, maxsplit=1)[0]
    if not _TITLE_RE.search(raw) or len(raw) > 60:
        return None
    return raw.strip()


def _contacts(text: str, sentences: list[str], account_name: str | None) -> list[ContactExtracted]:
    emails = [m.group(0) for m in _EMAIL_RE.finditer(text)]
    account_words = set((account_name or "").split())
    seen: dict[str, ContactExtracted] = {}
    for sent in sentences:
        for m in _PERSON_RE.finditer(sent):
            first, last = m.group(1), m.group(2)
            if first in _NOT_NAMES or last in _NOT_NAMES or first in account_words or last in account_words:
                continue
            if _TITLE_RE.fullmatch(first) or _TITLE_RE.fullmatch(last):
                continue
            key = f"{first} {last}".lower()
            after = sent[m.end(): m.end() + 70]
            before = sent[max(0, m.start() - 50): m.start()]
            title = None
            ta = re.match(r"\s*(?:,|\(|-|–|—)\s*([^,()\n.;]{2,60})", after)
            if ta:
                title = _clean_title(ta.group(1))
            if not title:
                tb = re.search(rf"((?:{_TITLE_WORDS})[\w &/,-]{{0,40}}?)\s*,?\s*$", before)
                if tb:
                    title = _clean_title(tb.group(1))
            email = next(
                (e for e in emails if first.lower() in e.lower().split("@")[0] or last.lower() in e.lower().split("@")[0]),
                None,
            )
            role = _buying_role(sent, title)
            if key in seen:
                existing = seen[key]
                existing.job_title = existing.job_title or title
                if existing.buying_role == "Evaluator" and role != "Evaluator":
                    existing.buying_role = role
                continue
            seen[key] = ContactExtracted(first_name=first, last_name=last, job_title=title, email=email, buying_role=role)
    contacts = list(seen.values())[:8]
    # Roles are often stated later using only a first name ("Elena is our champion").
    firsts = {c.first_name for c in contacts}
    for contact in contacts:
        if contact.buying_role != "Evaluator":
            continue
        for sent in sentences:
            mentioned = {f for f in firsts if re.search(rf"\b{re.escape(f)}\b", sent)}
            if mentioned == {contact.first_name}:
                role = _buying_role(sent, contact.job_title)
                if role != "Evaluator":
                    contact.buying_role = role
                    break
    return contacts


def _account(text: str, known: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    lowered = text.lower()
    for name, domain in known:
        if name.lower() in lowered or (domain and domain.lower() in lowered):
            return name, domain
    # partial match on the distinctive first word of a known account (e.g. "Apex")
    for name, domain in known:
        first = name.split()[0]
        if len(first) >= 4 and first.lower() not in {"the", "global"} and re.search(rf"\b{re.escape(first)}\b", text):
            return name, domain
    domain = None
    for m in _EMAIL_RE.finditer(text):
        if m.group(1).lower() not in _GENERIC_DOMAINS:
            domain = m.group(1).lower()
            break
    if not domain:
        m = re.search(r"\b((?:www\.)?[a-z0-9-]+\.(?:com|io|co|ai|net|org|dev|app|biz|us|uk|de))\b", lowered)
        if m:
            domain = m.group(1).removeprefix("www.")
    name = None
    allowed_lead = _NOT_NAMES - {"North", "South", "East", "West", "New", "Supply", "Industrial", "Analytics", "Cloud", "Data"}
    for m in re.finditer(
        r"\b(at|with|from|for|client|customer|prospect|account)\s*:?\s+((?:[A-Z][\w&'-]*|&)(?:\s+(?:[A-Z][\w&'-]*|&)){0,3})",
        text,
    ):
        lead, candidate = m.group(1), m.group(2).strip()
        words = candidate.split()
        # "at X"/"from X" almost always introduce an organisation; "with X" is usually a person.
        if lead in ("with", "for") and len(words) == 2 and _PERSON_RE.fullmatch(candidate) and not re.search(
            r"(Inc|Corp|LLC|Ltd|Group|Logistics|Systems|Labs|Supply|Industries|Partners|Health|Bank|Foods|Energy|Tech|Software)$", candidate
        ):
            continue
        if words[0] in allowed_lead or _TITLE_RE.fullmatch(words[0]):
            continue
        name = candidate
        break
    if not name and domain:
        name = domain.split(".")[0].replace("-", " ").title()
    return name, domain


def _action_items(sentences: list[str], today: date) -> list[ActionItem]:
    items: list[ActionItem] = []
    in_list = False
    for sent in sentences:
        is_header = bool(_ACTION_LEADS.match(sent))
        body = _ACTION_LEADS.sub("", sent).strip()
        if is_header and not body:
            in_list = True
            continue
        if is_header or in_list or _ACTION_VERBS.search(sent):
            for part in re.split(r";\s*|\s+and then\s+", body):
                part = part.strip().rstrip(".")
                if len(part) < 6:
                    continue
                task = re.sub(r"^(?:i|we|i'll|we'll|i will|we will|need to|needs to|going to)\s+", "", part, flags=re.IGNORECASE)
                task = task[:1].upper() + task[1:]
                items.append(ActionItem(task=task[:300], due_date=_parse_date(part, today)))
    unique: dict[str, ActionItem] = {}
    for item in items:
        unique.setdefault(item.task.lower(), item)
    return list(unique.values())[:6]


def _deal(text: str, sentences: list[str], account_name: str | None, today: date) -> DealExtracted | None:
    amounts = _amounts(text)
    close = None
    for sent in sentences:
        if re.search(r"\b(close|closing|sign|signature|go[- ]live|decision|contract|po\b|purchase order|budget cycle|by end)", sent, re.IGNORECASE):
            close = _parse_date(sent, today)
            if close:
                break
    title = None
    product = re.search(
        r"\b((?:[A-Z][\w/-]*\s){1,4}(?:Platform|Solution|Suite|Module|Pilot|Upgrade|Renewal|Expansion|Integration|System|Analytics|Rollout|Implementation))\b",
        text,
    )
    m = product or re.search(
        r"\b(?:interested in|excited about|evaluating|looking at|looking for|need(?:s)? an?|want(?:s)? an?|for (?:a|an|the|their))\s+"
        r"((?:[A-Za-z][\w/-]*\s){0,4}?(?:platform|solution|project|rollout|implementation|suite|module|pilot|upgrade|license|licenses|renewal|expansion|integration|system|tool|analytics))\b",
        text,
        re.IGNORECASE,
    )
    if m:
        words = [w for w in m.group(1).split() if w.lower() not in {"the", "a", "an", "their", "our"}]
        title = " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words)
    elif amounts or close:
        title = f"{account_name} Opportunity" if account_name else None
    if not (title or amounts or close):
        return None
    return DealExtracted(
        title=title,
        amount=max(amounts) if amounts else None,
        target_close_date=close,
        suggested_stage=_suggest_stage(text),
    )


def _summary(sentences: list[str]) -> str:
    body = [s for s in sentences if not _ACTION_LEADS.match(s)]
    text = " ".join(body[:2]) if body else " ".join(sentences[:1])
    return text[:400] + ("…" if len(text) > 400 else "")


def detect_signals(text: str) -> dict:
    lowered = text.lower()
    competitors = [c for c in COMPETITORS if re.search(rf"\b{re.escape(c.lower())}\b", lowered)]
    pains = [
        s for s in _sentences(text)
        if re.search(r"\b(pain|struggl\w*|challenge\w*|manual|spreadsheet\w*|bottleneck\w*|slow|errors?|visibility|costly|waste\w*|churn)\b", s, re.IGNORECASE)
    ]
    risks = [
        s for s in _sentences(text)
        if re.search(r"\b(concern\w*|risk\w*|delay\w*|pushback|blocker|freeze|cut|slip\w*|objection\w*|competitor\w*)\b", s, re.IGNORECASE)
    ]
    return {"competitors": competitors, "pain_points": pains[:5], "risks": risks[:5]}


def heuristic_extract(raw_text: str, known_accounts: list[tuple[str, str]], today: date) -> QuickLogResponse:
    text = raw_text.strip()
    sentences = _sentences(text)
    account_name, domain = _account(text, known_accounts)
    return QuickLogResponse(
        account_name=account_name,
        domain=domain,
        contacts=_contacts(text, sentences, account_name),
        deal=_deal(text, sentences, account_name, today),
        activity_type=_activity_type(text),
        summary=_summary(sentences) or text[:400],
        action_items=_action_items(sentences, today),
        sentiment=_sentiment(text),
        engine="heuristic",
    )
