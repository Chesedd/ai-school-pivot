from uuid import uuid4

from app.application.scan_grouping import GroupingSeedPage, seed_grouping_pages


def page(order, disposition, participant=None, proposed_order=None):
    return GroupingSeedPage(
        page_id=uuid4(),
        source_order=order,
        disposition=disposition,
        proposed_assignment_participant_id=participant,
        proposed_page_order=proposed_order,
    )


def test_seed_groups_matched_pages_by_participant_and_uses_complete_ai_order():
    participant = uuid4()
    second = page(1, "matched", participant, 0)
    first = page(0, "matched", participant, 1)
    groups, unresolved = seed_grouping_pages([first, second], {participant})
    assert groups == {participant: [second.page_id, first.page_id]}
    assert unresolved == []


def test_seed_falls_back_to_source_order_for_duplicate_or_incomplete_ai_order():
    participant = uuid4()
    first = page(0, "matched", participant, 4)
    second = page(1, "matched", participant, 4)
    groups, _ = seed_grouping_pages([second, first], {participant})
    assert groups[participant] == [first.page_id, second.page_id]


def test_seed_keeps_ambiguous_unmatched_and_invalid_matches_explicitly_unresolved():
    valid = uuid4()
    ambiguous = page(0, "ambiguous")
    unmatched = page(1, "unmatched")
    invalid = page(2, "matched", uuid4())
    groups, unresolved = seed_grouping_pages([invalid, unmatched, ambiguous], {valid})
    assert groups == {}
    assert unresolved == [
        (ambiguous.page_id, "ambiguous"),
        (unmatched.page_id, "unmatched"),
        (invalid.page_id, "invalid_match"),
    ]
