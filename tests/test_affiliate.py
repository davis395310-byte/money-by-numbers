"""Tests for affiliate click recording."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.business import AffiliateClick, build_click_record


def _full_fields(**overrides):
    fields = dict(
        sportsbook="Example Sportsbook",
        cta_location="game_card",
        timestamp=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        destination="https://example.com/affiliate/offer?ref=mbn",
        line="KC -3.5",
        odds="-110",
        model_probability=0.62,
        market_probability=0.55,
        edge=0.07,
        model_version="v0.1.0",
    )
    fields.update(overrides)
    return fields


def test_build_click_record_raises_when_destination_missing():
    fields = _full_fields()
    del fields["destination"]
    with pytest.raises(ValueError) as excinfo:
        build_click_record(**fields)
    assert "destination" in str(excinfo.value)


def test_build_click_record_lists_all_missing_required_fields():
    with pytest.raises(ValueError) as excinfo:
        build_click_record(sportsbook="Example Sportsbook")
    message = str(excinfo.value)
    assert "cta_location" in message
    assert "timestamp" in message
    assert "destination" in message


def test_build_click_record_accepts_fully_specified_record():
    record = build_click_record(**_full_fields())
    assert record["sportsbook"] == "Example Sportsbook"
    assert record["cta_location"] == "game_card"
    assert record["destination"] == "https://example.com/affiliate/offer?ref=mbn"
    assert record["click_id"]  # auto-generated
    # Never invents a destination: the returned value is exactly what the
    # caller supplied.
    assert record["destination"] == _full_fields()["destination"]


def test_affiliate_click_model_requires_timestamp():
    fields = _full_fields()
    del fields["timestamp"]
    with pytest.raises(ValidationError):
        AffiliateClick(**fields)
