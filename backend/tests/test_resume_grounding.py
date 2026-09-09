from app.services.resume_grounding_service import check_grounding


def test_novel_figures_are_cautions_but_years_and_existing_reordered_facts_are_not():
    source = "Built APIs at Acme Corp in 2019. Led teams through 2024. Used 8 GB."
    warning = check_grounding(source, source, "Saved $2M and cut latency 40% at Newco Corp in 2025.")
    assert warning["novel_numbers"] == ["$2M", "40%"]
    assert "Newco Corp" in warning["novel_organisations"]
    assert warning["review_required"]
    reordered = "Used 8 GB. Led teams through 2024. Built APIs at Acme Corp in 2019."
    assert check_grounding(source, source, reordered)["review_required"] is False
    assert check_grounding(source, source, "2019\n\n2024\n2025-03-12\n03/2024")["novel_numbers"] == []
    assert check_grounding("Delivered 2019%", "", "Delivered 2024%")["novel_numbers"] == ["2024%"]


def test_similarity_threshold_is_strictly_below_point_45():
    source = " ".join(f"token{i}" for i in range(20))
    at_boundary = " ".join([*(f"token{i}" for i in range(9)), *(f"new{i}" for i in range(11))])
    below = " ".join([*(f"token{i}" for i in range(8)), *(f"new{i}" for i in range(12))])
    assert check_grounding(source, source, at_boundary)["similarity"] == 0.45
    assert check_grounding(source, source, at_boundary)["review_required"] is False
    # The card reads this rather than re-applying the threshold, so it is the
    # boundary that has to be asserted, not just the derived review flag.
    assert check_grounding(source, source, at_boundary)["low_similarity"] is False
    assert check_grounding(source, source, below)["low_similarity"] is True
    assert check_grounding(source, source, below)["review_required"] is True
