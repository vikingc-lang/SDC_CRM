"""Volume demo data: a larger, realistic dataset across every object and transaction type.

    python -m app.demo_volume                     # batch "v1" at scale 1
    python -m app.demo_volume --scale 3 --batch v2 # three times the records, as a separate batch

Runs on top of the regular demo seed (python -m app.seed). Each batch is recorded in app_settings and runs
once; records are tagged with the batch in their domains and emails so batches never collide. Everything
goes through the same services the API uses (lead capture and conversion, stage gates, CPQ pricing and the
approval chain, document generation, redlining and e-signature, contracts, orders and the ERP hand-off,
case SLAs and CSAT, campaign membership, partner registrations), so derived data is consistent: stage
history, forecast deltas, engagement scores, notifications, the integration outbox and the audit trail.
Timestamps are then spread over the past year so reports, forecasts and trends have history.

Per scale unit, roughly: 40 accounts, 190 contacts, 120 leads, 110 opportunities, 500 activities, 150 tasks,
45 quotes, 60 documents, 25 contracts, 70 cases, 6 campaigns, partner registrations, invoices, usage and
forecast submissions.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import (
    Account, Activity, ApiKey, AppSetting, Campaign, CampaignMember, CommissionPlan, Contact, Contract, Dashboard, Deal, DealPartner,
    DealStageHistory, ForecastAdjustment, ForecastSubmission, IntegrationEvent, Invoice, KbArticle, Lead, OnboardingMilestone, Partner,
    Pipeline, PipelineStage, Product, ProductUsage, Quota, Quote, SavedReport, SupportTicket, Task, User, WebhookDelivery,
    WebhookSubscription, WorkflowRule,
)

UTC = timezone.utc

# ---- vocabularies ------------------------------------------------------------------------------------------------
PREFIX = ["Aster", "Birch", "Cedar", "Delta", "Ember", "Falcon", "Granite", "Harbor", "Iris", "Juniper", "Kestrel", "Lumen", "Meridian",
          "Northgate", "Onyx", "Pioneer", "Quartz", "Riverside", "Summit", "Tidewater", "Unity", "Vertex", "Willow", "Zenith", "Atlas",
          "Beacon", "Crescent", "Driftwood", "Evergreen", "Foundry", "Glacier", "Horizon", "Ironwood", "Keystone", "Lakeshore", "Monarch"]
SUFFIX = {
    "Manufacturing": ["Manufacturing", "Industries", "Components", "Precision Works"],
    "Logistics": ["Logistics", "Freight", "Shipping", "Distribution"],
    "Retail": ["Retail Group", "Stores", "Outfitters", "Market"],
    "Healthcare": ["Health", "Medical", "Care Partners", "Clinics"],
    "Financial Services": ["Capital", "Financial", "Bank", "Insurance"],
    "Energy & Utilities": ["Energy", "Power", "Utilities", "Renewables"],
    "Food & Beverage": ["Foods", "Beverages", "Farms", "Brands"],
    "Software": ["Software", "Labs", "Systems", "Cloud"],
    "Industrial Distribution": ["Supply", "Industrial", "Parts", "Equipment"],
    "Consumer Goods": ["Consumer Brands", "Home Goods", "Products", "Goods Co"],
}
INDUSTRIES = list(SUFFIX)
COUNTRIES = [  # (country, currency, timezone, city)
    ("United States", "USD", "America/New_York", "Chicago"), ("United States", "USD", "America/Los_Angeles", "San Diego"),
    ("Canada", "CAD", "America/Toronto", "Toronto"), ("Mexico", "USD", "America/Mexico_City", "Monterrey"),
    ("Germany", "EUR", "Europe/Berlin", "Munich"), ("United Kingdom", "GBP", "Europe/London", "Manchester"),
    ("France", "EUR", "Europe/Paris", "Lyon"), ("Netherlands", "EUR", "Europe/Amsterdam", "Rotterdam"),
    ("Sweden", "EUR", "Europe/Stockholm", "Gothenburg"), ("Spain", "EUR", "Europe/Madrid", "Valencia"),
    ("India", "INR", "Asia/Kolkata", "Pune"), ("Japan", "USD", "Asia/Tokyo", "Osaka"), ("Australia", "AUD", "Australia/Sydney", "Melbourne"),
    ("Singapore", "USD", "Asia/Singapore", "Singapore"), ("Brazil", "USD", "America/Sao_Paulo", "Curitiba"),
]
FIRST = ["Ana", "Ben", "Chen", "Dara", "Elif", "Farah", "Gabe", "Hana", "Ivan", "Jade", "Kofi", "Lara", "Mateo", "Nadia", "Omar", "Priyanka",
         "Quinn", "Rosa", "Sven", "Tariq", "Uma", "Victor", "Wen", "Ximena", "Yusuf", "Zoe", "Akira", "Bianca", "Carlos", "Dmitri", "Erin",
         "Felix", "Grace", "Hugo", "Ines", "Jonas", "Keiko", "Leo", "Maya", "Nils"]
LAST = ["Andersen", "Bauer", "Castillo", "Dubois", "Eriksen", "Fischer", "Garcia", "Haddad", "Ito", "Jansen", "Kaur", "Lindqvist", "Moreau",
        "Nakamura", "Okafor", "Petrov", "Quintero", "Rossi", "Schmidt", "Tanaka", "Umar", "Varga", "Wagner", "Xu", "Yamada", "Zhang",
        "Oliveira", "Brennan", "Costa", "Novak"]
TITLES = {  # buying role -> titles
    "Economic Buyer": ["CFO", "COO", "Chief Revenue Officer"], "Decision Maker": ["VP Sales", "VP Operations", "Chief Digital Officer"],
    "Champion": ["Director of RevOps", "Head of Sales Operations", "Sales Excellence Lead"], "Evaluator": ["Solutions Architect", "CRM Manager", "IT Manager"],
    "Influencer": ["Regional Sales Manager", "Finance Business Partner"], "Blocker": ["IT Security Lead", "Head of Procurement"],
    "Legal Counsel": ["Associate General Counsel"], "Procurement": ["Category Manager, IT"],
}
DEPARTMENTS = {"CFO": "Finance", "COO": "Operations", "IT": "IT", "Sales": "Sales", "Counsel": "Legal", "Procurement": "Procurement"}
COMPETITORS = ["Salesforce", "HubSpot", "Microsoft Dynamics", "Zoho", "Pipedrive", "SAP Sales Cloud"]
PAINS = ["Forecasts live in spreadsheets", "Quote-to-cash takes three weeks", "No visibility into renewals", "Reps log activity twice",
         "Discount approvals happen in email", "ERP and CRM customer records disagree", "Partner deals are tracked by hand"]
LOSS = ["competitor", "budget_frozen", "feature_gap", "champion_departed", "price", "no_decision", "timing", "other"]
CASE_SUBJECTS = [
    ("SSO login loop after IdP change", "Access"), ("Nightly ERP sync failed", "Integrations"), ("Dashboard export shows wrong totals", "Reporting"),
    ("API returns 429 during bulk import", "Developers"), ("Quote PDF missing logo", "CPQ"), ("E-signature email not received", "CLM"),
    ("User can't see a colleague's deal", "Access"), ("Forecast numbers don't match the report", "Reporting"), ("Invoice amounts differ from order", "Billing"),
    ("Mobile app crashes on activity log", "Mobile"), ("Workflow sent duplicate tasks", "Automation"), ("Need more licences before renewal", "Billing"),
]
KB = [
    ("Reset a user's two-factor device", "Access", "Admins open Admin > Users and choose Reset 2FA. The user is signed out everywhere and enrols a new authenticator at next sign-in."),
    ("Why a deal can't move to the next stage", "Pipeline", "Stage gates list the evidence each stage needs, such as a mapped economic buyer or a signed NDA. Managers can override with a reason."),
    ("Set up a customer price book", "CPQ", "Create a price book of kind Customer, link the account and add entries per product and currency. It wins over regional and list prices."),
    ("How commission is calculated", "Sales performance", "Your plan pays its base rate up to quota and each tier's rate on bookings above the tier threshold. Each deal earns what it adds."),
    ("Build a campaign list from a filter", "Marketing", "On the campaign's Members tab choose Add from a filter, pick leads or contacts and add conditions. People already in the campaign are skipped."),
    ("Connect a webhook endpoint", "Developers", "Admin > API & webhooks > New webhook. Verify X-Cirra-Signature: HMAC-SHA256 over timestamp.body with the endpoint secret."),
    ("Export any report to Excel", "Reporting", "Open the saved report and choose Export CSV, then open it in Excel with the English (United States) locale."),
    ("Recover a declined e-signature", "CLM", "Void the document, fix the terms in a new version and send it again. The audit trail keeps every version and signature."),
    ("Request more licences", "Billing", "Raise a case with category Billing and the number of users. Your account team prepares an upsell quote the same day."),
    ("Draft article: SCIM provisioning", "Access", "Planned: automatic user provisioning over SCIM 2.0 from Entra ID and Okta."),
]


class Gen:
    def __init__(self, db, batch: str, scale: float, seed: int):
        self.db, self.batch, self.scale = db, batch, scale
        self.r = random.Random(seed)
        self.now = datetime.now(UTC)
        self.today = date.today()
        self.stats: dict[str, int] = {}

    def n(self, base: int) -> int:
        return max(1, round(base * self.scale))

    def bump(self, key: str, k: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + k

    def ago(self, lo_days: float, hi_days: float) -> datetime:
        return self.now - timedelta(days=self.r.uniform(lo_days, hi_days), minutes=self.r.randint(0, 600))

    def pick(self, seq, weights=None):
        return self.r.choices(seq, weights=weights, k=1)[0] if weights else self.r.choice(seq)

    # ---- context ----------------------------------------------------------------------------------------------
    async def load(self) -> None:
        db = self.db
        self.users = {u.email: u for u in (await db.execute(select(User))).scalars()}
        need = ["admin@cirra.demo", "marcus@cirra.demo", "priya@cirra.demo", "diego@cirra.demo", "sam@cirra.demo"]
        missing = [e for e in need if e not in self.users]
        if missing:
            raise SystemExit(f"Run the demo seed first (python -m app.seed); missing users: {missing}")
        u = self.users
        self.admin, self.marcus, self.priya, self.diego, self.sam = (u[e] for e in need)
        self.sofia, self.nina = u.get("sofia@cirra.demo"), u.get("nina@cirra.demo")
        self.sellers = [self.priya, self.diego, self.marcus]
        self.pipelines = {p.kind if p.kind != "direct" or p.is_default else "solution": p
                          for p in (await db.execute(select(Pipeline))).scalars().unique()}
        self.stages = {}
        for p in self.pipelines.values():
            await db.refresh(p, ["stages"])
            self.stages[p.id] = sorted(p.stages, key=lambda s: s.stage_order)
        self.products = {p.sku: p for p in (await db.execute(select(Product))).scalars()}
        self.partners = list((await db.execute(select(Partner))).scalars())
        self.partner_user = u.get("partner@northstar-partners.com")

    # ---- 1. accounts and contacts -------------------------------------------------------------------------------
    async def accounts(self) -> None:
        from app.services import enrichment, performance

        db = self.db
        self.accts: list[Account] = []
        used = set()
        for i in range(self.n(40)):
            ind = INDUSTRIES[i % len(INDUSTRIES)]
            while True:
                name = f"{self.pick(PREFIX)} {self.pick(SUFFIX[ind])}"
                if name not in used:
                    used.add(name)
                    break
            country, currency, tz, city = self.pick(COUNTRIES)
            emp = self.pick([60, 180, 450, 900, 1800, 4200, 9500, 24000], [1, 2, 3, 3, 3, 2, 2, 1])
            tier = "Enterprise" if emp >= 2000 else "Mid-Market" if emp >= 250 else "SMB"
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            created = self.ago(60, 420)
            a = Account(name=name, domain=f"{slug}-{self.batch}.example.com", industry=ind, tier=tier, owner_id=self.pick(self.sellers).id,
                        country=country, region=enrichment.region_for(country), employee_count=emp,
                        annual_revenue=Decimal(emp * self.r.randint(90, 260) * 1000), legal_name=f"{name} {self.pick(['Inc.', 'Ltd', 'GmbH', 'B.V.', 'S.A.'])}",
                        tax_id=f"TX-{self.r.randint(10**7, 10**8 - 1)}", payment_terms=self.pick(["NET30", "NET30", "NET45", "NET60"]),
                        credit_limit=Decimal(self.pick([50_000, 150_000, 400_000, 1_000_000])), lifecycle_stage="prospect",
                        locations=[{"type": "HQ", "city": city, "country": country}],
                        billing_address={"line1": f"{self.r.randint(10, 999)} {self.pick(['Harbor', 'Market', 'Station', 'Park'])} Street", "city": city,
                                         "country": country},
                        custom_metadata={"erp_region": {"NA": "NA", "EMEA": "EMEA"}.get(enrichment.region_for(country) or "", "APAC"),
                                         "strategic_account": emp >= 9500, "source": f"volume:{self.batch}"},
                        created_at=created)
            a._currency, a._tz = currency, tz  # used below; not persisted
            db.add(a)
            self.accts.append(a)
        await db.flush()
        # hierarchies: a few groups with subsidiaries
        for parent in [a for a in self.accts if a.tier == "Enterprise"][: self.n(4)]:
            for k in range(self.r.randint(1, 2)):
                sub = Account(name=f"{parent.name} {self.pick(['Europe', 'Americas', 'Asia Pacific', 'Services'])}",
                              domain=f"{parent.domain.split('.')[0]}-sub{k}.example.com", industry=parent.industry, tier="Mid-Market",
                              owner_id=parent.owner_id, country=parent.country, region=parent.region, parent_id=parent.id,
                              employee_count=parent.employee_count // 5, custom_metadata={"source": f"volume:{self.batch}"}, created_at=self.ago(40, 300))
                sub._currency, sub._tz = parent._currency, parent._tz
                db.add(sub)
                self.accts.append(sub)
        # near-duplicates for the de-duplication queue
        for orig in self.accts[:2]:
            dupe = Account(name=f"{orig.name} Inc", domain=f"{orig.domain.split('.')[0]}-old.example.com",
                           industry=orig.industry, tier=orig.tier, owner_id=self.sam.id, country=orig.country, region=orig.region,
                           custom_metadata={"source": "imported trade-show list"}, created_at=self.ago(3, 20))
            dupe._currency, dupe._tz = orig._currency, orig._tz
            db.add(dupe)
            self.accts.append(dupe)
        await db.flush()
        for a in self.accts:
            await performance.assign(db, a)
        self.bump("accounts", len(self.accts))

        roles = list(TITLES)
        self.contacts: dict = {}
        for a in self.accts:
            people = []
            for k in range(self.r.randint(3, 7) if "-old" not in a.domain else 1):
                role = roles[k % len(roles)] if k < len(roles) else self.pick(roles)
                first, last = self.pick(FIRST), self.pick(LAST)
                title = self.pick(TITLES[role])
                regime = "GDPR" if a.region == "EMEA" else "CCPA" if a._tz == "America/Los_Angeles" else None
                consent = self.pick(["granted", "granted", "unknown", "denied"]) if regime else self.pick(["granted", "unknown"])
                c = Contact(account_id=a.id, first_name=first, last_name=last, email=f"{first}.{last}.{k}@{a.domain}".lower(),
                            phone=f"+1 555 {self.r.randint(100, 999)} {self.r.randint(1000, 9999)}", job_title=title, buying_role=role,
                            timezone=a._tz, department=next((v for key, v in DEPARTMENTS.items() if key in title), "Operations"),
                            linkedin_url=f"https://www.linkedin.com/in/{first}-{last}-{self.r.randint(100, 999)}".lower(),
                            privacy_regime=regime, consent_email=consent, consent_basis="consent" if consent == "granted" else None,
                            opt_out_email=consent == "denied", created_at=a.created_at + timedelta(days=self.r.randint(0, 30)))
                if self.r.random() < 0.05:
                    c.status, c.departed_at = "departed", self.ago(10, 90)
                db.add(c)
                people.append(c)
            self.contacts[a.id] = people
        await db.flush()
        self.bump("contacts", sum(len(v) for v in self.contacts.values()))

    # ---- 2. campaigns (before leads, so captured leads attach) -----------------------------------------------------
    async def campaigns(self) -> None:
        from app.services import campaigns as camp

        db = self.db
        owner = self.nina or self.marcus
        specs = [
            ("Spring Forecasting Webinar", "webinar", "completed", 150, 140, 9000, 8400, 180000),
            ("Manufacturing Summit Booth", "trade_show", "completed", 120, 118, 38000, 41500, 450000),
            ("ERP Integration Nurture", "email", "active", 60, None, 4000, 1800, 120000),
            ("Paid Search: CRM for Distributors", "paid_ads", "active", 45, None, 15000, 9200, 90000),
            ("Partner Co-marketing: Northstar", "partner", "active", 30, None, 6000, 2500, 150000),
            ("Executive Dinner Series", "event", "planned", -14, -16, 12000, 0, 200000),
        ]
        self.camps: list[Campaign] = []
        for name, typ, status, start_ago, end_ago, budget, cost, expected in specs[: max(3, self.n(6))]:
            c = Campaign(name=f"{name} ({self.batch})", code=camp.slug(f"{name} {self.batch}"), campaign_type=typ, status=status, owner_id=owner.id,
                         description=f"{name}: demo campaign.", start_date=self.today - timedelta(days=start_ago),
                         end_date=self.today - timedelta(days=end_ago) if end_ago is not None else None, budget=budget, actual_cost=cost,
                         expected_revenue=expected, email_subject="{{first_name}}, a faster quote-to-cash for {{company}}" if typ == "email" else None,
                         email_body="Hi {{first_name}},\n\nSee how distribution teams close faster with ERP-native quoting.\n\nThe Cirra team" if typ == "email" else None,
                         created_at=self.now - timedelta(days=max(start_ago, 1) + 10))
            db.add(c)
            self.camps.append(c)
        await db.flush()
        self.bump("campaigns", len(self.camps))

    async def campaign_members(self) -> None:
        from app.services import campaigns as camp

        db = self.db
        all_contacts = [c for v in self.contacts.values() for c in v if c.status == "active"]
        statuses = ["targeted", "sent", "sent", "responded", "registered", "attended", "unsubscribed", "bounced"]
        for c in self.camps:
            if c.status == "planned":
                picks = self.r.sample(all_contacts, min(len(all_contacts), self.n(15)))
                await camp.add_members(db, c, contact_ids=[p.id for p in picks], source="filter")
                continue
            picks = self.r.sample(all_contacts, min(len(all_contacts), self.n(25)))
            await camp.add_members(db, c, contact_ids=[p.id for p in picks], source=self.pick(["filter", "manual"]))
            members = (await db.execute(select(CampaignMember).where(CampaignMember.campaign_id == c.id))).scalars().all()
            for m in members:
                s = self.pick(statuses)
                if s in ("responded", "registered", "attended"):
                    m.status, m.sent_at = "sent", c.created_at + timedelta(days=1)
                    await camp.set_status(db, c, m, s)
                    m.responded_at = c.created_at + timedelta(days=self.r.randint(2, 20))
                elif s != "targeted":
                    m.status, m.sent_at = s, c.created_at + timedelta(days=1)
                m.added_at = c.created_at
            if c.campaign_type == "email":
                c.last_sent_at = c.created_at + timedelta(days=1)
        await db.flush()
        ids = [c.id for c in self.camps]
        self.bump("campaign_members", len((await db.execute(select(CampaignMember.id).where(CampaignMember.campaign_id.in_(ids)))).all()))

    # ---- 3. leads (every source and status) --------------------------------------------------------------------------
    async def leads(self) -> None:
        from app.services import leads as lead_svc

        db = self.db
        cfg = await lead_svc.app_settings.get(db, "lead_scoring")
        sources = list(lead_svc.SOURCES)
        plan = (["new"] * 20 + ["working"] * 15 + ["mql"] * 15 + ["sql"] * 10 + ["recycled"] * 5 + ["disqualified"] * 15 + ["converted"] * 20)
        self.leads_made: list[Lead] = []
        for i in range(self.n(120)):
            source = sources[i % len(sources)]
            country, currency, tz, city = self.pick(COUNTRIES)
            ind = self.pick(INDUSTRIES)
            company = f"{self.pick(PREFIX)} {self.pick(SUFFIX[ind])} {self.pick(['North', 'South', 'West', 'East', 'Group', 'Co'])}"
            slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") + f"-{i}-{self.batch}"
            first, last = self.pick(FIRST), self.pick(LAST)
            free_mail = self.r.random() < 0.06
            email = f"{first}.{last}{i}@gmail.com".lower() if free_mail else f"{first}.{last}@{slug}.example.com".lower()
            camp_key = None
            if source in ("campaign", "trade_show", "web_form", "partner") and self.camps and self.r.random() < 0.7:
                camp_key = self.pick(self.camps).code
            emp = self.pick([40, 150, 400, 1200, 3500, 12000])
            lead, _ = await lead_svc.capture(db, {
                "first_name": first, "last_name": last, "email": email, "job_title": self.pick([t for v in TITLES.values() for t in v]),
                "company_name": "" if free_mail else company, "domain": None if free_mail else f"{slug}.example.com", "industry": ind,
                "employee_count": emp, "annual_revenue": emp * self.r.randint(80, 240) * 1000, "country": country,
                "phone": f"+1 555 {self.r.randint(100, 999)} {self.r.randint(1000, 9999)}",
                "consent": self.pick(["granted", "granted", None, "denied"]),
            }, source=source, campaign=camp_key or self.pick([None, "Website", "Q4 ERP webinar"]))
            created = self.ago(2, 240)
            lead.created_at = created
            for _ in range(self.r.randint(0, 6)):
                t = self.pick(list(lead_svc.EVENT_TYPES))
                when = created + (self.now - created) * self.r.random()
                await lead_svc.add_event(db, lead, t, f"{t.replace('_', ' ')} ({source})", source=source, occurred_at=when, cfg=cfg)
            await lead_svc.rescore(db, lead, cfg)
            target = self.pick(plan)
            if free_mail and target == "converted":
                target = "disqualified"
            if target == "working":
                lead.status = "working"
            elif target == "mql":
                lead.status, lead.mql_at = "mql", created + timedelta(days=3)
            elif target in ("sql", "converted"):
                fw = self.pick(["bant", "meddpicc"])
                keys = ("budget", "authority", "need", "timeline") if fw == "bant" else (
                    "metrics", "economic_buyer", "decision_criteria", "decision_process", "identify_pain", "champion")
                lead.qualification_framework = fw
                lead.qualification = {k: {"met": True, "note": self.pick(PAINS)} for k in keys}
                lead.status = "sql"
            elif target == "disqualified":
                lead.status, lead.disqualified_reason = "disqualified", self.pick(list(lead_svc.DISQUALIFY_REASONS))
                lead.disqualify_note = "Qualified out after discovery."
            elif target == "recycled":
                lead.status = "recycled"
            if not lead.owner_id and target != "new":
                lead.owner_id = self.pick([self.sam, self.priya, self.diego]).id
            if target == "converted":
                pipeline = self.pipelines["inbound"]
                owner = self.pick([self.priya, self.diego])
                try:
                    out = await lead_svc.convert(db, lead, self.marcus, create_deal=True, amount=float(self.r.randint(12, 90) * 1000),
                                                 deal_title=f"{lead.company_name}: first rollout", pipeline_id=pipeline.id, owner_id=owner.id,
                                                 buying_role=self.pick(["Champion", "Economic Buyer", "Decision Maker"]),
                                                 target_close_date=self.today + timedelta(days=self.r.randint(10, 120)), override=True)
                    lead.converted_at = created + timedelta(days=self.r.randint(5, 30))
                    self.bump("lead_conversions")
                    if out.get("deal_id"):
                        d = await db.get(Deal, out["deal_id"])
                        d.created_at = lead.converted_at
                        self.converted_deals.append(d)
                except lead_svc.LeadError:
                    lead.status = "sql"
            self.leads_made.append(lead)
        await db.flush()
        self.bump("leads", len(self.leads_made))

    # ---- 4. opportunities, quotes, documents, contracts and orders -----------------------------------------------------
    async def walk(self, deal: Deal, target: PipelineStage, *, loss: str | None = None, win_note: str | None = None) -> None:
        from app.services import pipeline_service as ps

        stages = self.stages[deal.pipeline_id]
        open_stages = [s for s in stages if not (s.is_closed_won or s.is_closed_lost)]
        for s in open_stages:
            if s.stage_order <= deal.stage.stage_order:
                continue
            if target.is_closed_won or target.is_closed_lost or s.stage_order <= target.stage_order:
                if target.is_closed_lost and s.stage_order > open_stages[len(open_stages) // 2].stage_order and self.r.random() < 0.5:
                    break  # many losses happen mid-funnel
                await ps.change_stage(self.db, deal, s, self.marcus, override_gates=True)
        if target.is_closed_lost:
            reason = loss or self.pick(LOSS)
            await ps.change_stage(self.db, deal, target, self.marcus, loss_reason=reason, override_gates=True,
                                  loss_debrief=f"Lost: {reason.replace('_', ' ')}. {self.pick(PAINS)} was not enough to change priorities.",
                                  loss_competitor=self.pick(COMPETITORS) if reason == "competitor" else None)
        elif target.is_closed_won:
            await ps.change_stage(self.db, deal, target, self.marcus, override_gates=True,
                                  win_debrief=win_note or f"Won on {self.pick(['ERP-native quoting', 'the AI copilot', 'private-cloud deployment', 'time to value'])}.")

    async def spread_history(self, deal: Deal, start: datetime, end: datetime) -> None:
        """Move the stage history and its system activities onto the deal's real timeline."""
        rows = (await self.db.execute(select(DealStageHistory).where(DealStageHistory.deal_id == deal.id)
                                      .order_by(DealStageHistory.changed_at))).scalars().all()
        acts = (await self.db.execute(select(Activity).where(Activity.deal_id == deal.id, Activity.activity_type == "system")
                                      .order_by(Activity.occurred_at))).scalars().unique().all()
        span = max((end - start).total_seconds(), 3600)
        for i, h in enumerate(rows):
            h.changed_at = start + timedelta(seconds=span * i / max(len(rows) - 1, 1)) if len(rows) > 1 else start
        for i, a in enumerate(acts):
            a.occurred_at = rows[min(i + 1, len(rows) - 1)].changed_at if rows else end
        if rows:
            deal.stage_entered_at = rows[-1].changed_at

    async def quote_for(self, deal: Deal, account: Account, *, decide: str = "approve") -> Quote:
        from app.services import cpq

        db = self.db
        cur = deal.currency if deal.currency in ("USD", "EUR", "GBP") else "USD"
        q = Quote(deal_id=deal.id, quote_number=await cpq.next_quote_number(db), name=f"{account.name}: {self.pick(['pilot', 'rollout', 'enterprise'])}",
                  currency=cur, term_months=self.pick([12, 12, 24, 36]), payment_terms=self.pick(["NET30", "NET30", "NET45", "NET60", "NET90"]),
                  billing_frequency=self.pick(["annual", "quarterly", "monthly"]), created_by=deal.owner_id, is_primary=True,
                  custom_terms="Liability cap at 2x annual fees" if self.r.random() < 0.15 else None)
        db.add(q)
        await db.flush()
        users = self.r.randint(20, 400)
        lines = [{"product_id": self.products["CIR-PLAT"].id, "quantity": users, "discount_pct": self.pick([0, 5, 8, 12, 18, 25, 32])}]
        if self.r.random() < 0.6 and "CIR-AI" in self.products:
            lines.append({"product_id": self.products["CIR-AI"].id, "quantity": users, "discount_pct": self.pick([0, 10])})
        if self.r.random() < 0.5 and "CIR-IMPL" in self.products:
            lines.append({"product_id": self.products["CIR-IMPL"].id, "quantity": 1, "discount_pct": 0})
        if self.r.random() < 0.3 and "CIR-SUP" in self.products:
            lines.append({"product_id": self.products["CIR-SUP"].id, "quantity": 1, "discount_pct": 0})
        try:
            await cpq.rebuild(db, q, lines)
        except cpq.PricingError:  # not every product has a rate card in every currency; quote in USD instead
            q.currency = "USD"
            await cpq.rebuild(db, q, lines)
        if decide == "draft":
            return q
        await cpq.submit(db, q)
        await db.refresh(q, ["approvals"])
        for req in sorted(q.approvals, key=lambda a: a.level):
            if req.status != "pending" or decide == "pending":
                continue
            ok = decide != "reject" or req.level < max(a.level for a in q.approvals)
            await cpq.decide(db, req, self.admin, ok, "Within guardrails." if ok else "Discount too deep for this term; resubmit at 20%.")
            if not ok:
                break
        await db.refresh(q)
        self.bump(f"quotes_{q.status}")
        return q

    async def paper(self, deal: Deal, account: Account, quote: Quote | None, kind: str) -> None:
        """Documents in every state: completed, partially signed, in negotiation (redlines), sent, draft, voided."""
        from app.services import clm, contracting

        db = self.db
        people = [c for c in self.contacts.get(account.id, []) if c.email and c.status == "active"] or [None]
        buyer = self.pick(people)
        owner = await db.get(User, deal.owner_id)
        signers = [{"name": f"{buyer.first_name} {buyer.last_name}" if buyer else "Customer Signer", "email": buyer.email if buyer else f"signer@{account.domain}",
                    "party": "customer"}, {"name": owner.full_name, "email": owner.email, "party": "company"}]
        doc_type = {"order_form": "order_form"}.get(kind, kind)
        doc = await clm.generate(db, doc_type, account, deal, quote if doc_type in ("order_form", "proposal") else None, deal.owner_id)
        state = self.pick(["completed", "completed", "partial", "negotiation", "sent", "draft", "voided"]) if kind != "order_form" else "completed"
        if state == "draft":
            self.bump("documents_draft")
            return
        if state == "negotiation":
            await contracting.add_comment(db, doc, "Please cap liability at 2x annual fees.", "customer", signers[0]["name"], signers[0]["email"],
                                          clause="Limitation of liability")
            await contracting.new_version(db, doc, doc.body + "\n\nAmendment: liability capped at two times annual fees.", "Customer redline",
                                          "customer", author_name=signers[0]["name"])
            await contracting.new_version(db, doc, doc.body + "\n\nAmendment: liability capped at 1.5x annual fees; 60-day termination for convenience.",
                                          "Counter-proposal", "internal", owner)
            self.bump("documents_in_negotiation")
            return
        try:
            doc = await clm.send_for_signature(db, doc, signers)
        except Exception:  # noqa: BLE001  credit review can gate new accounts; leave it as a draft
            self.bump("documents_gated")
            return
        if state == "voided":
            doc.status = "voided"
        elif state in ("completed", "partial"):
            for req in sorted(doc.signers, key=lambda s: s.sign_order):
                await clm.sign(db, req, req.signer_name, None, f"203.0.113.{self.r.randint(2, 250)}", "Mozilla/5.0 (demo)")
                if state == "partial":
                    break
        self.bump(f"documents_{doc.status}")

    async def deals(self) -> None:
        from app.services import clm, orders
        from app.services.success import provision_onboarding, renew_contract_from_deal

        db = self.db
        kinds = ["direct"] * 7 + ["solution"] * 3 + ["inbound"] * 5 + ["renewal"] * 2 + ["partner"] * 3
        self.won: list[Deal] = []
        candidates = [a for a in self.accts if "-old" not in a.domain]
        made = list(self.converted_deals)
        for i in range(self.n(110)):
            a = self.pick(candidates)
            kind = self.pick(kinds)
            pipe = self.pipelines.get(kind) or self.pipelines["direct"]
            stages = self.stages[pipe.id]
            first = stages[0]
            outcome = self.pick(["open", "won", "lost"], [55, 25, 20])
            created = self.ago(15, 360)
            amount = Decimal(self.pick([8, 15, 25, 40, 65, 90, 120, 180, 260, 400, 650]) * 1000)
            cur = a._currency if a._currency in ("USD", "EUR", "GBP", "CAD", "AUD", "INR") else "USD"
            if cur == "INR":
                amount *= 80
            people = self.contacts.get(a.id, [])
            d = Deal(title=f"{a.name}: {self.pick(['Platform rollout', 'Revenue cloud', 'CPQ modernisation', 'Service desk', 'Partner portal', 'AI copilot pilot', 'Expansion'])}",
                     account_id=a.id, pipeline_id=pipe.id, stage_id=first.id, owner_id=(a.owner_id if self.r.random() < 0.8 else self.pick(self.sellers).id),
                     amount=amount, currency=cur, primary_contact_id=self.pick(people).id if people else None,
                     deal_type={"renewal": self.pick(["renewal", "upsell"]), "partner": "partner"}.get(kind, "new_business"),
                     source={"inbound": "inbound", "partner": "partner"}.get(kind, "direct"), risk_factors={},
                     ai_insights={"competitors": self.r.sample(COMPETITORS, self.r.randint(0, 2)), "pain_points": self.r.sample(PAINS, 2)},
                     created_at=created)
            d.stage = first
            db.add(d)
            await db.flush()
            db.add(DealStageHistory(deal_id=d.id, from_stage_id=None, to_stage_id=first.id, changed_by=d.owner_id, changed_at=created))
            if kind == "partner" and self.partners:
                db.add(DealPartner(deal_id=d.id, partner_id=self.pick(self.partners).id, role=self.pick(["referral", "co_sell", "resell"]),
                                   split_pct=self.pick([20, 30, 50, 100])))
            made.append(d)
            open_stages = [s for s in stages if not (s.is_closed_won or s.is_closed_lost)]
            won_stage = next(s for s in stages if s.is_closed_won)
            lost_stage = next(s for s in stages if s.is_closed_lost)
            late = open_stages[-1]
            quote = None
            # CPQ and paper for deals that get far enough
            reaches_late = outcome == "won" or (outcome == "open" and self.r.random() < 0.35) or (outcome == "lost" and self.r.random() < 0.3)
            if reaches_late and self.r.random() < 0.7:
                decision = "approve" if outcome == "won" else self.pick(["approve", "pending", "reject", "draft"])
                quote = await self.quote_for(d, a, decide=decision)
            if reaches_late:
                await self.paper(d, a, quote, self.pick(["nda", "msa", "sow", "proposal"] if quote is not None else ["nda", "msa", "sow"]))
            if outcome == "won":
                end = min(created + timedelta(days=self.r.randint(20, 140)), self.now - timedelta(days=1))
                full_order = kind == "solution" and quote is not None and quote.status == "approved"
                if full_order:  # everything the ERP needs -> the order is raised at Closed-Won
                    await self.paper(d, a, quote, "order_form")
                    addr = a.billing_address or {"line1": "1 Main Street", "city": "Chicago", "country": "US"}
                    d.po_number, d.bill_to, d.ship_to = f"PO-{self.r.randint(100000, 999999)}", addr, {**addr, "attention": "Operations"}
                    d.requested_delivery_date, d.incoterms = self.today + timedelta(days=14), "DAP"
                elif quote is not None and quote.status == "approved":
                    quote.status = "accepted"
                await self.walk(d, won_stage)
                d.closed_at = end
                d.target_close_date = d.original_close_date = end.date()
                a.lifecycle_stage, a.health_score = "customer", 100
                if d.deal_type != "renewal":
                    project = await provision_onboarding(db, d)
                    if project:
                        await self.age_onboarding(project, end)
                await renew_contract_from_deal(db, d)
                if quote is not None and quote.status in ("approved", "accepted"):
                    if not (await db.execute(select(Contract.id).where(Contract.quote_id == quote.id))).first():
                        c = await clm.create_contract_from_quote(db, quote, None, end.date())
                        c.start_date = end.date()
                order = (await db.execute(select(orders.Order).where(orders.Order.deal_id == d.id))).scalars().first()
                if order:
                    fate = self.pick(["push", "push", "leave", "cancel"])
                    if fate == "push":
                        await orders.push(db, order)
                    elif fate == "cancel":
                        order.status = "cancelled"
                    self.bump(f"orders_{order.status}")
                self.won.append(d)
                await self.spread_history(d, created, end)
            elif outcome == "lost":
                end = min(created + timedelta(days=self.r.randint(10, 120)), self.now - timedelta(days=1))
                await self.walk(d, lost_stage)
                d.closed_at = end
                d.target_close_date = d.original_close_date = end.date()
                await self.spread_history(d, created, end)
            else:
                target = late if reaches_late else self.pick(open_stages)
                await self.walk(d, target)
                close = self.today + timedelta(days=self.r.randint(-25, 160))
                d.target_close_date = close
                d.original_close_date = close - timedelta(days=self.pick([0, 0, 0, 14, 30]))
                d.close_date_pushes = 0 if d.original_close_date == close else self.r.randint(1, 3)
                if self.r.random() < 0.12:
                    d.forecast_category = self.pick(["commit", "best_case", "pipeline", "omitted"])
                await self.spread_history(d, created, self.now - timedelta(days=self.r.randint(0, 25)))
            self.bump(f"deals_{outcome}")
        self.deals_made = made
        await db.flush()

    async def age_onboarding(self, project, won_at: datetime) -> None:
        age = (self.now - won_at).days
        project.kickoff_date = won_at.date() + timedelta(days=3)
        project.target_go_live = won_at.date() + timedelta(days=60)
        await self.db.refresh(project, ["milestones"])
        for m in sorted(project.milestones, key=lambda m: m.position):
            m.due_date = won_at.date() + timedelta(days=[3, 14, 30, 45, 60, 90][min(m.position, 5)])
            if m.due_date < self.today - timedelta(days=5) and self.r.random() < 0.85:
                m.status, m.completed_at = "done", datetime.combine(m.due_date, datetime.min.time(), UTC)
            elif m.due_date < self.today + timedelta(days=10):
                m.status = self.pick(["in_progress", "in_progress", "blocked"])
        statuses = {m.status for m in project.milestones}
        project.status = ("completed" if statuses == {"done"} else "at_risk" if "blocked" in statuses
                          else "in_progress" if statuses & {"done", "in_progress"} else "not_started")
        self.bump(f"onboarding_{project.status}")
        _ = age

    # ---- 5. activities and tasks ----------------------------------------------------------------------------------------
    async def activities(self) -> None:
        from app.services import embeddings

        db = self.db
        count = 0
        for d in self.deals_made:
            a = await db.get(Account, d.account_id)
            people = [c for c in self.contacts.get(a.id, []) if c.status == "active"] or [None]
            start = d.created_at
            end = d.closed_at or self.now
            for _ in range(self.r.randint(2, 9)):
                when = start + (end - start) * self.r.random()
                c = self.pick(people)
                kind = self.pick(["email", "email", "call", "meeting", "note"], [3, 2, 3, 2, 1])
                sentiment = self.pick(["positive", "neutral", "negative"], [4, 5, 1] if d.closed_at is None or d.stage.is_closed_won else [2, 4, 4])
                summary = {
                    "email": f"{'Sent' if self.r.random() < 0.5 else 'Received'} {self.pick(['pricing follow-up', 'security questionnaire', 'implementation plan', 'reference request'])}.",
                    "call": f"{self.pick(['Discovery', 'Pricing', 'Technical', 'Check-in'])} call: {self.pick(PAINS).lower()}.",
                    "meeting": f"{self.pick(['Demo', 'Workshop', 'Executive briefing', 'QBR'])} with the {self.pick(['ops', 'finance', 'IT', 'sales'])} team.",
                    "note": f"Note: {self.pick(PAINS)}; competitor {self.pick(COMPETITORS)} in the mix.",
                }[kind]
                act = Activity(account_id=a.id, contact_id=c.id if c else None, deal_id=d.id, user_id=d.owner_id, activity_type=kind,
                               summary=summary, sentiment=sentiment, occurred_at=when, source="email_sync" if kind == "email" else "manual",
                               direction=self.pick(["inbound", "outbound"]) if kind in ("email", "call") else None,
                               subject=summary[:80] if kind == "email" else None,
                               duration_seconds=self.pick([300, 900, 1800, 3600]) if kind in ("call", "meeting") else None,
                               disposition=self.pick(["connected", "connected", "left_voicemail", "no_answer", "gatekeeper", "busy"]) if kind == "call" else None,
                               attendance=self.pick(["attended", "attended", "no_show", "cancelled"]) if kind == "meeting" else None,
                               agenda="1. Goals\n2. Demo\n3. Next steps" if kind == "meeting" else None)
                act.embedding = await embeddings.embed(summary)
                db.add(act)
                count += 1
            if d.closed_at is None and self.r.random() < 0.4 and people[0] is not None:  # an upcoming meeting on the calendar
                db.add(Activity(account_id=a.id, contact_id=self.pick(people).id, deal_id=d.id, user_id=d.owner_id, activity_type="meeting",
                                summary="Scheduled: next-step review", sentiment="neutral", attendance="scheduled", duration_seconds=1800,
                                occurred_at=self.now + timedelta(days=self.r.randint(1, 21), hours=self.r.randint(8, 16))))
                count += 1
        await db.flush()
        self.bump("activities", count)

    async def tasks(self) -> None:
        db = self.db
        titles = ["Send revised proposal", "Book technical deep-dive", "Chase signed NDA", "Prepare QBR deck", "Confirm budget owner",
                  "Share security pack", "Update close plan", "Introduce implementation lead", "Follow up on pricing questions", "Map the buying committee"]
        made = 0
        prev = None
        for d in [x for x in self.deals_made if x.closed_at is None] + self.r.sample(self.won, min(len(self.won), self.n(10))):
            for _ in range(self.r.randint(1, 3)):
                due = self.today + timedelta(days=self.r.randint(-20, 30))
                done = due < self.today and self.r.random() < 0.6
                owner = d.owner_id
                assignee = owner if self.r.random() < 0.8 else self.pick([self.sam, self.priya, self.diego]).id
                t = Task(title=self.pick(titles), due_date=due, account_id=d.account_id, deal_id=d.id, owner_id=owner, assignee_id=assignee,
                         priority=self.pick(["low", "normal", "normal", "high", "urgent"]), source=self.pick(["manual", "manual", "ai"]),
                         completed=done, completed_at=datetime.combine(due, datetime.min.time(), UTC) if done else None,
                         depends_on_id=prev.id if prev is not None and self.r.random() < 0.1 else None,
                         created_at=datetime.combine(due, datetime.min.time(), UTC) - timedelta(days=self.r.randint(3, 20)))
                db.add(t)
                await db.flush()
                prev = t
                made += 1
        self.bump("tasks", made)

    # ---- 6. service: cases, comments, SLA outcomes, CSAT, knowledge -----------------------------------------------------
    async def service(self) -> None:
        from app.services import cases

        db = self.db
        agent = self.sofia or self.admin
        customers = [a for a in self.accts if a.lifecycle_stage == "customer"] or self.accts
        made = 0
        for i in range(self.n(70)):
            a = self.pick(customers if self.r.random() < 0.8 else self.accts)
            people = [c for c in self.contacts.get(a.id, []) if c.status == "active"]
            subject, category = self.pick(CASE_SUBJECTS)
            case = await cases.create(db, {"account_id": a.id, "contact_id": self.pick(people).id if people else None, "subject": subject,
                                           "description": f"{subject}. Reported by the customer's team.", "category": category,
                                           "severity": self.pick(["critical", "high", "medium", "medium", "low"]),
                                           "channel": self.pick(["email", "phone", "web", "portal", "chat"])}, agent)
            opened = self.ago(0.2, 120)
            case.opened_at = opened
            await cases.apply_sla(db, case, opened)
            fate = self.pick(["open", "open_breached", "pending", "resolved", "resolved", "resolved", "closed"])
            if fate != "open" and fate != "open_breached":
                await cases.add_comment(db, case, agent, "Thanks for reporting this; we're looking into it.", False)
                late = self.r.random() < 0.2
                case.first_responded_at = opened + timedelta(hours=(case.first_response_due_at - opened).total_seconds() / 3600 * (1.6 if late else 0.5))
                await cases.add_comment(db, case, agent, "Reproduced internally; engineering ticket ENG-" + str(self.r.randint(1000, 9999)), True)
            if fate in ("resolved", "closed"):
                await cases.update(db, case, {"status": fate})
                case.resolved_at = case.first_responded_at + timedelta(hours=self.r.uniform(1, 90))
                if self.r.random() < 0.6 and case.csat_token:
                    score = self.pick([5, 5, 4, 4, 3, 2, 1])
                    await cases.submit_csat(db, case.csat_token, score, self.pick([None, "Quick fix, thanks", "Took a while", "Great support"]))
                    case.csat_at = case.resolved_at + timedelta(hours=6)
            elif fate == "pending":
                case.status = "pending"
            case.sla_breached = bool((case.first_responded_at or self.now) > case.first_response_due_at or
                                     (case.resolved_at or self.now) > case.resolve_due_at)
            if case.sla_breached:
                case.breach_notified_at = case.first_response_due_at
            made += 1
        await db.flush()
        self.bump("cases", made)
        for title, category, body in KB[: max(3, self.n(10))]:
            title = f"{title} ({self.batch})"
            if (await db.execute(select(KbArticle.id).where(KbArticle.title == title))).first():
                continue
            db.add(KbArticle(title=title, body=body, category=category, status="draft" if title.startswith("Draft") else "published",
                             tags=[category.lower()], author_id=agent.id, views=self.r.randint(0, 400), helpful=self.r.randint(0, 40),
                             not_helpful=self.r.randint(0, 8)))
            self.bump("kb_articles")

    # ---- 7. customer success & finance: usage, invoices, contracts, renewals -------------------------------------------
    async def success_finance(self) -> None:
        from app.services import clm

        db = self.db
        customers = [a for a in self.accts if a.lifecycle_stage == "customer"]
        for a in customers:
            licensed = self.pick([50, 120, 250, 400])
            trend = self.pick(["growing", "flat", "declining"])
            for week in range(13):
                base = licensed * {"growing": 0.55 + 0.03 * (12 - week), "flat": 0.75, "declining": 0.9 - 0.04 * (12 - week)}[trend]
                db.add(ProductUsage(account_id=a.id, metric_date=self.today - timedelta(days=7 * week), licensed_users=licensed,
                                    active_users=max(1, int(base + self.r.randint(-5, 5))), feature_adoption=Decimal(self.r.randint(20, 85))))
            for k, (issued_ago, status) in enumerate([(200, "paid"), (140, "paid"), (100, self.pick(["paid", "open"])), (75, self.pick(["paid", "open"])),
                                                       (50, "open"), (20, "open")][: self.r.randint(3, 6)]):
                amt = Decimal(self.r.randint(5, 60) * 1000)
                db.add(Invoice(account_id=a.id, invoice_number=f"INV-{self.batch.upper()}-{a.domain[:6].upper()}-{k}", issue_date=self.today - timedelta(days=issued_ago),
                               due_date=self.today - timedelta(days=issued_ago - 30), amount=amt, balance=Decimal(0) if status == "paid" else amt,
                               status=status, currency=a._currency if a._currency in ("USD", "EUR", "GBP") else "USD"))
                self.bump("invoices")
            self.bump("usage_series")
        # contracts at every point of their life: expired, expiring soon (renewal window), mid-term, just started
        for a in self.r.sample(customers, min(len(customers), self.n(12))):
            for end_in in self.r.sample([-40, 25, 80, 110, 200, 330], 2):
                start = self.today + timedelta(days=end_in) - timedelta(days=364)
                acv = Decimal(self.r.randint(20, 300) * 1000)
                db.add(Contract(account_id=a.id, contract_number=await clm.next_contract_number(db), name=f"{a.name}: Cirra subscription",
                                currency="USD", start_date=start, end_date=start + timedelta(days=364), acv=acv, tcv=acv, payment_terms=a.payment_terms,
                                status="expired" if end_in < 0 else "active",
                                terms={"term_months": 12, "lines": [{"sku": "CIR-PLAT", "quantity": self.r.randint(40, 300), "net_unit_price": 55}]}))
                await db.flush()
                self.bump("contracts")
        out = await clm.run_renewals(db, self.today)
        self.bump("renewal_deals_opened", int(out.get("renewals_created", 0)))

    # ---- 8. partners -------------------------------------------------------------------------------------------------
    async def partner_program(self) -> None:
        from app.services import prm

        db = self.db
        extra = [("Summit Resellers", "reseller", "platinum", ["NA-West", "LATAM"], 15), ("Cloudbridge Technologies", "technology", "silver", ["APAC"], 9)]
        for name, typ, tier, terr, rate in extra:
            name = f"{name} {self.batch.upper()}"
            if not (await db.execute(select(Partner.id).where(Partner.name == name))).first():
                p = Partner(name=name, partner_type=typ, tier=tier, domains=[re.sub(r"[^a-z]+", "", name.lower()) + ".example.com"], territories=terr,
                            commission_rate=rate, referral_fee_rate=5)
                db.add(p)
                self.partners.append(p)
        await db.flush()
        northstar = next((p for p in self.partners if p.name == "Northstar Partners"), self.partners[0])
        if not self.partner_user:
            return
        existing = [a for a in self.accts if "-old" not in a.domain]
        for i in range(self.n(12)):
            conflict = self.r.random() < 0.25
            if conflict:
                a = self.pick(existing)
                company, domain = a.name, a.domain
            else:
                company = f"{self.pick(PREFIX)} {self.pick(['Retail', 'Foods', 'Freight', 'Health'])} {i}{self.batch.upper()}"
                domain = re.sub(r"[^a-z0-9]+", "-", company.lower()) + ".example.com"
            reg = await prm.submit(db, northstar, self.partner_user, {
                "company_name": company, "domain": domain, "contact_name": f"{self.pick(FIRST)} {self.pick(LAST)}",
                "estimated_amount": self.r.randint(15, 180) * 1000, "territory": self.pick(northstar.territories or ["NA-East"]),
                "product_interest": self.pick(["Platform", "Platform + Ambient AI", "promo [Q] + Cirra"]), "notes": "Registered through the partner portal."})
            fate = self.pick(["approve", "approve", "reject", "pending"])
            if fate == "approve" and not conflict:
                await prm.decide(db, reg, self.marcus, True, "Approved: no conflicts.")
            elif fate != "pending":
                await prm.decide(db, reg, self.marcus, False, "Conflicts with an existing account team." if conflict else "Outside the partner's territory.")
            self.bump(f"registrations_{reg.status}")

    # ---- 9. forecasting, quotas, analytics, automation, developer platform -------------------------------------------
    async def planning(self) -> None:
        from app.services import forecasting

        db = self.db
        periods = [p["period"] for p in forecasting.periods()]
        for period in periods:
            for u, amount in ((self.priya, 350000), (self.diego, 350000), (self.marcus, 900000), (self.sam, 120000)):
                if not (await db.execute(select(Quota.id).where(Quota.user_id == u.id, Quota.period == period))).first():
                    db.add(Quota(user_id=u.id, period=period, amount=amount, set_by=self.marcus.id))
                    self.bump("quotas")
        from app.services import fx

        rates = await fx.rates(db)
        for period in periods[:2]:  # last quarter and this quarter: calls as they changed over the weeks
            for u in (self.priya, self.diego, self.sam):
                roll = forecasting.rollup(await forecasting.deals_in(db, [u.id], period), rates)
                for week, factor in ((6, 0.8), (3, 0.92), (0, 1.0)):
                    db.add(ForecastSubmission(user_id=u.id, period=period, scope="self", commit_amount=round(roll["commit_call"] * factor, 2),
                                              best_case_amount=round(roll["best_case_call"] * factor * 1.1, 2), calculated=roll,
                                              note=self.pick(["Holding the call.", "Two deals slipped.", "Upside from renewals."]),
                                              created_at=self.now - timedelta(weeks=week, days=1)))
                    self.bump("forecast_submissions")
                if self.r.random() < 0.6:
                    exists = (await db.execute(select(ForecastAdjustment.id).where(ForecastAdjustment.manager_id == self.marcus.id,
                                                                                   ForecastAdjustment.rep_id == u.id,
                                                                                   ForecastAdjustment.period == period))).first()
                    if not exists:
                        db.add(ForecastAdjustment(manager_id=self.marcus.id, rep_id=u.id, period=period, commit_amount=round(roll["commit_call"] * 0.9, 2),
                                                  best_case_amount=round(roll["best_case_call"], 2), note="Haircut on late-stage slippage."))
                        self.bump("forecast_adjustments")
            db.add(ForecastSubmission(user_id=self.marcus.id, period=period, scope="team", commit_amount=820000, best_case_amount=1050000,
                                      calculated={}, note="Team call after the weekly review.", created_at=self.now - timedelta(days=2)))
        # a second plan variant for SDRs so every seller type has one
        if not (await db.execute(select(CommissionPlan.id).where(CommissionPlan.name == "SDR sourced pipeline 2026"))).first():
            db.add(CommissionPlan(name="SDR sourced pipeline 2026", description="2% of bookings on sourced deals, 3% above quota.", base_rate=2,
                                  tiers=[{"from_pct": 100, "rate": 3}], roles=["sdr"]))

    async def analytics_automation_dev(self) -> None:
        from app.services import developer, reporting, workflows

        db = self.db
        reports = [
            (self.marcus, "Won revenue by territory and quarter", {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Won"}],
                                                                 "group_by": [{"field": "territory"}, {"field": "closed", "bucket": "quarter"}],
                                                                 "measures": [{"agg": "sum", "field": "amount_usd"}], "chart": {"type": "stacked"}}),
            (self.marcus, "Loss reasons this year", {"source": "deals", "filters": [{"field": "status", "op": "eq", "value": "Lost"}],
                                                     "group_by": [{"field": "loss_reason"}], "measures": [{"agg": "count"}], "chart": {"type": "bar"}}),
            (self.sofia or self.admin, "Cases by priority and status", {"source": "cases", "group_by": [{"field": "priority"}, {"field": "status"}],
                                                                        "measures": [{"agg": "count"}], "chart": {"type": "stacked"}}),
            (self.sofia or self.admin, "CSAT by category", {"source": "cases", "filters": [{"field": "csat", "op": "not_empty"}],
                                                            "group_by": [{"field": "category"}], "measures": [{"agg": "avg", "field": "csat"}], "chart": {"type": "bar"}}),
            (self.nina or self.marcus, "Campaign spend vs expected revenue", {"source": "campaigns", "group_by": [{"field": "type"}],
                                                                             "measures": [{"agg": "sum", "field": "actual_cost"}, {"agg": "sum", "field": "expected_revenue"}],
                                                                             "chart": {"type": "column"}}),
            (self.nina or self.marcus, "Leads by source and status", {"source": "leads", "group_by": [{"field": "source"}, {"field": "status"}],
                                                                      "measures": [{"agg": "count"}], "chart": {"type": "stacked"}}),
        ]
        by_owner: dict = {}
        for owner, name, defn in reports:
            reporting.validate(defn)  # same checks as the report builder
            name = f"{name} ({self.batch})"
            r = SavedReport(name=name, owner_id=owner.id, source=defn["source"], definition=defn, visibility="shared")
            db.add(r)
            await db.flush()
            by_owner.setdefault(owner.id, []).append(r)
            self.bump("saved_reports")
        for owner_id, rs in by_owner.items():
            db.add(Dashboard(name=f"Team view ({self.batch})", owner_id=owner_id, visibility="shared",
                             tiles=[{"report_id": str(r.id), "size": "half"} for r in rs]))
            self.bump("dashboards")
        for name, source, trigger, conds, actions in (
            ("Critical case: alert the support managers", "cases", {"type": "created"}, [{"field": "priority", "op": "eq", "value": "critical"}],
             [{"type": "notify", "to": ["owner", "role:sales_manager"], "title": "Critical case {{case_number}}: {{subject}}"}]),
            ("Big win: tell finance", "deals", {"type": "updated", "fields": ["stage"]}, [{"field": "status", "op": "eq", "value": "Won"},
                                                                                         {"field": "amount_usd", "op": "gte", "value": 200000}],
             [{"type": "emit_event", "event": "deal.big_win"}]),
        ):
            if not (await db.execute(select(WorkflowRule.id).where(WorkflowRule.name == name))).first():
                workflows.validate(source, trigger, conds, actions)
                db.add(WorkflowRule(name=name, enabled=True, source=source, trigger=trigger, conditions=conds, actions=actions, created_by=self.admin.id))
                self.bump("workflow_rules")
        # developer platform: an active read-only BI key, a revoked key, and a webhook with delivery history
        for label, ro, revoked in ((f"Data warehouse sync ({self.batch})", True, False), (f"Old middleware ({self.batch})", False, True)):
            _, prefix, digest = developer.new_key()
            db.add(ApiKey(name=label, prefix=prefix, key_hash=digest, user_id=self.marcus.id, read_only=ro, created_by=self.admin.id,
                          last_used_at=self.ago(0, 3), revoked_at=self.ago(5, 30) if revoked else None))
            self.bump("api_keys")
        _, enc = developer.new_secret()
        sub = WebhookSubscription(name=f"BI event stream ({self.batch})", url="https://hooks.example.com/cirra", event_types=["deal.*", "case.*"],
                                  secret_enc=enc, active=False, disabled_reason="Demo endpoint: deliveries are illustrative",
                                  cursor_event_id=0, created_by=self.admin.id, last_success_at=self.ago(1, 2), last_failure_at=self.ago(0, 1))
        db.add(sub)
        await db.flush()
        events = (await db.execute(select(IntegrationEvent).where(IntegrationEvent.event_type.like("deal.%"))
                                   .order_by(IntegrationEvent.id.desc()).limit(self.n(25)))).scalars().all()
        for ev in events:
            status = self.pick(["success", "success", "success", "failed", "dead"])
            db.add(WebhookDelivery(subscription_id=sub.id, event_id=ev.id, event_type=ev.event_type, status=status,
                                   attempts=1 if status == "success" else 3 if status == "failed" else 6,
                                   response_code=204 if status == "success" else self.pick([500, 502, 503, None]),
                                   error=None if status == "success" else self.pick(["HTTP 503", "Connection timed out"]),
                                   duration_ms=self.r.randint(40, 900), created_at=ev.created_at,
                                   delivered_at=ev.created_at + timedelta(seconds=2) if status == "success" else None,
                                   next_attempt_at=self.now + timedelta(minutes=30)))
            self.bump("webhook_deliveries")
        sub.cursor_event_id = events[0].id if events else 0

    async def privacy(self) -> None:
        from app.services import privacy

        db = self.db
        people = [c for v in self.contacts.values() for c in v if c.status == "active"]
        for c in self.r.sample(people, min(len(people), self.n(12))):
            await privacy.record_consent(db, c, consent_email=self.pick(["granted", "denied"]), basis=self.pick(["consent", "legitimate_interest"]),
                                         opt_outs={"phone": self.r.random() < 0.3}, source="preference centre")
            self.bump("consent_changes")
        victim = self.pick(people)
        await privacy.erase_contact(db, victim, "GDPR")
        self.bump("erasures")

    # ---- 10. recompute the derived layer ------------------------------------------------------------------------------
    async def finish(self) -> None:
        from app.services import cases, embeddings, insights, leads as lead_svc, performance, scoring, sla, workflows
        from app.services.search import account_document

        db = self.db
        await db.flush()
        for a in self.accts:
            a.embedding = await embeddings.embed(account_document(a))
            await scoring.rescore_account(db, a.id)
        await lead_svc.rescore_open(db)
        await performance.realign(db, apply=True)
        await db.commit()
        await insights.scan_pipeline(db)
        await sla.escalate_overdue(db)
        await cases.scan_breaches(db)
        await workflows.run_scheduled(db)
        await db.commit()


