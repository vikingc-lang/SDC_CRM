"""Default pipelines for the four revenue streams, each with its own stage gates (pillar 3)."""

R = dict  # rule shorthand

SIGNED = R(type="any_of", label="Executed MSA / Order Form or PO posted",
           rules=[R(type="signed_document", doc_type="order_form"), R(type="keyword", pattern=r"\b(msa|po|purchase order|countersigned|executed)\b")])
LOSS = R(type="loss_reason", label="Loss reason (taxonomy) and rep debrief recorded", min_debrief=15)
PROPOSAL = [R(type="field_present", field="target_close_date", label="Target close date set"),
            R(type="amount_approved", label="Approved amount (approved quote)")]

PIPELINES = [
    {
        "name": "Enterprise Direct Sales", "kind": "direct", "is_default": True,
        "description": "Named-account enterprise motion with InfoSec review.",
        "stages": [
            ("Discovery", 10, [R(type="domain_verified", label="Verified business domain"), R(type="min_contacts", min=1, label="At least 1 contact logged")]),
            ("Pain Fit", 25, [R(type="pain_identified", label="Pain identified"),
                              R(type="role_mapped", roles=["Economic Buyer", "Champion"], label="Economic Buyer or Champion mapped")]),
            ("Solution Demo", 50, [R(type="activity_logged", activity_types=["meeting"], keywords=r"demo|walkthrough|poc|pilot", label="Demo completed (meeting logged)"),
                                   R(type="keyword", pattern=r"criteria|requirements|scorecard|success plan", label="Evaluation criteria agreed in writing")]),
            ("Proposal/InfoSec", 75, [*PROPOSAL, R(type="keyword", pattern=r"security|infosec|soc ?2|legal|questionnaire|redline", label="InfoSec / legal review open")]),
            ("Closed-Won", 100, [SIGNED]),
            ("Closed-Lost", 0, [LOSS]),
        ],
    },
    {
        "name": "Inbound Mid-Market", "kind": "inbound", "is_default": False,
        "description": "High-velocity inbound funnel.",
        "stages": [
            ("Lead", 5, []),
            ("Qualified", 20, [R(type="min_contacts", min=1, label="Contact identified"), R(type="field_present", field="amount", label="Budget estimate captured")]),
            ("Demo Completed", 45, [R(type="activity_logged", activity_types=["meeting", "call"], label="Demo activity logged")]),
            ("Proposal Sent", 70, PROPOSAL),
            ("Closed-Won", 100, [SIGNED]),
            ("Closed-Lost", 0, [LOSS]),
        ],
    },
    {
        "name": "Renewals & Upsells", "kind": "renewal", "is_default": False,
        "description": "Auto-generated 120 days before contract expiry.",
        "stages": [
            ("Renewal Identified", 60, []),
            ("Customer Review", 70, [R(type="activity_logged", activity_types=["meeting", "call", "email"], since_stage=False, label="Customer touchpoint logged")]),
            ("Proposal Sent", 85, PROPOSAL),
            ("Negotiation", 90, []),
            ("Closed-Won", 100, [SIGNED]),
            ("Closed-Lost", 0, [LOSS]),
        ],
    },
    {
        "name": "Partner Channels", "kind": "partner", "is_default": False,
        "description": "Registered and co-sell opportunities from distributors and agencies.",
        "stages": [
            ("Registered", 10, []),
            ("Qualified", 25, [R(type="min_contacts", min=1, label="Customer contact identified")]),
            ("Joint Demo", 50, [R(type="activity_logged", activity_types=["meeting"], label="Joint demo meeting logged")]),
            ("Proposal Sent", 75, PROPOSAL),
            ("Closed-Won", 100, [SIGNED]),
            ("Closed-Lost", 0, [LOSS]),
        ],
    },
]
