"""Seed Cirra with a demo workspace that exercises every capability pillar.

    python -m app.seed            # full demo dataset (idempotent)
    python -m app.seed --minimal  # specification minimum: 3 accounts, 6 contacts, 3 active deals
    python -m app.seed --reset    # wipe everything (including the append-only ledgers) first

Four pipelines (Enterprise Direct, Inbound Mid-Market, Renewals & Upsells,
Partner Channels) each with their own stage gates; product catalog with tiered
rate cards; approval policies; document templates; partners and a partner
portal user; contracts, onboarding, tickets, usage and ERP-synced invoices.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone

from fpdf import FPDF
from sqlalchemy import func, select, text

from app.core.database import SessionLocal
from app.core.rbac import seed_permissions
from app.core.security import hash_password
from app.models import (
    Account, Activity, ApprovalGroup, ApprovalPolicy, AssignmentRule, BundleComponent, Collateral, Contact, Contract, CustomFieldDefinition, Deal, DealPartner, DealRegistration,
    DealStageHistory, EngagementEvent, FxRate, IntakeKey, Lead, OnboardingMilestone, Partner, Pipeline, PipelineStage, PriceBook,
    PriceBookEntry, Product, ProductRule, ProductUsage, Promotion, Quote,
    SupportTicket, Task, User,
)
from app.services import clm, cpq, embeddings, erp, fx, insights, prm, scoring, sla, storage
from app.services.cpq import LEVEL_LABELS
from app.services.pipeline_templates import PIPELINES
from app.services.search import account_document
from app.services.success import provision_onboarding

DEMO_PASSWORD = "cirra123"

# (email, name, role, manager email)
USERS = [
    ("admin@cirra.demo", "Avery Admin", "super_admin", None),
    ("marcus@cirra.demo", "Marcus Vance", "sales_manager", "admin@cirra.demo"),
    ("priya@cirra.demo", "Priya Raman", "account_executive", "marcus@cirra.demo"),
    ("diego@cirra.demo", "Diego Alvarez", "account_executive", "marcus@cirra.demo"),
    ("sam@cirra.demo", "Sam Okoye", "sdr", "marcus@cirra.demo"),
    ("viewer@cirra.demo", "Robin Viewer", "auditor", None),
]
PARTNER_USER = ("partner@northstar-partners.com", "Nia Fontaine")

# Each account: (name, domain, industry, tier, owner_email, contacts, deals, activities, tasks)
# contacts: (first, last, email, title, role)
# deals: (title, amount, stage, days_in_stage, close_in_days, loss_reason)
# activities: (days_ago, type, sentiment, summary)
# tasks: (title, due_in_days)
ACCOUNTS = [
    (
        "Apex Industrial Supply", "apexindustrial.com", "Industrial Distribution", "Mid-Market", "marcus@cirra.demo",
        [
            ("Elena", "Rostova", "elena.rostova@apexindustrial.com", "VP Procurement", "Decision Maker"),
            ("James", "Cole", "james.cole@apexindustrial.com", "IT Director", "Blocker"),
        ],
        [("Supply Chain Analytics Platform", 75000, "Proposal/InfoSec", 6, 35, None)],
        [
            (1, "meeting", "positive", "InfoSec security clearance review for on-premises deployment. Elena confirmed budget is approved for Q4 and pricing was well received."),
            (8, "email", "neutral", "Sent the SOC 2 Type II report and the completed security questionnaire to James Cole."),
            (15, "meeting", "positive", "Solution demo for the procurement and IT teams. Evaluation criteria agreed in writing: forecast accuracy, ERP integration, on-prem hosting."),
            (26, "call", "neutral", "Discovery call. Pain: forecasting happens in spreadsheets and stock-outs cost ~$400k a year. Currently on Salesforce for CRM."),
        ],
        [("Answer James Cole's follow-up questions on data residency", 2), ("Send final pricing proposal to Elena", 5)],
    ),
    (
        "Northwind Logistics", "northwind.io", "Logistics", "Enterprise", "priya@cirra.demo",
        [
            ("Samantha", "Okafor", "s.okafor@northwind.io", "Chief Operating Officer", "Economic Buyer"),
            ("Liam", "Chen", "liam.chen@northwind.io", "Fleet Systems Manager", "Evaluator"),
        ],
        [("Fleet Telemetry Rollout", 180000, "Solution Demo", 24, 60, None)],
        [
            (19, "meeting", "negative", "Pilot review: Liam raised concerns that telematics data latency is too high. Samantha worried about rollout timeline slipping into next year."),
            (27, "meeting", "positive", "Live demo of the telemetry dashboards. Team impressed with route analytics."),
            (40, "call", "neutral", "Intro call. Pain: no visibility into idle time across 1,200 trucks. Evaluating HubSpot and one other vendor."),
        ],
        [("Share latency benchmark results with Liam", -3)],
    ),
    (
        "Helios Energy", "heliosenergy.com", "Energy & Utilities", "Enterprise", "diego@cirra.demo",
        [
            ("Marta", "Keller", "marta.keller@heliosenergy.com", "Head of Digital Transformation", "Champion"),
            ("Raj", "Patel", "raj.patel@heliosenergy.com", "CFO", "Economic Buyer"),
        ],
        [("Grid Asset Intelligence Suite", 240000, "Pain Fit", 9, 90, None)],
        [
            (3, "call", "positive", "Marta is championing the project internally and has secured a slot with CFO Raj Patel. Pain: manual asset inspections are slow and error-prone."),
            (12, "note", "neutral", "Research: Helios announced a $2B grid modernization program. Strong fit for predictive maintenance."),
        ],
        [("Prepare ROI model for Raj Patel", 4), ("Book executive alignment meeting", 7)],
    ),
    (
        "Bluepeak Health", "bluepeakhealth.org", "Healthcare", "Enterprise", "marcus@cirra.demo",
        [
            ("Nora", "Lindqvist", "nora.lindqvist@bluepeakhealth.org", "CIO", "Decision Maker"),
            ("Tom", "Becker", "tom.becker@bluepeakhealth.org", "Procurement Lead", "Evaluator"),
            ("Aisha", "Grant", "aisha.grant@bluepeakhealth.org", "Security Architect", "Influencer"),
        ],
        [("Patient Engagement Platform", 320000, "Closed-Won", 12, -12, None), ("Analytics Expansion", 95000, "Discovery", 4, 120, None)],
        [
            (4, "meeting", "positive", "Kickoff for the analytics expansion. Nora wants a Q1 business case. Great momentum after the platform win."),
            (12, "email", "positive", "Signed MSA and purchase order received for the Patient Engagement Platform. PO #BP-5531."),
            (30, "meeting", "neutral", "HIPAA and InfoSec review with Aisha Grant: all findings resolved."),
        ],
        [("Draft analytics expansion business case", 10)],
    ),
    (
        "Cobalt Retail Group", "cobaltretail.com", "Retail", "Mid-Market", "priya@cirra.demo",
        [
            ("Grace", "Huang", "grace.huang@cobaltretail.com", "Director of eCommerce", "Evaluator"),
        ],
        [("Omnichannel Promotions Engine", 64000, "Discovery", 30, 45, None)],
        [
            (31, "email", "neutral", "Grace asked for a case study on promotions optimization. Sent the promo [Q] overview."),
        ],
        [("Follow up with Grace on case study", -10)],
    ),
    (
        "Summit Foods Co.", "summitfoods.com", "Food & Beverage", "Mid-Market", "diego@cirra.demo",
        [
            ("Oliver", "Grant", "oliver.grant@summitfoods.com", "VP Finance", "Economic Buyer"),
            ("Chloe", "Martin", "chloe.martin@summitfoods.com", "Trade Marketing Manager", "Champion"),
        ],
        [("Trade Deduction Automation", 88000, "Proposal/InfoSec", 3, 21, None)],
        [
            (2, "call", "positive", "Chloe confirmed legal redlines are nearly done; pricing accepted by Oliver. Target signature end of month."),
            (9, "meeting", "positive", "Demo of deduct automation. Evaluation criteria agreed: 60% auto-resolution of deductions."),
        ],
        [("Send redlined MSA back to legal", 1)],
    ),
    (
        "Vertex Manufacturing", "vertexmfg.com", "Manufacturing", "SMB", "priya@cirra.demo",
        [
            ("Ben", "Russo", "ben.russo@vertexmfg.com", "Operations Manager", "Evaluator"),
        ],
        [("Yield Optimization Pilot", 42000, "Closed-Lost", 20, -20, "competitor")],
        [
            (20, "email", "negative", "Ben informed us they selected a competitor (SAP add-on) due to existing ERP licensing. Price was not the main factor."),
            (45, "meeting", "neutral", "Pilot scoping workshop. Concerns about integration effort with the SAP ERP."),
        ],
        [],
    ),
    (
        "Orion Financial", "orionfinancial.com", "Financial Services", "Enterprise", "marcus@cirra.demo",
        [
            ("Hannah", "Weiss", "hannah.weiss@orionfinancial.com", "SVP Revenue Operations", "Champion"),
            ("Kevin", "Doyle", "kevin.doyle@orionfinancial.com", "CISO", "Blocker"),
        ],
        [("Revenue Intelligence Platform", 410000, "Solution Demo", 11, 75, None)],
        [
            (5, "meeting", "neutral", "Technical deep dive. Kevin Doyle pushed back on cloud hosting; Cirra's private-cloud Docker deployment addressed most concerns."),
            (11, "meeting", "positive", "Executive demo with Hannah's team. Strong excitement about ambient note capture and pipeline risk scoring."),
        ],
        [("Send private-cloud architecture doc to Kevin Doyle", 3)],
    ),
]

# Firmographics & customer master per account
FIRMO = {
    "Apex Industrial Supply": dict(annual_revenue=420_000_000, employee_count=1800, industry_code="423840", legal_name="Apex Industrial Supply LLC",
                                   locations=[{"type": "HQ", "city": "Cleveland", "region": "OH", "country": "US"}, {"type": "DC", "city": "Dallas", "region": "TX", "country": "US"}],
                                   custom={"erp_region": "NA", "strategic_account": True}),
    "Northwind Logistics": dict(annual_revenue=1_250_000_000, employee_count=6400, industry_code="484121", locations=[{"type": "HQ", "city": "Rotterdam", "country": "NL"}],
                                custom={"erp_region": "EMEA"}),
    "Helios Energy": dict(annual_revenue=5_800_000_000, employee_count=14200, industry_code="221122", legal_name="Helios Energy AG",
                          locations=[{"type": "HQ", "city": "Munich", "country": "DE"}], custom={"erp_region": "EMEA", "strategic_account": True}),
    "Bluepeak Health": dict(annual_revenue=2_100_000_000, employee_count=9800, industry_code="622110", legal_name="Bluepeak Health System Inc.", lifecycle="customer",
                            locations=[{"type": "HQ", "city": "Denver", "region": "CO", "country": "US"}], custom={"erp_region": "NA"}),
    "Cobalt Retail Group": dict(annual_revenue=310_000_000, employee_count=2300, industry_code="452319", locations=[{"type": "HQ", "city": "Atlanta", "region": "GA", "country": "US"}]),
    "Summit Foods Co.": dict(annual_revenue=640_000_000, employee_count=3100, industry_code="311999", legal_name="Summit Foods Company",
                             locations=[{"type": "HQ", "city": "Minneapolis", "region": "MN", "country": "US"}]),
    "Vertex Manufacturing": dict(annual_revenue=85_000_000, employee_count=420, industry_code="332710", locations=[{"type": "HQ", "city": "Toledo", "region": "OH", "country": "US"}]),
    "Orion Financial": dict(annual_revenue=3_400_000_000, employee_count=7600, industry_code="523920", legal_name="Orion Financial Corp.",
                            locations=[{"type": "HQ", "city": "Boston", "region": "MA", "country": "US"}, {"type": "Office", "city": "London", "country": "GB"}]),
}

# Extra contact profile data: email -> fields
CONTACT_PROFILE = {
    "elena.rostova@apexindustrial.com": dict(timezone="America/New_York", department="Procurement", mobile="+1 216 555 0142",
                                             linkedin_url="https://www.linkedin.com/in/elena-rostova", privacy_regime="CCPA", consent_email="granted", consent_basis="consent"),
    "james.cole@apexindustrial.com": dict(timezone="America/New_York", department="IT", privacy_regime="CCPA", consent_email="granted", consent_basis="consent"),
    "s.okafor@northwind.io": dict(timezone="Europe/Amsterdam", department="Operations", privacy_regime="GDPR", consent_email="granted", consent_basis="consent"),
    "liam.chen@northwind.io": dict(timezone="Europe/Amsterdam", department="Fleet Systems", privacy_regime="GDPR", consent_email="unknown"),
    "marta.keller@heliosenergy.com": dict(timezone="Europe/Berlin", department="Digital Transformation", privacy_regime="GDPR",
                                          consent_email="unknown", consent_basis="legitimate_interest"),
    "raj.patel@heliosenergy.com": dict(timezone="Europe/Berlin", department="Finance", privacy_regime="GDPR", consent_email="granted", consent_basis="consent"),
    "nora.lindqvist@bluepeakhealth.org": dict(timezone="America/Denver", department="IT", privacy_regime="CCPA", consent_email="granted", consent_basis="contract"),
    "grace.huang@cobaltretail.com": dict(timezone="America/New_York", department="eCommerce", opt_out_email=True, privacy_regime="CCPA"),
    "hannah.weiss@orionfinancial.com": dict(timezone="America/New_York", department="Revenue Operations", privacy_regime="CCPA", consent_email="granted", consent_basis="consent"),
}

PRODUCTS = [
    ("CIR-PLAT", "Cirra Platform", "Private-cloud CRM subscription", "Cirra", "recurring", "user / month",
     {"USD": [(1, 65), (100, 58), (500, 49)], "EUR": [(1, 60), (100, 53), (500, 45)], "GBP": [(1, 52), (100, 46), (500, 39)]}),
    ("CIR-AI", "Ambient AI add-on", "Quick-Log, voice transcription, copilot", "Cirra", "recurring", "user / month",
     {"USD": [(1, 20), (100, 17), (500, 14)], "EUR": [(1, 18), (100, 16), (500, 13)]}),
    ("CIR-SUP", "Premium Support", "24x7 support with named TAM", "Cirra", "recurring", "org / month", {"USD": [(1, 1500)], "EUR": [(1, 1400)]}),
    ("CIR-IMPL", "Implementation Services", "Deployment, migration and enablement", "Services", "one_time", "project", {"USD": [(1, 15000)], "EUR": [(1, 14000)]}),
    ("PROMO-Q", "promo [Q] module", "SDC Solutions module", "SDC Solutions", "recurring", "user / month", {"USD": [(1, 45), (100, 40)]}),
    ("YIELD-S", "Yield [S] module", "SDC Solutions module", "SDC Solutions", "recurring", "user / month", {"USD": [(1, 55), (100, 49)]}),
    ("DEDUCT", "deduct module", "SDC Solutions module", "SDC Solutions", "recurring", "user / month", {"USD": [(1, 40), (100, 35)]}),
]

POLICIES = [  # sequential chain: sales manager -> deal desk -> VP sales -> finance -> legal
    ("Discount above 10% needs sales manager", "discount_pct", 10, "sales_manager"),
    ("Discount above 20% needs deal desk", "discount_pct", 20, "deal_desk"),
    ("Discount above 30% needs VP Sales", "discount_pct", 30, "vp_sales"),
    ("Deals above 500k TCV need VP Sales", "tcv", 500000, "vp_sales"),
    ("Payment terms beyond NET45 need finance", "payment_terms", 45, "finance"),
    ("Accounts on credit hold need finance", "credit_hold", None, "finance"),
    ("Credit risk score 70+ needs finance", "credit_risk", 70, "finance"),
    ("Non-standard legal terms need legal", "custom_terms", None, "legal"),
]
APPROVER_USERS = [  # approval authority comes from approval groups, not from the RBAC role
    ("dana@cirra.demo", "Dana Whitfield", "sales_manager", "deal_desk"),
    ("victor@cirra.demo", "Victor Osei", "sales_manager", "vp_sales"),
    ("fiona@cirra.demo", "Fiona Brandt", "auditor", "finance"),
    ("lena@cirra.demo", "Lena Kowalski", "auditor", "legal"),
]

CUSTOM_FIELDS = [
    ("account", "erp_region", "ERP region", "select", ["NA", "EMEA", "APAC"]),
    ("account", "strategic_account", "Strategic account", "boolean", []),
    ("account", "procurement_portal", "Procurement portal", "url", []),
    ("contact", "preferred_language", "Preferred language", "select", ["English", "German", "Dutch", "French"]),
    ("deal", "competitor_incumbent", "Incumbent vendor", "text", []),
]


def _pdf(title: str, lines: list[str]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for line in lines:
        pdf.multi_cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


async def _reset(db) -> None:
    # GUC name is fixed by the append-only trigger in migration 002 (internal identifier, predates the Cirra name)
    await db.execute(text("SET LOCAL relate.allow_ledger_reset = 'on'"))
    tables = (await db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"))).scalars().all()
    await db.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    await db.commit()


async def seed(minimal: bool = False, reset: bool = False) -> None:
    async with SessionLocal() as db:
        if reset:
            await _reset(db)
        if (await db.execute(select(func.count()).select_from(User))).scalar_one():
            print("Database already seeded. Use --reset to start over.")
            return

        now = datetime.now(timezone.utc)
        today = date.today()

        # ---- platform core -----------------------------------------------------------------
        await seed_permissions(db)
        for cur, rate in fx.DEFAULT_RATES.items():
            db.add(FxRate(currency=cur, rate_to_usd=rate))
        users: dict[str, User] = {}
        for email, name, role, _ in USERS:
            users[email] = User(email=email, full_name=name, role=role, password_hash=hash_password(DEMO_PASSWORD))
            db.add(users[email])
        await db.flush()
        for email, _, _, manager in USERS:
            if manager:
                users[email].manager_id = users[manager].id
        for entity, key, label, ftype, options in CUSTOM_FIELDS:
            db.add(CustomFieldDefinition(entity=entity, key=key, label=label, field_type=ftype, options=options))

        pipelines: dict[str, Pipeline] = {}
        stages: dict[tuple[str, str], PipelineStage] = {}
        for spec in PIPELINES:
            p = Pipeline(name=spec["name"], kind=spec["kind"], is_default=spec["is_default"], description=spec["description"])
            db.add(p)
            await db.flush()
            key = spec.get("key", spec["kind"])
            pipelines[key] = p
            for order, (name, prob, rules) in enumerate(spec["stages"], start=1):
                s = PipelineStage(pipeline_id=p.id, name=name, stage_order=order, default_probability=prob, gate_rules=rules,
                                  is_closed_won=name == "Closed-Won", is_closed_lost=name == "Closed-Lost")
                db.add(s)
                stages[(key, name)] = s
        await db.flush()

        products: dict[str, Product] = {}
        for sku, name, desc, family, billing, unit, prices in PRODUCTS:
            prod = Product(sku=sku, name=name, description=desc, family=family, billing_type=billing, unit=unit,
                           prices=[PriceBookEntry(currency=c, tiers=[{"min_qty": q, "unit_price": up} for q, up in tiers]) for c, tiers in prices.items()])
            db.add(prod)
            products[sku] = prod
        for name, rule, threshold, role in POLICIES:
            db.add(ApprovalPolicy(name=name, rule_type=rule, threshold=threshold, approver_role=role))
        groups: dict[str, list[str]] = {"deal_desk": [], "vp_sales": [], "finance": [], "legal": []}
        for email, name, role, group in APPROVER_USERS:
            users[email] = User(email=email, full_name=name, role=role, password_hash=hash_password(DEMO_PASSWORD),
                                manager_id=users["admin@cirra.demo"].id)
            db.add(users[email])
            await db.flush()
            groups[group].append(str(users[email].id))
        for key, members in groups.items():
            db.add(ApprovalGroup(key=key, name=LEVEL_LABELS[key], member_ids=members))
        # deal desk catalogue: a bundle with dependency / exclusion rules and a launch promotion
        growth = Product(sku="CIR-GROWTH", name="Cirra Growth Bundle", description="Platform + Ambient AI per user, one price", family="Cirra",
                         billing_type="recurring", unit="user / month", product_type="bundle",
                         prices=[PriceBookEntry(currency="USD", tiers=[{"min_qty": 1, "unit_price": 78}, {"min_qty": 100, "unit_price": 69}]),
                                 PriceBookEntry(currency="EUR", tiers=[{"min_qty": 1, "unit_price": 72}, {"min_qty": 100, "unit_price": 64}])])
        db.add(growth)
        products["CIR-GROWTH"] = growth
        await db.flush()
        db.add_all([BundleComponent(bundle_id=growth.id, component_id=products["CIR-PLAT"].id, quantity=1),
                    BundleComponent(bundle_id=growth.id, component_id=products["CIR-AI"].id, quantity=1),
                    ProductRule(product_id=products["CIR-AI"].id, rule_type="requires", target_product_id=products["CIR-PLAT"].id,
                                message="Ambient AI add-on requires the Cirra Platform"),
                    ProductRule(product_id=growth.id, rule_type="excludes", target_product_id=products["CIR-SUP"].id,
                                message="The Growth Bundle cannot be combined with Premium Support; quote Enterprise instead"),
                    Promotion(code="LAUNCH-AI", name="Ambient AI launch offer", discount_pct=15, product_ids=[str(products["CIR-AI"].id)],
                              min_quantity=50, valid_from=today - timedelta(days=30), valid_to=today + timedelta(days=90))])
        await clm.ensure_templates(db)
        await db.flush()

        # ---- accounts, contacts, deals, activities --------------------------------------------
        direct = pipelines["direct"]
        accounts: dict[str, Account] = {}
        deals: dict[str, Deal] = {}
        contacts_by_email: dict[str, Contact] = {}
        dataset = ACCOUNTS[:3] if minimal else ACCOUNTS
        for name, domain, industry, tier, owner_email, contacts, deal_specs, activities, tasks in dataset:
            owner = users[owner_email]
            f = FIRMO.get(name, {})
            account = Account(name=name, domain=domain, industry=industry, tier=tier, owner_id=owner.id, created_at=now - timedelta(days=60),
                              annual_revenue=f.get("annual_revenue"), employee_count=f.get("employee_count"), industry_code=f.get("industry_code"),
                              legal_name=f.get("legal_name"), locations=f.get("locations", []), lifecycle_stage=f.get("lifecycle", "prospect"),
                              custom_metadata=dict(f.get("custom", {})))
            db.add(account)
            await db.flush()
            accounts[name] = account
            rows = []
            for first, last, email, title, role in contacts:
                c = Contact(account_id=account.id, first_name=first, last_name=last, email=email, job_title=title, buying_role=role,
                            **CONTACT_PROFILE.get(email, {}))
                db.add(c)
                rows.append(c)
                contacts_by_email[email] = c
            await db.flush()
            key_contact = next((c for c in rows if c.buying_role in ("Champion", "Decision Maker")), rows[0])

            deal_rows = []
            for title, amount, stage_name, days_in_stage, close_in, loss_reason in deal_specs:
                stage = stages[("direct", stage_name)]
                entered = now - timedelta(days=days_in_stage)
                d = Deal(title=title, account_id=account.id, pipeline_id=direct.id, stage_id=stage.id, owner_id=owner.id, amount=amount,
                         target_close_date=today + timedelta(days=close_in), original_close_date=today + timedelta(days=close_in),
                         primary_contact_id=key_contact.id, stage_entered_at=entered, created_at=entered - timedelta(days=10 * stage.stage_order),
                         closed_at=entered if stage.is_closed else None, loss_reason=loss_reason,
                         loss_debrief="Selected an SAP add-on bundled with their existing ERP licence; integration effort was the deciding factor." if loss_reason else None,
                         loss_competitor="SAP" if loss_reason == "competitor" else None, risk_factors={}, ai_insights={})
                db.add(d)
                deal_rows.append(d)
                deals[title] = d
                await db.flush()
                path = [s for s in PIPELINES[0]["stages"][:4] if stages[("direct", s[0])].stage_order <= min(stage.stage_order, 4 if stage.is_closed_won else 2 if stage.is_closed_lost else stage.stage_order)]
                names = [s[0] for s in path] + ([stage_name] if stage.is_closed else [])
                prev = None
                for i, n in enumerate(names):
                    to = stages[("direct", n)]
                    db.add(DealStageHistory(deal_id=d.id, from_stage_id=prev.id if prev else None, to_stage_id=to.id, changed_by=owner.id,
                                            changed_at=entered - timedelta(days=7 * (len(names) - 1 - i))))
                    prev = to

            main_deal = deal_rows[0] if deal_rows else None
            for days_ago, kind, sentiment, summary in activities:
                a = Activity(account_id=account.id, deal_id=main_deal.id if main_deal else None, contact_id=key_contact.id, user_id=owner.id,
                             activity_type=kind, sentiment=sentiment, summary=summary, occurred_at=now - timedelta(days=days_ago, hours=3),
                             direction="outbound" if kind == "email" else None, attendance="attended" if kind == "meeting" else None,
                             duration_seconds=1800 if kind == "call" else 3600 if kind == "meeting" else None,
                             disposition="connected" if kind == "call" else None)
                a.embedding = await embeddings.embed(summary)
                db.add(a)
            for title, due_in in tasks:
                db.add(Task(title=title, due_date=today + timedelta(days=due_in), account_id=account.id, deal_id=main_deal.id if main_deal else None,
                            owner_id=owner.id, assignee_id=owner.id, source="ai", priority="high" if due_in < 0 else "normal"))
            await db.flush()

        insights_by_deal = {
            "Supply Chain Analytics Platform": {"competitors": ["Salesforce"], "pain_points": ["Forecasting happens in spreadsheets and stock-outs cost ~$400k a year."]},
            "Fleet Telemetry Rollout": {"competitors": ["HubSpot"], "pain_points": ["No visibility into idle time across 1,200 trucks."]},
            "Grid Asset Intelligence Suite": {"pain_points": ["Manual asset inspections are slow and error-prone."]},
        }
        for title, ins in insights_by_deal.items():
            if title in deals:
                deals[title].ai_insights = ins

        if not minimal:
            await _enterprise(db, users, accounts, deals, contacts_by_email, stages, pipelines, products, now, today)
            await _lead_to_order(db, users, accounts, stages, pipelines, products, now, today)

        await db.flush()
        for account in (await db.execute(select(Account))).scalars().unique().all():
            account.embedding = await embeddings.embed(account_document(account))
            await scoring.rescore_account(db, account.id)
        await db.commit()
        if not minimal:
            await insights.scan_pipeline(db)
            await sla.escalate_overdue(db)
        total = (await db.execute(select(func.count()).select_from(Account))).scalar_one()
        print(f"Seeded {total} accounts. Log in with marcus@cirra.demo / {DEMO_PASSWORD} "
              f"(partner portal: {PARTNER_USER[0]} / {DEMO_PASSWORD})")


async def _enterprise(db, users, accounts, deals, contacts, stages, pipelines, products, now, today) -> None:
    marcus, priya, diego, sam, admin = (users[e] for e in ("marcus@cirra.demo", "priya@cirra.demo", "diego@cirra.demo", "sam@cirra.demo", "admin@cirra.demo"))

    # -- hierarchies (pillar 1) -------------------------------------------------------------------
    helios_group = Account(name="Helios Group", domain="heliosgroup.com", industry="Energy & Utilities", tier="Enterprise", owner_id=diego.id,
                           annual_revenue=11_200_000_000, employee_count=31000, legal_name="Helios Group SE", custom_metadata={"erp_region": "EMEA"},
                           locations=[{"type": "HQ", "city": "Frankfurt", "country": "DE"}], created_at=now - timedelta(days=200))
    renewables = Account(name="Helios Renewables", domain="heliosrenewables.com", industry="Energy & Utilities", tier="Mid-Market", owner_id=diego.id,
                         annual_revenue=900_000_000, employee_count=2100, lifecycle_stage="customer", legal_name="Helios Renewables GmbH",
                         payment_terms="NET30", custom_metadata={"erp_region": "EMEA"}, locations=[{"type": "HQ", "city": "Hamburg", "country": "DE"}],
                         created_at=now - timedelta(days=400))
    orion_holdings = Account(name="Orion Holdings", domain="orionholdings.com", industry="Financial Services", tier="Enterprise", owner_id=marcus.id,
                             annual_revenue=9_000_000_000, employee_count=18000, created_at=now - timedelta(days=300))
    orion_am = Account(name="Orion Asset Management", domain="orion-am.com", industry="Financial Services", tier="Enterprise", owner_id=marcus.id,
                       lifecycle_stage="customer", annual_revenue=1_700_000_000, employee_count=2600, created_at=now - timedelta(days=500))
    # a planted near-duplicate for the dedup demo (similar name, different domain -> suggestion, not auto-merge)
    apex_dupe = Account(name="Apex Industrial Supplies", domain="apexindustrialsupply.com", industry="Industrial Distribution", tier="Mid-Market",
                        owner_id=sam.id, annual_revenue=None, custom_metadata={"source": "trade show list"}, created_at=now - timedelta(days=5))
    for a in (helios_group, renewables, orion_holdings, orion_am, apex_dupe):
        db.add(a)
    await db.flush()
    accounts["Helios Energy"].parent_id = helios_group.id
    renewables.parent_id = accounts["Helios Energy"].id
    accounts["Orion Financial"].parent_id = orion_holdings.id
    orion_am.parent_id = orion_holdings.id
    db.add(Contact(account_id=apex_dupe.id, first_name="Elena", last_name="Rostova", job_title="VP Procurement", buying_role="Evaluator"))
    ren_c = Contact(account_id=renewables.id, first_name="Jonas", last_name="Weber", email="jonas.weber@heliosrenewables.com", job_title="Head of Operations",
                    buying_role="Champion", timezone="Europe/Berlin", department="Operations", privacy_regime="GDPR", consent_email="granted", consent_basis="contract")
    oam_c = Contact(account_id=orion_am.id, first_name="Priscilla", last_name="Grant", email="p.grant@orion-am.com", job_title="COO",
                    buying_role="Decision Maker", timezone="America/New_York", department="Operations")
    db.add_all([ren_c, oam_c])

    # -- champion turnover at Bluepeak (churn signal) -----------------------------------------------
    blue = accounts["Bluepeak Health"]
    db.add(Contact(account_id=blue.id, first_name="Ian", last_name="Moss", email="ian.moss@bluepeakhealth.org", job_title="Director of Patient Experience",
                   buying_role="Champion", status="departed", departed_at=now - timedelta(days=40), department="Patient Experience"))

    # -- two-way email threads & meeting attendance (relationship strength) -----------------------
    def thread(contact, owner, deal, hours_to_reply, days_ago, subject):
        base = now - timedelta(days=days_ago)
        db.add(Activity(account_id=contact.account_id, contact_id=contact.id, user_id=owner.id, deal_id=deal.id if deal else None, activity_type="email",
                        direction="outbound", subject=subject, summary=f"{subject}: sent follow-up materials.", sentiment="neutral", occurred_at=base, source="email_sync"))
        if hours_to_reply is not None:
            db.add(Activity(account_id=contact.account_id, contact_id=contact.id, deal_id=deal.id if deal else None, activity_type="email", direction="inbound",
                            subject=f"Re: {subject}", summary=f"Re: {subject}: thanks, reviewing with the team.", sentiment="positive",
                            occurred_at=base + timedelta(hours=hours_to_reply), source="email_sync"))
    apex_deal, nw_deal = deals["Supply Chain Analytics Platform"], deals["Fleet Telemetry Rollout"]
    thread(contacts["elena.rostova@apexindustrial.com"], marcus, apex_deal, 2, 6, "Pricing proposal")
    thread(contacts["elena.rostova@apexindustrial.com"], marcus, apex_deal, 3, 3, "Data residency addendum")
    thread(contacts["james.cole@apexindustrial.com"], marcus, apex_deal, 30, 9, "Security questionnaire")
    thread(contacts["liam.chen@northwind.io"], priya, nw_deal, None, 21, "Latency benchmark results")
    thread(contacts["s.okafor@northwind.io"], priya, nw_deal, 60, 25, "Rollout timeline")
    db.add(Activity(account_id=nw_deal.account_id, contact_id=contacts["s.okafor@northwind.io"].id, user_id=priya.id, deal_id=nw_deal.id,
                    activity_type="meeting", attendance="no_show", subject="Executive alignment", summary="Executive alignment call: Samantha did not join.",
                    sentiment="negative", occurred_at=now - timedelta(days=17), duration_seconds=1800, agenda="1. Pilot results\n2. Timeline\n3. Next steps"))
    db.add(Activity(account_id=nw_deal.account_id, contact_id=contacts["liam.chen@northwind.io"].id, user_id=priya.id, deal_id=nw_deal.id,
                    activity_type="call", direction="outbound", disposition="left_voicemail", duration_seconds=45, summary="Called Liam re: pilot delay, left voicemail.",
                    sentiment="neutral", occurred_at=now - timedelta(days=16)))
    # slippage: close date pushed twice
    nw_deal.original_close_date, nw_deal.close_date_pushes = today + timedelta(days=5), 2

    # -- CPQ: approved quote + Order Form out for signature (Apex) -------------------------------------
    apex_quote = Quote(deal_id=apex_deal.id, quote_number=await cpq.next_quote_number(db), name="Supply Chain Analytics: 3-year", currency="USD",
                       term_months=12, payment_terms="NET30", created_by=marcus.id)
    db.add(apex_quote)
    await db.flush()
    await cpq.rebuild(db, apex_quote, [{"product_id": products["CIR-PLAT"].id, "quantity": 100, "discount_pct": 8},
                                       {"product_id": products["CIR-AI"].id, "quantity": 100, "discount_pct": 0},
                                       {"product_id": products["CIR-IMPL"].id, "quantity": 1, "discount_pct": 0}])
    await cpq.submit(db, apex_quote)
    order_form = await clm.generate(db, "order_form", accounts["Apex Industrial Supply"], apex_deal, apex_quote, marcus.id)
    await clm.send_for_signature(db, order_form, [{"name": "Elena Rostova", "email": "elena.rostova@apexindustrial.com", "party": "customer"},
                                                  {"name": "Marcus Vance", "email": "marcus@cirra.demo", "party": "company"}])
    # -- CPQ: quote needing manager + finance approval (Summit Foods) ------------------------------------
    summit_deal = deals["Trade Deduction Automation"]
    summit_quote = Quote(deal_id=summit_deal.id, quote_number=await cpq.next_quote_number(db), name="Deduction automation rollout", currency="USD",
                         term_months=24, payment_terms="NET60", created_by=diego.id)
    db.add(summit_quote)
    await db.flush()
    await cpq.rebuild(db, summit_quote, [{"product_id": products["DEDUCT"].id, "quantity": 120, "discount_pct": 18},
                                         {"product_id": products["CIR-PLAT"].id, "quantity": 40, "discount_pct": 12},
                                         {"product_id": products["CIR-IMPL"].id, "quantity": 1, "discount_pct": 0}])
    await cpq.submit(db, summit_quote)

    # -- Bluepeak: won deal signed through built-in e-signature -> contract -> onboarding ------------------
    blue_deal = deals["Patient Engagement Platform"]
    blue_quote = Quote(deal_id=blue_deal.id, quote_number=await cpq.next_quote_number(db), name="Patient Engagement Platform", currency="USD",
                       term_months=12, payment_terms="NET30", created_by=marcus.id)
    db.add(blue_quote)
    await db.flush()
    await cpq.rebuild(db, blue_quote, [{"product_id": products["CIR-PLAT"].id, "quantity": 400, "discount_pct": 5},
                                       {"product_id": products["CIR-SUP"].id, "quantity": 1, "discount_pct": 0},
                                       {"product_id": products["CIR-IMPL"].id, "quantity": 1, "discount_pct": 0}])
    blue_quote.status, blue_quote.approved_at = "approved", now - timedelta(days=15)
    blue_doc = await clm.generate(db, "order_form", blue, blue_deal, blue_quote, marcus.id)
    await clm.send_for_signature(db, blue_doc, [{"name": "Nora Lindqvist", "email": "nora.lindqvist@bluepeakhealth.org", "party": "customer"},
                                                {"name": "Marcus Vance", "email": "marcus@cirra.demo", "party": "company"}])
    for signer in sorted(blue_doc.signers, key=lambda s: s.sign_order):
        await clm.sign(db, signer, signer.signer_name, None, "203.0.113.24" if signer.signer_party == "customer" else "10.0.4.12", "Mozilla/5.0 (seed)")
    blue_contract = (await db.execute(select(Contract).where(Contract.quote_id == blue_quote.id))).scalars().first()
    blue_contract.start_date = today - timedelta(days=12)
    blue_contract.end_date = today - timedelta(days=12) + timedelta(days=364)
    blue_deal.amount = blue_quote.tcv
    project = await provision_onboarding(db, blue_deal)
    # it's been three weeks: kickoff done, technical setup running late
    project.status, project.kickoff_date = "in_progress", today - timedelta(days=18)
    for m in project.milestones:
        m.due_date = m.due_date - timedelta(days=20)
        if m.position == 0:
            m.status, m.completed_at = "done", now - timedelta(days=16)
        elif m.position == 1:
            m.status = "in_progress"
    for t in (await db.execute(select(Task).where(Task.milestone_id.in_([m.id for m in project.milestones])))).scalars().unique().all():
        ms = next(m for m in project.milestones if m.id == t.milestone_id)
        t.due_date, t.completed = ms.due_date, ms.status == "done"

    # -- contracts for renewals (pillar 7) ------------------------------------------------------------------
    ren_contract = Contract(account_id=renewables.id, contract_number="CT-2025-0007", name="Helios Renewables: Cirra Platform", currency="EUR",
                            start_date=today - timedelta(days=290), end_date=today + timedelta(days=75), acv=138000, tcv=138000, payment_terms="NET30",
                            terms={"term_months": 12, "lines": [{"sku": "CIR-PLAT", "quantity": 220, "net_unit_price": 52.27, "billing_type": "recurring"}]})
    oam_contract = Contract(account_id=orion_am.id, contract_number="CT-2025-0003", name="Orion Asset Management: Platform + Yield [S]", currency="USD",
                            start_date=today - timedelta(days=160), end_date=today + timedelta(days=205), acv=264000, tcv=528000, payment_terms="NET45",
                            terms={"term_months": 24, "lines": [{"sku": "CIR-PLAT", "quantity": 250, "net_unit_price": 58}, {"sku": "YIELD-S", "quantity": 120, "net_unit_price": 49}]})
    db.add_all([ren_contract, oam_contract])
    await db.flush()
    await clm.run_renewals(db, today)  # opens the Helios Renewables renewal (expires in 75 days)

    # -- support & adoption signals -------------------------------------------------------------------------
    db.add_all([
        SupportTicket(account_id=blue.id, subject="SSO login loop for clinicians", severity="high", status="open", opened_at=now - timedelta(days=4)),
        SupportTicket(account_id=blue.id, subject="Nightly EHR sync failing", severity="critical", status="open", opened_at=now - timedelta(days=2)),
        SupportTicket(account_id=renewables.id, subject="Dashboard export formatting", severity="low", status="open", opened_at=now - timedelta(days=9)),
        SupportTicket(account_id=orion_am.id, subject="API rate limit question", severity="medium", status="resolved", opened_at=now - timedelta(days=30),
                      resolved_at=now - timedelta(days=28)),
    ])
    for week in range(13):
        d = today - timedelta(days=7 * week)
        db.add(ProductUsage(account_id=blue.id, metric_date=d, active_users=150 + week * 9, licensed_users=400, feature_adoption=35 + week))
        db.add(ProductUsage(account_id=renewables.id, metric_date=d, active_users=178 - week, licensed_users=220, feature_adoption=72))
        db.add(ProductUsage(account_id=orion_am.id, metric_date=d, active_users=205, licensed_users=250, feature_adoption=64))

    # -- partners (pillar 9) --------------------------------------------------------------------------------------
    northstar = Partner(name="Northstar Partners", partner_type="distributor", tier="gold", domains=["northstar-partners.com"],
                        territories=["NA-East", "NA-Central"], commission_rate=12, referral_fee_rate=6)
    brightpath = Partner(name="BrightPath Agency", partner_type="agency", tier="silver", domains=["brightpath.agency"], territories=["EMEA"],
                         commission_rate=10, referral_fee_rate=5)
    keystone = Partner(name="Keystone Advisors", partner_type="referral", tier="registered", domains=["keystone-advisors.com"], territories=["NA-West"],
                       commission_rate=8, referral_fee_rate=7)
    db.add_all([northstar, brightpath, keystone])
    await db.flush()
    db.add(User(email=PARTNER_USER[0], full_name=PARTNER_USER[1], role="partner", partner_id=northstar.id, password_hash=hash_password(DEMO_PASSWORD)))
    await db.flush()
    nia = (await db.execute(select(User).where(User.email == PARTNER_USER[0]))).scalars().one()
    approved = await prm.submit(db, northstar, nia, {"company_name": "Vantage Freight", "domain": "vantagefreight.com", "contact_name": "Leo Marsh",
                                                     "contact_email": "leo.marsh@vantagefreight.com", "estimated_amount": 96000, "territory": "NA-East",
                                                     "product_interest": "Platform + Ambient AI", "notes": "Met at a logistics summit; evaluating CRM replacement in Q1."})
    await prm.decide(db, approved, marcus, True, "Approved: no conflicts.")
    await prm.submit(db, northstar, nia, {"company_name": "Lumina Retail", "domain": "lumina-retail.com", "contact_name": "Ava Chen",
                                          "estimated_amount": 90000, "territory": "NA-Central", "product_interest": "promo [Q] + Cirra"})
    await prm.submit(db, northstar, nia, {"company_name": "Cobalt Retail Group", "domain": "cobaltretail.com", "estimated_amount": 64000,
                                          "territory": "NA-East", "product_interest": "Promotions"})
    db.add(DealPartner(deal_id=deals["Revenue Intelligence Platform"].id, partner_id=brightpath.id, role="co_sell", split_pct=30))
    db.add(DealPartner(deal_id=blue_deal.id, partner_id=keystone.id, role="referral", split_pct=100))
    for title, category, tier, domains, lines in (
        ("Cirra partner overview", "deck", "registered", [], ["Positioning, ideal customer profile and the private-cloud deployment model.",
                                                                    "Use with new prospects in your territory."]),
        ("Battlecard: Cirra vs incumbent SaaS CRMs", "battlecard", "silver", [], ["Private-cloud data residency, ambient AI capture and stage-gate governance.",
                                                                                      "Handle objections about migration effort with the import engine."]),
        ("Channel price list (Gold and above)", "price_list", "gold", [], ["Cirra Platform: tiered per-user pricing; distributor margin per agreement."]),
        ("EMEA case study: utilities", "case_study", "registered", ["brightpath.agency"], ["Restricted to BrightPath Agency."]),
    ):
        att = storage.save(_pdf(title, lines), f"{title}.pdf", "application/pdf", uploaded_by=admin.id)
        db.add(att)
        await db.flush()
        db.add(Collateral(title=title, category=category, min_tier=tier, allowed_domains=domains, attachment_id=att.id, description=lines[0]))

    # -- a pending NDA for Northwind (e-signature demo) ------------------------------------------------------
    nda = await clm.generate(db, "nda", accounts["Northwind Logistics"], nw_deal, None, priya.id)
    await clm.send_for_signature(db, nda, [{"name": "Samantha Okafor", "email": "s.okafor@northwind.io", "party": "customer"},
                                           {"name": "Priya Raman", "email": "priya@cirra.demo", "party": "company"}])

    # -- SDR-sourced inbound lead in the mid-market pipeline ------------------------------------------------------
    inbound = pipelines["inbound"]
    lead_acc = Account(name="Quarry Labs", domain="quarrylabs.io", industry="Software", tier="SMB", owner_id=sam.id, custom_metadata={"source": "website demo request"})
    db.add(lead_acc)
    await db.flush()
    db.add(Contact(account_id=lead_acc.id, first_name="Tomas", last_name="Reyes", email="tomas@quarrylabs.io", job_title="Head of Sales", buying_role="Champion"))
    db.add(Deal(title="Quarry Labs: 25 seats", account_id=lead_acc.id, pipeline_id=inbound.id, stage_id=stages[("inbound", "Qualified")].id, owner_id=sam.id,
                amount=19500, target_close_date=today + timedelta(days=30), original_close_date=today + timedelta(days=30), source="inbound",
                risk_factors={}, ai_insights={}))
    # EUR deal in Helios Renewables expansion
    db.add(Deal(title="Helios Renewables: Ambient AI expansion", account_id=renewables.id, pipeline_id=pipelines["renewal"].id,
                stage_id=stages[("renewal", "Customer Review")].id, owner_id=diego.id, amount=26400, currency="EUR", deal_type="upsell", source="direct",
                target_close_date=today + timedelta(days=45), original_close_date=today + timedelta(days=45), risk_factors={}, ai_insights={}))
    # an overdue delegated task chain for the SLA engine
    t1 = Task(title="Collect InfoSec evidence pack from James Cole", due_date=today - timedelta(days=4), account_id=accounts["Apex Industrial Supply"].id,
              deal_id=apex_deal.id, owner_id=marcus.id, assignee_id=priya.id, priority="high", source="manual")
    db.add(t1)
    await db.flush()
    db.add(Task(title="Submit security questionnaire answers", due_date=today + timedelta(days=3), account_id=accounts["Apex Industrial Supply"].id,
                deal_id=apex_deal.id, owner_id=marcus.id, assignee_id=marcus.id, depends_on_id=t1.id, source="manual"))

    # an aged receivable at Orion Asset Management puts it on credit hold after the ERP sync
    from app.models import Invoice
    db.add(Invoice(account_id=orion_am.id, invoice_number="INV-2025-0412", issue_date=today - timedelta(days=150), due_date=today - timedelta(days=120),
                   amount=66000, balance=66000, status="open", currency="USD"))
    await db.flush()
    await erp.sync_inbound(db)  # demo ERP: customer master + invoices + credit holds


# public demo intake keys (hashed at rest; the raw values are printed so the hosted form and webhook can be tried)
DEMO_FORM_KEY = "cf_demo_webform_cirra"
DEMO_WEBHOOK_KEY = "ck_demo_webhook_cirra"
COUNTRY_BY_CODE = {"US": "United States", "NL": "Netherlands", "DE": "Germany", "GB": "United Kingdom"}

# (first, last, email, title, company, domain, industry, employees, revenue, country, source, campaign, events[(type, days_ago, detail)], status)
LEADS = [
    ("Jonas", "Becker", "jonas.becker@kraftwerk-tools-demo.de", "VP Operations", "Kraftwerk Tools GmbH", "kraftwerk-tools-demo.de", "Manufacturing", 850, 120_000_000,
     "Germany", "web_form", "Q4 ERP webinar", [("form_submit", 2, "Demo request"), ("pricing_page_visit", 1, "/pricing"), ("webinar_attended", 6, "ERP-native CRM")], None),
    ("Liam", "O'Connor", "liam.oconnor@shamrock-foods-demo.ie", "Head of Sales Ops", "Shamrock Foods", "shamrock-foods-demo.ie", "Food & Beverage", 400,
     60_000_000, "Ireland", "campaign", "EMEA nurture", [("email_click", 3, "Forecasting e-book"), ("content_download", 3, "Forecasting e-book")], None),
    ("Aisha", "Khan", "aisha.khan@meridian-health-demo.com", "COO", "Meridian Health Partners", "meridian-health-demo.com", "Healthcare", 3200,
     540_000_000, "United States", "trade_show", "SaaStr 2026", [("trade_show_scan", 12, "Booth scan"), ("meeting_booked", 4, "Discovery call")], "sql"),
    ("Ravi", "Menon", "ravi.menon@lotus-textiles-demo.in", "Director IT", "Lotus Textiles", "lotus-textiles-demo.in", "Manufacturing", 1500, 90_000_000,
     "India", "partner", "Northstar referral", [("form_submit", 20, "Partner referral form")], None),
    ("Chloe", "Martin", "chloe.martin@gmail.com", "Consultant", "", None, None, None, None, "France", "web_form", "Website",
     [("form_submit", 40, "Newsletter signup")], "disqualified"),
    ("Ben", "Carter", "ben.carter@granite-build-demo.com", "CFO", "Granite Build Co", "granite-build-demo.com", "Construction", 700, 150_000_000,
     "United States", "outbound", "Q3 outbound: construction", [("email_open", 9, "Sequence step 2"), ("email_click", 8, "Case study")], None),
    ("Sofia", "Rossi", "sofia.rossi@alpina-energy-demo.it", "Head of Commercial", "Alpina Energy", "alpina-energy-demo.it", "Energy", 2100, 700_000_000,
     "Italy", "web_form", "Q4 ERP webinar", [("form_submit", 1, "Contact sales"), ("pricing_page_visit", 1, "/pricing/enterprise"),
                                             ("webinar_attended", 6, "ERP-native CRM")], None),
]


async def _lead_to_order(db, users, accounts, stages, pipelines, products, now, today) -> None:
    """Lead-to-order demo: intake keys, routing, leads with engagement, regional / customer price books, a solution sale taken
    from lead to an ERP-acknowledged order, and a second one waiting in the approval chain."""
    import hashlib

    from app.services import enrichment, leads as lead_svc, orders, pipeline_service
    from app.services.clm import generate, send_for_signature, sign

    marcus, priya, diego, sam = (users[e] for e in ("marcus@cirra.demo", "priya@cirra.demo", "diego@cirra.demo", "sam@cirra.demo"))
    for name, account in accounts.items():  # geography drives regional price books and routing
        code = next((l.get("country") for l in (FIRMO.get(name, {}).get("locations") or []) if l.get("type") == "HQ"), "US")
        account.country = COUNTRY_BY_CODE.get(code, code)
        account.region = enrichment.region_for(account.country)

    db.add_all([
        IntakeKey(name="Website demo form", kind="web_form", key_hash=hashlib.sha256(DEMO_FORM_KEY.encode()).hexdigest(), key_prefix=DEMO_FORM_KEY[:10],
                  source="web_form", campaign="Website", created_by=users["admin@cirra.demo"].id),
        IntakeKey(name="Marketing automation webhook", kind="webhook", key_hash=hashlib.sha256(DEMO_WEBHOOK_KEY.encode()).hexdigest(),
                  key_prefix=DEMO_WEBHOOK_KEY[:10], source="campaign", created_by=users["admin@cirra.demo"].id),
        AssignmentRule(name="Named accounts stay with the account owner", priority=10, method="account_owner", criteria={"existing_account": True}),
        AssignmentRule(name="EMEA inbound round robin", priority=20, method="round_robin", criteria={"regions": ["EMEA"]},
                       assignee_ids=[str(priya.id), str(diego.id)]),
        AssignmentRule(name="Enterprise (1,000+ employees) to Marcus", priority=30, method="specific", criteria={"min_employees": 1000},
                       assignee_ids=[str(marcus.id)]),
        PriceBook(name="EMEA 2026 regional", kind="regional", region="EMEA", valid_from=today - timedelta(days=90),
                  entries=[PriceBookEntry(product_id=products["CIR-PLAT"].id, currency="EUR", tiers=[{"min_qty": 1, "unit_price": 56}, {"min_qty": 100, "unit_price": 50}])]),
        PriceBook(name="Apex framework agreement", kind="customer", account_id=accounts["Apex Industrial Supply"].id,
                  entries=[PriceBookEntry(product_id=products["CIR-PLAT"].id, currency="USD", tiers=[{"min_qty": 1, "unit_price": 58}])]),
    ])
    await db.flush()

    cfg = await lead_svc.app_settings.get(db, "lead_scoring")
    for first, last, email, title, company, domain, industry, emp, rev, country, source, campaign, events, status in LEADS:
        lead, _ = await lead_svc.capture(db, {"first_name": first, "last_name": last, "email": email, "job_title": title, "company_name": company,
                                              "domain": domain, "industry": industry, "employee_count": emp, "annual_revenue": rev, "country": country,
                                              "consent": "granted" if source == "web_form" else None}, source=source, campaign=campaign)
        lead.created_at = now - timedelta(days=max(d for _, d, _ in events))
        for kind, days_ago, detail in events:
            await lead_svc.add_event(db, lead, kind, detail, source=source, occurred_at=now - timedelta(days=days_ago, hours=2), cfg=cfg)
        await lead_svc.rescore(db, lead, cfg)
        if status == "sql":
            lead.qualification_framework, lead.status = "meddpicc", "sql"
            lead.qualification = {k: {"met": True, "note": n} for k, n in (
                ("metrics", "Cut claim-to-cash by 20 days"), ("economic_buyer", "COO owns budget"), ("decision_criteria", "HL7 + ERP integration"),
                ("decision_process", "Security review then board"), ("identify_pain", "Manual renewals"), ("champion", "RevOps lead"))}
        if status == "disqualified":
            lead.status, lead.disqualified_reason, lead.disqualify_note = "disqualified", "not_icp", "Individual consultant, free-mail address"

    # ---- a solution sale from lead to ERP sales order -------------------------------------------------------------
    solution = pipelines["solution"]
    lead, _ = await lead_svc.capture(db, {"first_name": "Mei", "last_name": "Tanaka", "email": "mei.tanaka@harborline-demo.com", "job_title": "CIO",
                                          "company_name": "Harborline Freight", "domain": "harborline-demo.com", "industry": "Transportation",
                                          "employee_count": 2400, "annual_revenue": 800_000_000, "country": "United States", "consent": "granted"},
                                     source="trade_show", campaign="SaaStr 2026", owner_id=sam.id)
    lead.created_at = now - timedelta(days=45)
    for kind, d in (("trade_show_scan", 45), ("meeting_booked", 40)):
        await lead_svc.add_event(db, lead, kind, "SaaStr booth, then discovery call", source="trade_show", occurred_at=now - timedelta(days=d), cfg=cfg)
    lead.qualification = {k: {"met": True} for k in ("budget", "authority", "need", "timeline")}
    lead.status = "sql"
    out = await lead_svc.convert(db, lead, sam, deal_title="Harborline Freight: Revenue platform", amount=108_000, pipeline_id=solution.id,
                                 owner_id=priya.id, buying_role="Economic Buyer", target_close_date=today)
    deal = await db.get(Deal, out["deal_id"])
    acc = await db.get(Account, out["account_id"])
    for first, last, role, title in (("Kenji", "Mori", "Champion", "VP Revenue Operations"), ("Ava", "Brooks", "Evaluator", "Enterprise Architect"),
                                     ("Lucas", "Grant", "Legal Counsel", "Associate General Counsel")):
        db.add(Contact(account_id=acc.id, first_name=first, last_name=last, email=f"{first.lower()}.{last.lower()}@harborline-demo.com",
                       job_title=title, buying_role=role))
    for days, kind, text_ in ((32, "meeting", "Solution design workshop: pain is manual quote-to-cash and a forecast bottleneck across 3 regions."),
                              (21, "meeting", "PoC scope agreed: SAP S/4 integration and Ambient AI on 40 reps; architecture review passed."),
                              (12, "call", "Business case signed off by the CIO: 14-month payback, budget approved for FY27.")):
        db.add(Activity(account_id=acc.id, deal_id=deal.id, user_id=priya.id, activity_type=kind, sentiment="positive", summary=text_,
                        occurred_at=now - timedelta(days=days), attendance="attended" if kind == "meeting" else None))
    await db.flush()
    for name in ("Solution Design / Demo", "Technical Evaluation / PoC", "Business Case Validation"):
        await pipeline_service.change_stage(db, deal, stages[("solution", name)], priya)

    quote = Quote(deal_id=deal.id, quote_number=await cpq.next_quote_number(db), name="Harborline Freight: 120 users", currency="USD", term_months=12,
                  payment_terms="NET30", billing_frequency="quarterly", created_by=priya.id, status="draft", is_primary=True)
    db.add(quote)
    await db.flush()
    await cpq.rebuild(db, quote, [{"product_id": products["CIR-GROWTH"].id, "quantity": 120, "discount_pct": 0},
                                  {"product_id": products["CIR-IMPL"].id, "quantity": 1, "discount_pct": 0}])
    await cpq.submit(db, quote)
    await pipeline_service.change_stage(db, deal, stages[("solution", "Negotiation & Legal")], priya)
    doc = await generate(db, "order_form", acc, deal, quote, priya.id)
    doc = await send_for_signature(db, doc, [{"name": "Mei Tanaka", "email": "mei.tanaka@harborline-demo.com", "party": "customer"},
                                             {"name": "Marcus Vance", "email": "marcus@cirra.demo", "party": "company"}])
    for req in sorted(doc.signers, key=lambda r: r.sign_order):
        await sign(db, req, req.signer_name, None, "203.0.113.10", "seed")
    hq = {"line1": "400 Pier 91 Way", "city": "Seattle", "region": "WA", "postal_code": "98119", "country": "US"}
    deal.po_number, deal.bill_to, deal.ship_to, deal.incoterms = "PO-HF-778812", hq, {**hq, "attention": "Revenue Operations"}, "DAP"
    deal.requested_delivery_date = today + timedelta(days=14)
    await pipeline_service.change_stage(db, deal, stages[("solution", "Closed-Won")], priya,
                                        win_debrief="Won on ERP-native quote-to-cash and the Ambient AI PoC; SAP add-on lost on integration effort.")
    order = (await db.execute(select(orders.Order).where(orders.Order.deal_id == deal.id))).scalars().first()
    if order:
        await orders.push(db, order)

    # ---- a second solution sale waiting in the approval chain ------------------------------------------------------------
    lead, _ = await lead_svc.capture(db, {"first_name": "Omar", "last_name": "Haddad", "email": "omar.haddad@crescent-retail-demo.ae",
                                          "job_title": "Chief Digital Officer", "company_name": "Crescent Retail", "domain": "crescent-retail-demo.ae",
                                          "industry": "Retail", "employee_count": 5200, "country": "United Arab Emirates", "consent": "granted"},
                                     source="partner", campaign="Northstar referral", owner_id=diego.id)
    lead.qualification = {k: {"met": True} for k in ("budget", "authority", "need")}
    lead.status = "sql"
    out = await lead_svc.convert(db, lead, diego, deal_title="Crescent Retail: CRM + AI", amount=240_000, pipeline_id=solution.id, owner_id=diego.id,
                                 buying_role="Champion", target_close_date=today + timedelta(days=40))
    deal2 = await db.get(Deal, out["deal_id"])
    q2 = Quote(deal_id=deal2.id, quote_number=await cpq.next_quote_number(db), name="Crescent Retail: 300 users, 3 years", currency="USD",
               term_months=36, payment_terms="NET60", billing_frequency="annual", created_by=diego.id, status="draft", is_primary=True,
               custom_terms="Liability cap raised to 2x annual fees; 60-day termination for convenience in year 1")
    db.add(q2)
    await db.flush()
    await cpq.rebuild(db, q2, [{"product_id": products["CIR-PLAT"].id, "quantity": 300, "discount_pct": 28},
                               {"product_id": products["CIR-AI"].id, "quantity": 300, "discount_pct": 10}])
    await cpq.submit(db, q2)
    await db.flush()
    print(f"Lead intake: hosted form /forms/{DEMO_FORM_KEY}  |  webhook key {DEMO_WEBHOOK_KEY}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimal", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    asyncio.run(seed(minimal=args.minimal, reset=args.reset))
