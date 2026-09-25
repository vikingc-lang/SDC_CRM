"""Seed relate [R] with demo data.

    python -m app.seed            # full demo dataset (idempotent)
    python -m app.seed --minimal  # specification minimum: 3 accounts, 6 contacts, 3 active deals
    python -m app.seed --reset    # wipe CRM data first

Pipeline: the four open stage gates (Discovery, Pain Fit, Solution Demo,
Proposal/InfoSec) plus the two terminal states (Closed-Won, Closed-Lost).
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import Account, Activity, Contact, Deal, DealStageHistory, Pipeline, PipelineStage, Task, User
from app.services import embeddings, scoring

DEMO_PASSWORD = "relate123"

USERS = [
    ("admin@relate.demo", "Avery Admin", "super_admin"),
    ("marcus@relate.demo", "Marcus Vance", "sales_manager"),
    ("priya@relate.demo", "Priya Raman", "sales_rep"),
    ("diego@relate.demo", "Diego Alvarez", "sales_rep"),
    ("viewer@relate.demo", "Robin Viewer", "read_only"),
]

STAGES = [
    ("Discovery", 10, False, False),
    ("Pain Fit", 25, False, False),
    ("Solution Demo", 50, False, False),
    ("Proposal/InfoSec", 75, False, False),
    ("Closed-Won", 100, True, False),
    ("Closed-Lost", 0, False, True),
]

# Each account: (name, domain, industry, tier, owner_email, contacts, deals, activities, tasks)
# contacts: (first, last, email, title, role)
# deals: (title, amount, stage, days_in_stage, close_in_days, loss_reason)
# activities: (days_ago, type, sentiment, summary)
# tasks: (title, due_in_days)
ACCOUNTS = [
    (
        "Apex Industrial Supply", "apexindustrial.com", "Industrial Distribution", "Mid-Market", "marcus@relate.demo",
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
        "Northwind Logistics", "northwind.io", "Logistics", "Enterprise", "priya@relate.demo",
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
        "Helios Energy", "heliosenergy.com", "Energy & Utilities", "Enterprise", "diego@relate.demo",
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
        "Bluepeak Health", "bluepeakhealth.org", "Healthcare", "Enterprise", "marcus@relate.demo",
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
        "Cobalt Retail Group", "cobaltretail.com", "Retail", "Mid-Market", "priya@relate.demo",
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
        "Summit Foods Co.", "summitfoods.com", "Food & Beverage", "Mid-Market", "diego@relate.demo",
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
        "Vertex Manufacturing", "vertexmfg.com", "Manufacturing", "SMB", "priya@relate.demo",
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
        "Orion Financial", "orionfinancial.com", "Financial Services", "Enterprise", "marcus@relate.demo",
        [
            ("Hannah", "Weiss", "hannah.weiss@orionfinancial.com", "SVP Revenue Operations", "Champion"),
            ("Kevin", "Doyle", "kevin.doyle@orionfinancial.com", "CISO", "Blocker"),
        ],
        [("Revenue Intelligence Platform", 410000, "Solution Demo", 11, 75, None)],
        [
            (5, "meeting", "neutral", "Technical deep dive. Kevin Doyle pushed back on cloud hosting; relate's private-cloud Docker deployment addressed most concerns."),
            (11, "meeting", "positive", "Executive demo with Hannah's team. Strong excitement about ambient note capture and pipeline risk scoring."),
        ],
        [("Send private-cloud architecture doc to Kevin Doyle", 3)],
    ),
]


async def seed(minimal: bool = False, reset: bool = False) -> None:
    async with SessionLocal() as db:
        if reset:
            await db.execute(text("TRUNCATE deal_stage_history, tasks, activities, deals, contacts, accounts, pipeline_stages, pipelines, users CASCADE"))
            await db.commit()

        if (await db.execute(select(func.count()).select_from(User))).scalar_one():
            print("Database already seeded. Use --reset to start over.")
            return

        users = {}
        for email, name, role in USERS:
            u = User(email=email, full_name=name, role=role, password_hash=hash_password(DEMO_PASSWORD))
            db.add(u)
            users[email] = u
        pipeline = Pipeline(name="Enterprise Sales", is_default=True)
        db.add(pipeline)
        await db.flush()
        stages = {}
        for order, (name, prob, won, lost) in enumerate(STAGES, start=1):
            s = PipelineStage(pipeline_id=pipeline.id, name=name, stage_order=order, default_probability=prob, is_closed_won=won, is_closed_lost=lost)
            db.add(s)
            stages[name] = s
        await db.flush()

        now = datetime.now(timezone.utc)
        today = date.today()
        dataset = ACCOUNTS[:3] if minimal else ACCOUNTS
        for name, domain, industry, tier, owner_email, contacts, deals, activities, tasks in dataset:
            owner = users[owner_email]
            account = Account(name=name, domain=domain, industry=industry, tier=tier, owner_id=owner.id, custom_metadata={},
                              created_at=now - timedelta(days=60))
            db.add(account)
            await db.flush()
            contact_rows = []
            for first, last, email, title, role in contacts:
                c = Contact(account_id=account.id, first_name=first, last_name=last, email=email, job_title=title, buying_role=role,
                            phone=None)
                db.add(c)
                contact_rows.append(c)
            await db.flush()
            key_contact = next((c for c in contact_rows if c.buying_role in ("Champion", "Decision Maker")), contact_rows[0])

            deal_rows = []
            for title, amount, stage_name, days_in_stage, close_in, loss_reason in deals:
                stage = stages[stage_name]
                entered = now - timedelta(days=days_in_stage)
                d = Deal(
                    title=title, account_id=account.id, pipeline_id=pipeline.id, stage_id=stage.id, owner_id=owner.id,
                    amount=amount, target_close_date=today + timedelta(days=close_in), primary_contact_id=key_contact.id,
                    stage_entered_at=entered, created_at=entered - timedelta(days=10 * stage.stage_order),
                    closed_at=entered if stage.is_closed_won or stage.is_closed_lost else None,
                    loss_reason=loss_reason, risk_factors={}, ai_insights={},
                )
                db.add(d)
                deal_rows.append(d)
                await db.flush()
                # reconstruct a plausible stage-gate audit trail
                prev = None
                path = [s for s in STAGES if not s[2] and not s[3] and stages[s[0]].stage_order <= stage.stage_order]
                if stage.is_closed_won or stage.is_closed_lost:
                    path = [s for s in STAGES[:4] if stages[s[0]].stage_order <= (4 if stage.is_closed_won else 2)] + [(stage_name,)]
                for i, step in enumerate(path):
                    to = stages[step[0]]
                    db.add(DealStageHistory(deal_id=d.id, from_stage_id=prev.id if prev else None, to_stage_id=to.id, changed_by=owner.id,
                                            changed_at=entered - timedelta(days=7 * (len(path) - 1 - i))))
                    prev = to

            main_deal = deal_rows[0] if deal_rows else None
            for days_ago, kind, sentiment, summary in activities:
                a = Activity(account_id=account.id, deal_id=main_deal.id if main_deal else None, contact_id=key_contact.id, user_id=owner.id,
                             activity_type=kind, sentiment=sentiment, summary=summary, occurred_at=now - timedelta(days=days_ago, hours=3))
                a.embedding = await embeddings.embed(summary)
                db.add(a)
            for title, due_in in tasks:
                db.add(Task(title=title, due_date=today + timedelta(days=due_in), account_id=account.id,
                            deal_id=main_deal.id if main_deal else None, owner_id=owner.id, source="ai"))
            await db.flush()

        # AI insights that the stage triggers would have produced
        for d in (await db.execute(select(Deal))).scalars().unique().all():
            if d.title == "Supply Chain Analytics Platform":
                d.ai_insights = {"competitors": ["Salesforce"], "pain_points": ["Forecasting happens in spreadsheets and stock-outs cost ~$400k a year."]}
            if d.title == "Fleet Telemetry Rollout":
                d.ai_insights = {"competitors": ["HubSpot"], "pain_points": ["No visibility into idle time across 1,200 trucks."]}
            if d.title == "Grid Asset Intelligence Suite":
                d.ai_insights = {"pain_points": ["Manual asset inspections are slow and error-prone."]}

        await db.flush()
        for account in (await db.execute(select(Account))).scalars().unique().all():
            await scoring.rescore_account(db, account.id)
        await db.commit()
        print(f"Seeded {len(dataset)} accounts. Log in with marcus@relate.demo / {DEMO_PASSWORD}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimal", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    asyncio.run(seed(minimal=args.minimal, reset=args.reset))
