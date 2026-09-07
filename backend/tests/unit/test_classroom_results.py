from decimal import Decimal
from types import SimpleNamespace
from app.application.classroom_results import check_status, score_totals

def test_check_state_projection_covers_persisted_states():
    assert check_status(False,None)=="not_submitted"
    assert check_status(True,None)=="submitted"
    for state in ("pending","running"): assert check_status(True,state)=="checking"
    assert check_status(True,"completed")=="checked"
    assert check_status(True,"completed_with_review_required")=="review_required"
    for state in ("failed_retryable","failed_terminal"): assert check_status(True,state)=="check_failed"

def test_score_total_is_null_when_any_item_has_no_suggestion():
    rows=[SimpleNamespace(score_suggested=Decimal("2"),max_score=Decimal("3")),SimpleNamespace(score_suggested=None,max_score=Decimal("4"))]
    assert score_totals(rows)==(None,Decimal("7"),None)

def test_complete_suggested_percentage_remains_decimal():
    rows=[SimpleNamespace(score_suggested=Decimal("2"),max_score=Decimal("4"))]
    assert score_totals(rows)==(Decimal("2"),Decimal("4"),Decimal("50"))
