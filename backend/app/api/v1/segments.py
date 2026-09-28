"""Behavioural events and dynamic segments (services/cdp.py): the segment builder, website tracking settings,
server-side event ingestion, and the public endpoints the website snippet calls."""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenancy
from app.core.database import get_db
from app.core.rbac import Principal, authorize
from app.models import Campaign, Segment
from app.services import app_settings, campaigns as campaign_svc, cdp

router = APIRouter(tags=["segments"])
public = APIRouter(prefix="/public", tags=["public"])


class SegmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    object: str = "contact"
    rules: dict
    active: bool = True


class PreviewIn(BaseModel):
    object: str = "contact"
    rules: dict


async def _segment(db: AsyncSession, segment_id: uuid.UUID) -> Segment:
    seg = await db.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(404, "Segment not found")
    return seg


@router.get("/segments/catalog")
async def catalog(_: Principal = Depends(authorize("campaigns", "read"))):
    return cdp.catalog()


@router.get("/segments")
async def list_segments(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("campaigns", "read"))):
    return [cdp.segment_out(s) for s in (await db.execute(select(Segment).order_by(Segment.name))).scalars()]


@router.post("/segments/preview")
async def preview(body: PreviewIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "read"))):
    try:
        return await cdp.preview(db, body.object, body.rules, principal=p)
    except (cdp.SegmentError, ValueError) as e:
        raise HTTPException(422, str(e))


@router.post("/segments", status_code=201)
async def create_segment(body: SegmentIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "create"))):
    try:
        rules = cdp.validate_rules(body.object, body.rules)
    except (cdp.SegmentError, ValueError) as e:
        raise HTTPException(422, str(e))
    seg = Segment(name=body.name.strip(), description=body.description, object=body.object, rules=rules, active=body.active, created_by=p.id)
    db.add(seg)
    await db.flush()
    await cdp.refresh(db, seg)
    await db.commit()
    return cdp.segment_out(seg)


@router.get("/segments/{segment_id}")
async def get_segment(segment_id: uuid.UUID, db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("campaigns", "read"))):
    seg = await _segment(db, segment_id)
    try:
        sample = (await cdp.preview(db, seg.object, seg.rules, sample=25, principal=p))["sample"]
    except cdp.SegmentError:
        sample = []
    return {**cdp.segment_out(seg), "sample": sample}


@router.patch("/segments/{segment_id}")
async def update_segment(segment_id: uuid.UUID, body: SegmentIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("campaigns", "update"))):
    seg = await _segment(db, segment_id)
    try:
        rules = cdp.validate_rules(body.object, body.rules)
    except (cdp.SegmentError, ValueError) as e:
        raise HTTPException(422, str(e))
    if body.object != seg.object:
        raise HTTPException(422, "A segment's object can't change; create a new segment")
    seg.name, seg.description, seg.rules, seg.active = body.name.strip(), body.description, rules, body.active
    await cdp.refresh(db, seg, emit_events=False)
    await db.commit()
    return cdp.segment_out(seg)


@router.delete("/segments/{segment_id}", status_code=204)
async def delete_segment(segment_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("campaigns", "delete"))):
    await db.delete(await _segment(db, segment_id))
    await db.commit()


@router.post("/segments/{segment_id}/refresh")
async def refresh_segment(segment_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("campaigns", "update"))):
    seg = await _segment(db, segment_id)
    try:
        out = await cdp.refresh(db, seg)
    except cdp.SegmentError as e:
        raise HTTPException(422, str(e))
    await db.commit()
    return {**cdp.segment_out(seg), **out}


class ToCampaign(BaseModel):
    campaign_id: uuid.UUID


@router.post("/segments/{segment_id}/add-to-campaign")
async def add_to_campaign(segment_id: uuid.UUID, body: ToCampaign, db: AsyncSession = Depends(get_db),
                          _: Principal = Depends(authorize("campaigns", "update"))):
    seg = await _segment(db, segment_id)
    campaign = await db.get(Campaign, body.campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    ids = await cdp.member_ids(db, seg)
    out = await campaign_svc.add_members(db, campaign, lead_ids=ids if seg.object == "lead" else (),
                                         contact_ids=ids if seg.object == "contact" else (), source="filter")
    await db.commit()
    return out


# ---- website tracking settings and server-side events -------------------------------------------------------------
class TrackingIn(BaseModel):
    enabled: bool
    domains: list[str] = Field(default_factory=list, max_length=20)


def _snippet(site_key: str) -> str:
    src = f"{tenancy.api_url()}/api/v1/public/t.js?k={site_key}"
    return f'<script async src="{src}"></script>'


@router.get("/segments-tracking")
async def get_tracking(db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("campaigns", "read"))):
    cfg = await cdp.tracking_config(db)
    await db.commit()
    return {**cfg, "snippet": _snippet(cfg["site_key"])}


