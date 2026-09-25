from app.services import scoring


def test_recency_decays_five_points_per_day():
    assert scoring.recency_score(0) == 100
    assert scoring.recency_score(4) == 80
    assert scoring.recency_score(30) == 0


def test_sentiment_uses_last_five_notes():
    assert scoring.sentiment_score([]) == 50
    assert scoring.sentiment_score(["positive", "negative"]) == 50
    assert scoring.sentiment_score(["positive"] * 5 + ["negative"] * 10) == 100


def test_velocity_penalises_stalled_deals():
    assert scoring.velocity_score([3, 10]) == 100
    assert scoring.velocity_score([3, 22]) == 30


def test_health_formula():
    assert scoring.health_score(100, 100, 100) == 100
    assert scoring.health_score(80, 50, 30) == round(0.4 * 80 + 0.35 * 50 + 0.25 * 30)


def test_deal_risk_components():
    assert scoring.deal_risk(2, "positive", True)[0] == 0
    assert scoring.deal_risk(20, "positive", True)[0] == 30
    assert scoring.deal_risk(2, "negative", True)[0] == 30
    assert scoring.deal_risk(2, "neutral", False)[0] == 40
    score, factors = scoring.deal_risk(None, "negative", False)
    assert score == 100 and factors["stale"] and factors["no_champion"]


def test_weighted_value_is_risk_adjusted():
    # spec sample: $75,000 at 75% with risk 0 -> $56,250
    assert scoring.weighted_value(75000, 75, 0) == 56250
    assert scoring.weighted_value(100000, 50, 100) == 25000
