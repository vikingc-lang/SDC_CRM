"""The help center: role guides, functional areas, process maps, a live data model, search and page context,
plus the help answers Aiden gives to "how do I…" questions (content in app/help/content.py).

Two parts are generated live, so they're always true for this workspace: "what you can do" comes from the role's
permissions, and the data model comes from the schema plus the admin's custom fields and objects (hidden fields
are left out for roles that can't see them)."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base
from app.help import content as C

RESOURCE_LABELS = {
    "accounts": "Accounts", "contacts": "Contacts", "deals": "Opportunities", "activities": "Activities", "tasks": "Tasks",
    "products": "Products & price books", "quotes": "Quotes", "approvals": "Approvals", "documents": "Documents & e-signature",
    "contracts": "Contracts", "success": "Customer success", "finance": "Finance & ERP", "partners": "Partners", "reports": "Reports",
    "admin": "Administration", "audit": "Audit trail", "data": "Import / export", "leads": "Leads", "orders": "Orders", "cases": "Cases",
    "knowledge": "Knowledge", "campaigns": "Campaigns", "custom_objects": "Custom objects",
}
HELP_INTENT = re.compile(r"\b(how (do|can|to|does)|where (do|can|is)|what (is|are|does)|what's|can i|help|explain|meaning of|"
                         r"difference between|why (does|is|can't|cannot)|guide|set ?up|configure|shortcut)\b", re.I)
_TECHNICAL = re.compile(r"(^id$|_id$|_at$|embedding|search_tsv|_encrypted|token|hash|secret|^custom_fields$|^custom_metadata$|"
                        r"external_id|ai_insights|risk_factors|_json$)")


def _words(text: str) -> list[str]:
    stop = {"how", "do", "i", "a", "an", "the", "to", "can", "what", "is", "are", "in", "of", "on", "my", "for", "does", "where", "and",
            "with", "it", "this", "that", "me", "you", "cirra", "aiden", "please", "help"}
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in stop and len(w) > 1]


def _stem(w: str) -> str:
    for suf in ("ing", "es", "s", "ed"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _items(role: str | None) -> list[dict]:
    """Every searchable piece of help, with a link to where it lives."""
    out = []
    for a in C.AREAS:
        out.append({"kind": "area", "title": a["title"], "snippet": a["summary"], "href": f"/help/areas/{a['key']}", "area": a["key"],
                    "text": " ".join([a["title"], a["summary"], *a["capabilities"]]), "roles": a["roles"], "page": a["pages"][0]})
        for i, h in enumerate(a["howto"]):
            out.append({"kind": "howto", "title": h["q"], "snippet": " → ".join(h["steps"]), "href": f"/help/areas/{a['key']}#howto-{i}",
                        "area": a["key"], "text": " ".join([h["q"], *h["steps"], a["title"]]), "roles": a["roles"], "steps": h["steps"],
                        "page": a["pages"][0]})
    for p in C.PROCESSES:
        out.append({"kind": "process", "title": f"Process: {p['title']}", "snippet": p["summary"], "href": f"/help?tab=processes&p={p['key']}",
                    "text": " ".join([p["title"], p["summary"], *(s["label"] + " " + s["detail"] for s in p["steps"])]), "roles": []})
    for term, meaning in C.GLOSSARY.items():
        out.append({"kind": "glossary", "title": term, "snippet": meaning, "href": "/help?tab=glossary", "text": f"{term} {meaning}", "roles": []})
    return out


def search(query: str, role: str | None = None, limit: int = 8) -> list[dict]:
    words = [_stem(w) for w in _words(query)]
    if not words:
        return []
    scored = []
    for item in _items(role):
        title, text = item["title"].lower(), item["text"].lower()
        s = sum(3 for w in words if w in title) + sum(1 for w in words if w in text)
        if s == 0:
            continue
        if item["kind"] == "howto":
            s += 1.5  # answers "how do I" better than an overview
        if role and item["roles"] and role in item["roles"]:
            s += 1
        if item["kind"] == "glossary" and not re.search(r"what (is|are|does)|mean", query, re.I):
            s -= 1
        scored.append((s, item))
    scored.sort(key=lambda t: -t[0])
    return [{k: v for k, v in i.items() if k != "text"} for s, i in scored[:limit] if s > 0]


def context(path: str, role: str | None = None) -> dict:
    """Help for the page at ``path``: the matching areas (longest page prefix first) and their how-tos."""
    path = (path or "/").split("?")[0]
    matches = []
    for a in C.AREAS:
        for pg in a["pages"]:
            base = pg.split("?")[0]
            if (base == "/" and path == "/") or (base != "/" and (path == base or path.startswith(base + "/"))):
                matches.append((len(base), a))
    matches.sort(key=lambda t: -t[0])
    areas = [a for _, a in matches][:2]
    if not areas:
        areas = [next(a for a in C.AREAS if a["key"] == "home")]
    return {"areas": [area_out(a) for a in areas], "role": role_out(role) if role else None}


def area_out(a: dict) -> dict:
    return {**a, "processes": [{"key": p["key"], "title": p["title"]} for p in C.PROCESSES
                               if any(s["page"].split("?")[0] in a["pages"] for s in p["steps"])]}


def role_out(role: str) -> dict:
    r = C.ROLES.get(role) or C.ROLES["account_executive"]
    return {"key": role, **r, "day": [{"label": label, "href": href} for label, href in r["day"]],
            "areas": [{"key": k, "title": a["title"]} for k in r["areas"] for a in C.AREAS if a["key"] == k]}


def permissions(matrix: dict) -> list[dict]:
    """What the role can do, from the live permissions matrix."""
    out = []
    for res, perm in matrix.items():
        actions = [a for a in ("create", "read", "update", "delete", "export") if perm.allows(a)]
        if actions:
            out.append({"resource": res, "label": RESOURCE_LABELS.get(res, res.replace("_", " ").title()), "actions": actions,
                        "scope": "Only yours" if perm.scope == "own" else "Everyone's"})
    return sorted(out, key=lambda r: r["label"])


def _kind(col) -> str:
    t = col.type.__class__.__name__.lower()
    return next((k for k, v in (("number", ("numeric", "integer", "biginteger", "smallinteger", "float")), ("date", ("date",)),
                                ("date & time", ("utcdatetime", "datetime")), ("yes / no", ("boolean",)), ("list / details", ("jsonb", "json")))
                 if t in v), "text")


async def data_model(db: AsyncSession, role: str | None) -> dict:
    """Business entities from the live schema (fields and links), plus custom fields and custom objects."""
    from app.models import CustomFieldDefinition, CustomObject
    from app.services import custom_fields

    tables = Base.metadata.tables
    defs = (await db.execute(select(CustomFieldDefinition))).scalars().all()
    by_entity: dict[str, list] = {}
    for d in defs:
        if custom_fields.level(d, role) != "hidden":
            by_entity.setdefault(d.entity, []).append({"key": d.key, "label": d.label, "kind": d.field_type, "custom": True})
    entity_key = {"accounts": "account", "contacts": "contact", "deals": "deal", "leads": "lead"}
    out = []
    for table, (label, description, area) in C.ENTITIES.items():
        t = tables.get(table)
        if t is None:
            continue
        fields = [{"key": c.name, "label": c.name.replace("_", " ").capitalize(), "kind": _kind(c), "required": not c.nullable and not c.primary_key}
                  for c in t.columns if not _TECHNICAL.search(c.name) and c.info.get("postgres_only") is None]
        links = sorted({C.ENTITIES[fk.column.table.name][0] for fk in t.foreign_keys if fk.column.table.name in C.ENTITIES
                        and fk.column.table.name != table})
        out.append({"table": table, "label": label, "description": description, "area": area, "fields": fields,
                    "custom_fields": by_entity.get(entity_key.get(table, ""), []), "links": links})
    objects = [{"key": o.key, "label": o.label, "plural": o.plural_label, "description": o.description,
                "fields": by_entity.get(f"object:{o.key}", [])} for o in (await db.execute(select(CustomObject))).scalars().all()]
    return {"entities": out, "custom_objects": objects}


def catalog(role: str | None) -> dict:
    return {"role": role_out(role) if role else None, "roles": {k: v["title"] for k, v in C.ROLES.items()},
            "areas": [area_out(a) for a in C.AREAS], "processes": C.PROCESSES, "glossary": C.GLOSSARY,
            "shortcuts": [{"keys": k, "action": a} for k, a in C.SHORTCUTS], "whats_new": C.WHATS_NEW}


def is_help_question(question: str) -> bool:
    return bool(HELP_INTENT.search(question or ""))


def compose_answer(question: str, hits: list[dict]) -> str:
    """A grounded answer without a language model: the best how-to's steps, else the best overview."""
    if not hits:
        return ""
    best = hits[0]
    if best["kind"] == "howto":
        steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(best["steps"]))
        return f"{best['title']}\n{steps}"
    if best["kind"] == "glossary":
        return f"{best['title']}: {best['snippet']}"
    return f"{best['title']}: {best['snippet']}"


def help_context(hits: list[dict]) -> str:
    return "\n".join(f"[H{i + 1}] {h['title']}: {h['snippet']} (page {h.get('page', h['href'])})" for i, h in enumerate(hits))
