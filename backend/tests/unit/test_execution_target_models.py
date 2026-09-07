from app.infrastructure.model_registry import register_all_models
from app.infrastructure.models import Base


def test_shared_execution_models_expose_remediation_targets_without_duplicates():
    register_all_models()
    expected = {
        "student_submissions": "remediation_plan_id",
        "student_answers": "remediation_plan_item_id",
        "check_results": "remediation_plan_item_id",
        "model_runs": "remediation_plan_item_id",
        "checker_events": "remediation_plan_item_id",
    }
    for table_name, column_name in expected.items():
        table = Base.metadata.tables[table_name]
        assert column_name in table.c
        assert table.c[column_name].nullable

    assert "remediation_submissions" not in Base.metadata.tables
    assert "remediation_answers" not in Base.metadata.tables
    assert "remediation_check_runs" not in Base.metadata.tables
