from app.services.lpr_zone_shadow import evaluate_zone_shadow_decision


def test_zone_shadow_would_suppress_unknown_moving_plate() -> None:
    decision = evaluate_zone_shadow_decision(
        zone_status="moving",
        known_vehicle=False,
        visitor=False,
    )

    assert decision.shadow_decision == "would_suppress"
    assert decision.would_suppress


def test_zone_shadow_allows_entering_unknown_plate() -> None:
    decision = evaluate_zone_shadow_decision(
        zone_status="enter",
        known_vehicle=False,
        visitor=False,
    )

    assert decision.shadow_decision == "would_allow"
    assert not decision.would_suppress


def test_zone_shadow_allows_known_or_visitor_moving_plate() -> None:
    known_decision = evaluate_zone_shadow_decision(
        zone_status="moving",
        known_vehicle=True,
        visitor=False,
    )
    visitor_decision = evaluate_zone_shadow_decision(
        zone_status="moving",
        known_vehicle=False,
        visitor=True,
    )

    assert known_decision.shadow_decision == "would_allow"
    assert not known_decision.would_suppress
    assert visitor_decision.shadow_decision == "would_allow"
    assert not visitor_decision.would_suppress


def test_zone_shadow_allows_missing_status() -> None:
    decision = evaluate_zone_shadow_decision(
        zone_status=None,
        known_vehicle=False,
        visitor=False,
    )

    assert decision.shadow_decision == "would_allow"
    assert not decision.would_suppress


def test_zone_shadow_reviews_unhandled_status() -> None:
    decision = evaluate_zone_shadow_decision(
        zone_status="loitering",
        known_vehicle=False,
        visitor=False,
    )

    assert decision.shadow_decision == "would_review"
    assert not decision.would_suppress