async def generate(scale: float = 1.0, batch: str = "v1", seed: int = 7) -> dict:
    batch = re.sub(r"[^a-z0-9]+", "", batch.lower()) or "v1"
    async with SessionLocal() as db:
        marker = f"demo_volume:{batch}"
        if await db.get(AppSetting, marker):
            print(f"Batch '{batch}' was already generated; pick another --batch to add more.")
            return {}
        g = Gen(db, batch, scale, seed)
        await g.load()
        g.converted_deals = []
        steps = [("accounts & contacts", g.accounts), ("campaigns", g.campaigns), ("leads", g.leads), ("opportunities, CPQ, CLM, orders", g.deals),
                 ("activities", g.activities), ("tasks", g.tasks), ("campaign members", g.campaign_members), ("service", g.service),
                 ("success & finance", g.success_finance), ("partners", g.partner_program), ("forecasting & quotas", g.planning),
                 ("analytics, automation, developer", g.analytics_automation_dev), ("privacy", g.privacy), ("scores & scans", g.finish)]
        for label, fn in steps:
            print(f"  {label}...", flush=True)
            await fn()
            await db.flush()
        db.add(AppSetting(key=marker, value={"scale": scale, "seed": seed, "at": g.now.isoformat(), "counts": g.stats}))
        await db.commit()
        return g.stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=float, default=1.0, help="multiplier for record counts (default 1)")
    ap.add_argument("--batch", default="v1", help="batch tag; each batch runs once (default v1)")
    ap.add_argument("--seed", type=int, default=7, help="random seed, for reproducible data")
    args = ap.parse_args()
    stats = asyncio.run(generate(args.scale, args.batch, args.seed))
    if stats:
        width = max(len(k) for k in stats)
        print("\nCreated:")
        for k in sorted(stats):
            print(f"  {k.ljust(width)}  {stats[k]}")
