from app.application.capabilities import ALL_CAPABILITIES, ROLE_CAPABILITIES, capabilities_for_roles


def test_admin_has_every_capability():
    assert ROLE_CAPABILITIES["admin"] == ALL_CAPABILITIES


def test_teacher_has_exact_b1_grants():
    assert ROLE_CAPABILITIES["teacher"] == {
        "content.read", "content.create", "content.edit", "content.review.submit",
        "image_solving.use", "assessment.create", "assessment.manage", "assessment.results.read",
        "catalog.propose",
    }
    assert not {"users.manage", "catalog.manage", "content.approve", "content.archive"} & ROLE_CAPABILITIES["teacher"]


def test_student_has_only_student_workflow_grants():
    assert ROLE_CAPABILITIES["student"] == {
        "student.assignments.read", "student.attempts.submit", "student.results.read"
    }


def test_multiple_and_no_roles():
    assert capabilities_for_roles(frozenset({"teacher", "student"})) == (
        ROLE_CAPABILITIES["teacher"] | ROLE_CAPABILITIES["student"]
    )
    assert capabilities_for_roles(frozenset()) == frozenset()
    assert "catalog.propose" not in capabilities_for_roles(frozenset({"unknown"}))
