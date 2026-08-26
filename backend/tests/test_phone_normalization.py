from app.premium_numbers.phone_normalization import format_phone


def test_us_formats_keep_existing_canonical_shape() -> None:
    assert format_phone("(214) 555-1212")[:2] == ("12145551212", "(214) 555-1212")
    assert format_phone("1-214-555-1212")[:2] == ("12145551212", "(214) 555-1212")


def test_international_fallback_returns_e164() -> None:
    assert format_phone("+44 20 7946 0958")[:2] == ("+442079460958", "+442079460958")
    assert format_phone("442079460958")[:2] == ("+442079460958", "+442079460958")


def test_invalid_phone_is_still_rejected() -> None:
    assert format_phone("12345") == ("", "", "")