@router.put("/segments-tracking")
async def put_tracking(body: TrackingIn, db: AsyncSession = Depends(get_db), _: Principal = Depends(authorize("campaigns", "update"))):
    cfg = await cdp.tracking_config(db)
    domains = sorted({d.strip().lower().removeprefix("https://").removeprefix("http://").strip("/") for d in body.domains if d.strip()})
    cfg = await app_settings.put(db, "web_tracking", {**cfg, "enabled": body.enabled, "domains": domains})
    await db.commit()
    return {**cfg, "snippet": _snippet(cfg["site_key"])}


class ApiEvent(BaseModel):
    event: str = Field(max_length=60)
    email: str | None = Field(None, max_length=255)
    contact_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    url: str | None = Field(None, max_length=1000)
    properties: dict = Field(default_factory=dict)
    occurred_at: str | None = None


@router.post("/events", status_code=202)
async def ingest_events(events: list[ApiEvent], db: AsyncSession = Depends(get_db), p: Principal = Depends(authorize("contacts", "update"))):
    """Product-usage and other server-side events for known people (by email, contact_id or lead_id)."""
    if p.is_own_scope("contacts") or not p.can("leads", "update") or p.is_own_scope("leads"):
        raise HTTPException(403, "Sending events needs update access to every contact and lead")
    try:
        return await cdp.ingest_api(db, [e.model_dump() for e in events])
    except cdp.SegmentError as e:
        raise HTTPException(413, str(e))


# ---- public: the website snippet -----------------------------------------------------------------------------------
SNIPPET = r"""(function(){try{
var K=%(key)s,E=%(endpoint)s;if(window.cirra&&window.cirra._k)return;
if(navigator.doNotTrack==="1"||window.cirraConsent===false)return;
function id(){try{var v=localStorage.getItem("cirra_aid");if(!v){v=(crypto.randomUUID?crypto.randomUUID():String(Math.random()).slice(2)+Date.now()).replace(/[^A-Za-z0-9_-]/g,"");localStorage.setItem("cirra_aid",v)}return v}catch(e){return "anon"+Date.now()}}
var A=id(),Q=[],T=null;
function flush(ident){var b=JSON.stringify({site_key:K,anonymous_id:A,events:Q.splice(0,50),identify:ident||null});
if(navigator.sendBeacon){navigator.sendBeacon(E,new Blob([b],{type:"text/plain"}))}else{fetch(E,{method:"POST",body:b,keepalive:true,headers:{"Content-Type":"text/plain"}})}}
function track(ev,props){Q.push({event:ev,url:location.href,properties:props||{},ts:new Date().toISOString()});clearTimeout(T);T=setTimeout(flush,800)}
function page(){track("page_view",{title:document.title,referrer:document.referrer})}
window.cirra={_k:K,track:track,identify:function(email){if(email)flush({email:email})},anonymousId:function(){return A}};
document.addEventListener("submit",function(e){var f=e.target;if(f&&f.querySelector&&!f.querySelector("input[name=cirra_aid]")){var i=document.createElement("input");i.type="hidden";i.name="cirra_aid";i.value=A;f.appendChild(i)}},true);
var P=history.pushState;history.pushState=function(){P.apply(this,arguments);setTimeout(page,0)};window.addEventListener("popstate",page);
addEventListener("pagehide",function(){if(Q.length)flush()});page();
}catch(e){}})();"""


@public.get("/t.js")
async def tracking_script(k: str, db: AsyncSession = Depends(get_db)):
    cfg = await app_settings.get(db, "web_tracking")
    if not cfg.get("enabled") or k != cfg.get("site_key"):
        return Response("/* Cirra tracking is off */", media_type="application/javascript")
    js = SNIPPET % {"key": json.dumps(k), "endpoint": json.dumps(f"{tenancy.api_url()}/api/v1/public/events")}
    return Response(js, media_type="application/javascript", headers={"Cache-Control": "public, max-age=300"})


@public.post("/events", status_code=202)
async def public_events(request: Request, db: AsyncSession = Depends(get_db)):
    """Events from the website snippet (sent as text/plain so browsers need no CORS preflight)."""
    raw = await request.body()
    if len(raw) > 64_000:
        raise HTTPException(413, "Too much data")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Send JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "Send a JSON object")
    cfg = await app_settings.get(db, "web_tracking")
    origin = (request.headers.get("origin") or request.headers.get("referer") or "").lower()
    host = origin.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    if cfg.get("domains") and host and not any(host == d or host.endswith("." + d) for d in cfg["domains"]):
        raise HTTPException(403, "This site isn't allowed to send events")
    try:
        return await cdp.ingest_web(db, body)
    except cdp.SegmentError as e:
        raise HTTPException(422, str(e))
