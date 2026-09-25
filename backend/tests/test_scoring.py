from app.services import scoring


def test_recency_decays_five_points_per_day():
    assert scoring.recency_score(0) == 100
    assert scoring.recency_score(4) == 80
    assert scoring.recency_score(30) == 0


def test_sentiment_uses_last_five_notes():
    assert scoring.sentiment_score([]) == 50
    assert scoring.sentiment_score(["positive", "negative"]) == 50
    assert scoring.sentiment_score(["positive"] * 5 + ["negative"] * 10) == 100


def test_sentiment_drift_penalises_only_falling_trend():
    falling = ["negative"] * 5 + ["positive"] * 5  # newest first
    rising = ["positive"] * 5 + ["negative"] * 5
    assert scoring.sentiment_drift(falling) == -100
    assert scoring.drifted_sentiment(falling) == 0  # 0 + (-100 / 2) floored at 0
    assert scoring.drifted_sentiment(rising) == 100


def test_velocity_penalises_stalled_deals():
    assert scoring.velocity_score([3, 10]) == 100
    assert scoring.velocity_score([3, 22]) == 30


def test_support_and_milestone_components():
    assert scoring.support_score([]) == 100
    assert scoring.support_score(["critical", "high"]) == 45
    assert scoring.milestone_score(0) == 100
    assert scoring.milestone_score(2) == 60


def test_health_formula():
    assert scoring.health_score(100, 100, 100, 100, 100) == 100
    expected = round(0.30 * 80 + 0.25 * 50 + 0.15 * 30 + 0.15 * 45 + 0.15 * 60)
    assert scoring.health_score(80, 50, 30, 45, 60) == expected


def test_deal_risk_components():
    assert scoring.deal_risk(2, "positive", True)[0] == 0
    assert scoring.deal_risk(20, "positive", True)[0] == 30
    assert scoring.deal_risk(2, "negative", True)[0] == 30
    assert scoring.deal_risk(2, "neutral", False)[0] == 40
    score, factors = scoring.deal_risk(None, "negative", False)
    assert score == 100 and factors["stale"] and factors["no_champion"]


def test_weighted_value_is_risk_adjusted():
    assert scoring.weighted_value(75000, 75, 0) == 56250  # spec sample
    assert scoring.weighted_value(100000, 50, 100) == 25000


def test_relationship_strength_index():
    fast, f = scoring.relationship_strength(2, 5, 3, 0)
    assert fast == 100 and f["latency_score"] == 100
    slow, _ = scoring.relationship_strength(72, 0, 1, 1)
    assert slow == round((0.4 * 0 + 0.3 * 0 + 0.3 * 50) / 1.0)
    no_email, _ = scoring.relationship_strength(None, 2, 0, 0)
    assert no_email == 40  # only inbound frequency available
