"""Real PostgreSQL paper provenance and reused remediation execution acceptance."""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from app.application.paper_checking_review import PaperReviewApproval
from app.application.remediation import RemediationError
from app.application.remediation_execution import RemediationExecutionService
from app.application.remediation_results import RemediationResultsService
from app.infrastructure.checking_models import CheckRun
from app.infrastructure.paper_remediation_repository import PaperRemediationRepository
from app.infrastructure.remediation_repository import RemediationRepository
from app.presentation.remediation_schemas import (
    CreatePaperRemediation,
    ItemInput,
    PaperCandidateSearch,
    UpdateRemediation,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from tests.integration.test_paper_checking_execution_database import (
    execution_database as execution_database,  # noqa: PLC0414
)
from tests.integration.test_paper_checking_review_database import (
    call,
    completed,
    edited,
)
from tests.integration.test_paper_result_publication_database import publish

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
    ),
]


async def published(
    context, status="partially_correct", *, remove=False, teacher_added=False
):
    run = await completed(context, status)
    assert await context[0].scalar(text("SELECT count(*) FROM remediation_plans")) == 0
    review = await call(context, run, "start")
    removed = review.draft.findings[0].finding_id
    if remove or teacher_added:
        findings = () if remove else review.draft.findings
        if teacher_added:
            findings += (
                review.draft.findings[0].model_copy(
                    update={
                        "finding_id": uuid4(),
                        "source_check_finding_id": None,
                        "short_explanation": "Teacher context",
                    }
                ),
            )
        review = await call(
            context, run, "edit", edited(review, findings=findings), review.version
        )
    if status == "not_attempted":
        review = await call(
            context,
            run,
            "edit",
            edited(
                review,
                items=tuple(
                    x.model_copy(update={"accepted_score": Decimal(0)})
                    for x in review.draft.items
                ),
            ),
            review.version,
        )
    review = await call(
        context, run, "approve", PaperReviewApproval(expected_version=review.version)
    )
    assert await context[0].scalar(text("SELECT count(*) FROM remediation_plans")) == 0
    pub = await publish(context, review.latest_approved_revision_id)
    return run, review, pub, removed


def payload(context, pub, **changes):
    ids = context[2]
    return CreatePaperRemediation(
        creation_key="paper-plan",
        source_paper_publication_id=pub["id"],
        source_assessment_item_ids=[ids["item_a"]],
        title="Practice",
        **changes,
    )


async def invoke(context, method, *args):
    async with context[1]() as db, db.begin():
        repo = RemediationRepository(db)
        return await getattr(repo, method)(*args)


async def create(context, pub, **changes):
    async with context[1]() as db, db.begin():
        return await PaperRemediationRepository(db).create(
            context[2]["teacher"],
            False,
            context[2]["paper_a"],
            payload(context, pub, **changes),
            RemediationRepository(db),
        )


async def eligibility(context):
    async with context[1]() as db:
        return await PaperRemediationRepository(db).eligibility(
            context[2]["teacher"], False, context[2]["paper_a"]
        )


@pytest.mark.parametrize(
    "status,eligible",
    [
        ("correct", False),
        ("partially_correct", True),
        ("incorrect", True),
        ("not_attempted", True),
    ],
)
async def test_published_score_eligibility(execution_database, status, eligible):
    context = execution_database
    await published(context, status)
    result = await eligibility(context)
    assert result["eligible"] is eligible
    assert result["eligible_item_count"] == int(eligible)


async def test_no_active_pointer_ineligible(execution_database):
    context = execution_database
    assert (await eligibility(context))["reason"] == "no_active_publication"
    from app.infrastructure.paper_result_publication_models import (
        PaperResultPublication,
    )
    from app.infrastructure.paper_result_publication_repository import (
        PaperResultPublicationRepository,
    )
    from app.infrastructure.scan_checking_models import PaperSubmission

    run = await completed(context, "incorrect")
    review = await call(context, run, "start")
    review = await call(
        context, run, "approve", PaperReviewApproval(expected_version=review.version)
    )
    async with context[1]() as db, db.begin():
        paper = await db.get(PaperSubmission, context[2]["paper_a"])
        value = await PaperResultPublicationRepository(db)._snapshot(
            paper, review.latest_approved_revision_id
        )
        db.add(
            PaperResultPublication(
                paper_submission_id=paper.id,
                review_id=review.latest_approved_revision.review_id,
                approved_review_revision_id=review.latest_approved_revision_id,
                published_by_user_id=context[2]["teacher"],
                publication_number=1,
                publication_snapshot=value.model_dump(mode="json"),
            )
        )
    assert not (await eligibility(context))["eligible"]


async def test_approval_alone_does_not_change_source(execution_database):
    context = execution_database
    run, review, pub, _ = await published(context)
    correction = await call(
        context,
        run,
        "edit",
        edited(
            review,
            items=tuple(
                x.model_copy(
                    update={"accepted_score": x.max_score, "status": "correct"}
                )
                for x in review.draft.items
            ),
        ),
        review.version,
    )
    await call(
        context,
        run,
        "approve",
        PaperReviewApproval(expected_version=correction.version),
    )
    result = await eligibility(context)
    assert result["active_publication_id"] == pub["id"] and result["eligible"]
    plan = await create(context, pub)
    assert plan["source_paper_publication_id"] == pub["id"]


async def test_republication_changes_new_eligibility_immediately(execution_database):
    context = execution_database
    run, review, old, _ = await published(context)
    correction = await call(
        context,
        run,
        "edit",
        edited(
            review,
            items=tuple(
                x.model_copy(
                    update={"accepted_score": x.max_score, "status": "correct"}
                )
                for x in review.draft.items
            ),
        ),
        review.version,
    )
    approved = await call(
        context,
        run,
        "approve",
        PaperReviewApproval(expected_version=correction.version),
    )
    new = await publish(context, approved.latest_approved_revision_id)
    result = await eligibility(context)
    assert not result["eligible"] and result["active_publication_id"] == new["id"]
    with pytest.raises(RemediationError, match="publication_stale"):
        await create(context, old)
    with pytest.raises(RemediationError, match="item_ineligible"):
        await create(context, new)


async def test_grade_override_alone_does_not_create_weak_items(execution_database):
    from app.application.paper_checking_review import PaperReviewOverride

    context = execution_database
    run = await completed(context)
    review = await call(context, run, "start")
    review = await call(
        context,
        run,
        "edit",
        edited(
            review,
            grade_override=PaperReviewOverride(
                override_grade="2", reason="Teacher decision"
            ),
        ),
        review.version,
    )
    review = await call(
        context, run, "approve", PaperReviewApproval(expected_version=review.version)
    )
    await publish(context, review.latest_approved_revision_id)
    assert not (await eligibility(context))["eligible"]


async def test_plan_provenance_signals_and_idempotency(execution_database):
    context = execution_database
    run, _, pub, _ = await published(context, teacher_added=True)
    findings = [x["finding_id"] for x in pub["result"]["findings"]]
    plan = await create(context, pub, publication_finding_ids=findings)
    assert plan == await create(context, pub, publication_finding_ids=findings)
    ids = context[2]
    assert plan["source_submission_id"] is None
    assert plan["source_type"] == "paper"
    assert plan["student_id"] == ids["student_a"]
    assert plan["class_group_id"] == ids["group"]
    assert plan["source_assignment_id"] == ids["assignment"]
    assert plan["source_assignment_participant_id"] == ids["participant_a"]
    assert plan["source_check_run_id"] == run.id
    assert plan["source_assessment_item_ids"] == [ids["item_a"]]
    assert set(map(str, plan["publication_finding_ids"])) == set(findings)
    assert plan["finding_ids"] == []
    with pytest.raises(RemediationError, match="idempotency_conflict"):
        await create(context, pub)
    with pytest.raises(RemediationError, match="idempotency_conflict"):
        await create(context, pub, instructions="Changed")


@pytest.mark.parametrize(
    "case", ["foreign_item", "foreign_finding", "foreign_publication", "perfect"]
)
async def test_source_substitution_and_full_credit_rejected(execution_database, case):
    context = execution_database
    _, _, pub, _ = await published(
        context, "correct" if case == "perfect" else "incorrect"
    )
    p = payload(context, pub)
    changes = {
        "foreign_item": {"source_assessment_item_ids": [context[2]["item_b"]]},
        "foreign_finding": {"publication_finding_ids": [uuid4()]},
        "foreign_publication": {"source_paper_publication_id": uuid4()},
        "perfect": {},
    }[case]
    async with context[1]() as db, db.begin():
        with pytest.raises(RemediationError):
            await PaperRemediationRepository(db).create(
                context[2]["teacher"],
                False,
                context[2]["paper_a"],
                p.model_copy(update=changes),
                RemediationRepository(db),
            )


async def replacement(context, run, review):
    correction = await call(
        context, run, "edit", edited(review, summary="Replacement"), review.version
    )
    approved = await call(
        context,
        run,
        "approve",
        PaperReviewApproval(expected_version=correction.version),
    )
    return await publish(context, approved.latest_approved_revision_id)


async def test_active_draft_update_and_stale_assignment(execution_database):
    context = execution_database
    ids = context[2]
    run, review, pub, _ = await published(context)
    plan = await create(
        context,
        pub,
        items=[ItemInput(task_version_id=ids["version_b"], selection_source="manual")],
    )
    updated = await invoke(
        context,
        "update",
        plan["id"],
        ids["teacher"],
        False,
        UpdateRemediation(
            expected_updated_at=plan["updated_at"],
            source_assessment_item_ids=[ids["item_a"]],
            publication_finding_ids=[],
        ),
    )
    b = await replacement(context, run, review)
    for method, args in [
        ("assign", (False,)),
        (
            "update",
            (
                UpdateRemediation(
                    expected_updated_at=updated["updated_at"],
                    source_assessment_item_ids=[ids["item_a"]],
                ),
            ),
        ),
    ]:
        with pytest.raises(RemediationError, match="publication_stale"):
            await invoke(context, method, plan["id"], ids["teacher"], False, *args)
    with pytest.raises(RemediationError, match="publication_stale"):
        async with context[1]() as db:
            await PaperRemediationRepository(db).selected(
                ids["teacher"], False, ids["paper_a"], pub["id"], [ids["item_a"]], []
            )
    with pytest.raises(RemediationError, match="idempotency_conflict"):
        await create(context, b)
    # Identical intent remains a historical idempotent replay.
    assert (
        await create(
            context,
            pub,
            items=[
                ItemInput(task_version_id=ids["version_b"], selection_source="manual")
            ],
        )
    )["id"] == plan["id"]


async def test_assigned_plan_survives_republication_and_executes(execution_database):
    context = execution_database
    ids = context[2]
    run, review, pub, _ = await published(context)
    before = await context[0].scalar(
        text("SELECT status FROM assessment_scan_batches WHERE id=:batch"), ids
    )
    assert await context[0].scalar(text("SELECT count(*) FROM remediation_plans")) == 0
    plan = await create(
        context,
        pub,
        items=[ItemInput(task_version_id=ids["version_b"], selection_source="manual")],
    )
    assigned = await invoke(context, "assign", plan["id"], ids["teacher"], False, False)
    assert assigned["review_acknowledged_at"] is None
    await replacement(context, run, review)
    assert (await invoke(context, "student_detail", plan["id"], ids["student_a"]))[
        "status"
    ] == "assigned"
    rubric = uuid4()
    await context[0].execute(
        text(
            "INSERT INTO rubrics(id,task_version_id,max_score,grading_mode,notes) VALUES (:rubric,:version_b,1,'points','Practice rubric')"
        ),
        {**ids, "rubric": rubric},
    )
    await context[0].execute(
        text(
            "INSERT INTO rubric_items(rubric_id,criterion,max_points,required,order_index) VALUES (:rubric,'Criterion',1,true,0)"
        ),
        {"rubric": rubric},
    )
    await context[0].execute(
        text("""INSERT INTO accepted_answers
        (task_version_id,answer_value,value_kind,canonical_text,normalization_policy_code,normalization_policy_version)
        VALUES (:version_b,'answer','text','answer','exact_text_v1',1)"""),
        ids,
    )
    service = RemediationExecutionService(context[1])
    execution, status = await service.start(plan["id"], ids["student_a"])
    assert status == 201
    item = execution["items"][0]["remediation_plan_item_id"]
    await service.save_answer(plan["id"], item, ids["student_a"], "answer")
    submitted = await service.submit(plan["id"], ids["student_a"])
    async with context[1]() as db:
        checking = await db.scalar(
            select(CheckRun).where(CheckRun.submission_id == submitted["submission_id"])
        )
        assert checking.input_snapshot["items"][0]["remediation_plan_item_id"] == str(
            item
        )
        assert checking.paper_submission_id is None
        result = await RemediationResultsService(db).student_execution(
            plan["id"], ids["student_a"]
        )
        assert result["execution_status"] == "checking"
        teacher = await RemediationResultsService(db).teacher_result(
            plan["id"], ids["teacher"]
        )
        assert teacher["student_id"] == ids["student_a"]
    from tests.integration.test_remediation_checking_database import _run_checkers

    run_row = (
        (
            await context[0].execute(
                text("SELECT * FROM check_runs WHERE id=:id"), {"id": checking.id}
            )
        )
        .mappings()
        .one()
    )
    await _run_checkers(context[1], run_row)
    async with context[1]() as db:
        result = await RemediationResultsService(db).student_execution(
            plan["id"], ids["student_a"]
        )
        assert result["execution_status"] == "checked"
        assert result["items"][0]["score_suggested"] == Decimal(1)
        result = await RemediationResultsService(db).teacher_result(
            plan["id"], ids["teacher"]
        )
        assert result["suggested_score_total"] == Decimal(1)
    with pytest.raises(RemediationError, match="not_found"):
        await service.start(plan["id"], ids["student_b"])
    history = await invoke(
        context,
        "history",
        ids["group"],
        ids["student_a"],
        ids["teacher"],
        False,
        "all",
        0,
        50,
    )
    assert history["items"][0]["source_paper_publication_id"] == pub["id"]
    assert (
        await context[0].scalar(
            text("SELECT status FROM assessment_scan_batches WHERE id=:batch"), ids
        )
        == before
    )
    assert (
        await context[0].scalar(
            text(
                "SELECT count(*) FROM student_submissions WHERE assignment_participant_id=:participant_a AND id<>:digital_submission"
            ),
            ids,
        )
        == 0
    )


@pytest.mark.parametrize("mode", ["suggested", "manual"])
async def test_candidates_removed_findings_and_teacher_context(
    execution_database, mode
):
    context = execution_database
    ids = context[2]
    _, _, pub, removed = await published(context, remove=True, teacher_added=True)
    # The authoritative snapshot contains only the teacher-added finding.
    finding = pub["result"]["findings"][0]["finding_id"]
    assert str(removed) != finding
    skill, task, version, subtopic = uuid4(), uuid4(), uuid4(), uuid4()
    await context[0].execute(
        text(
            "INSERT INTO subtopics(id,topic_id,code,name,normalized_name) VALUES (:subtopic,:topic,:code,'Practice',:normalized)"
        ),
        {**ids, "subtopic": subtopic, "code": subtopic.hex, "normalized": subtopic.hex},
    )
    await context[0].execute(
        text(
            "INSERT INTO skills(id,subtopic_id,code,name,normalized_name) VALUES (:skill,:subtopic,:code,'Practice skill',:normalized)"
        ),
        {
            **ids,
            "skill": skill,
            "subtopic": subtopic,
            "code": skill.hex,
            "normalized": skill.hex,
        },
    )
    await context[0].execute(
        text(
            "INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:teacher)"
        ),
        {**ids, "task": task},
    )
    await context[0].execute(
        text(
            "INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,answer_format,difficulty,status,created_by) VALUES (:version,:task,1,'Practice','Practice','problem','short_text',50,'approved',:teacher)"
        ),
        {**ids, "task": task, "version": version},
    )
    await context[0].execute(
        text(
            "INSERT INTO task_skill_links(task_version_id,skill_id,is_primary,weight) VALUES (:version_a,:skill,true,1),(:version,:skill,true,1)"
        ),
        {**ids, "skill": skill, "version": version},
    )
    p = PaperCandidateSearch(
        **payload(context, pub).model_dump(
            include={"source_paper_publication_id", "source_assessment_item_ids"}
        ),
        publication_finding_ids=[finding],
        mode=mode,
    )
    candidates = await invoke(
        context, "paper_candidates", ids["teacher"], False, ids["paper_a"], p
    )
    assert [x["task_version_id"] for x in candidates] == [version]
    assert candidates == await invoke(
        context, "paper_candidates", ids["teacher"], False, ids["paper_a"], p
    )
    assert all(
        x["matched_typical_error_id"] is None for c in candidates for x in c["reasons"]
    )
    assert (
        candidates[0]["subject_id"] == ids["subject"]
        and candidates[0]["grade_id"] == ids["grade"]
    )
    plan = await create(context, pub, publication_finding_ids=[finding])
    assert plan["finding_ids"] == []
    with pytest.raises(RemediationError, match="finding_not_found"):
        await invoke(
            context,
            "paper_candidates",
            ids["teacher"],
            False,
            ids["paper_a"],
            p.model_copy(update={"publication_finding_ids": [removed]}),
        )


@pytest.mark.parametrize(
    "state", ["mixed", "empty", "missing_paper", "missing_publication"]
)
async def test_database_source_xor(execution_database, state):
    context = execution_database
    _, _, pub, _ = await published(context)
    plan = await create(context, pub)
    changes = {
        "mixed": "source_submission_id=gen_random_uuid()",
        "empty": "source_paper_submission_id=NULL,source_paper_publication_id=NULL",
        "missing_paper": "source_paper_submission_id=NULL",
        "missing_publication": "source_paper_publication_id=NULL",
    }[state]
    async with context[1]() as db:
        with pytest.raises(IntegrityError) as exc:
            async with db.begin_nested():
                await db.execute(
                    text(f"UPDATE remediation_plans SET {changes} WHERE id=:id"),
                    {"id": plan["id"]},
                )
        assert "ck_remediation_plans_source_xor" in str(exc.value)


async def test_signal_publication_constraint_and_uniqueness(execution_database):
    context = execution_database
    _, _, pub, _ = await published(context)
    plan = await create(context, pub)
    async with context[1]() as db:
        for publication, item, constraint in (
            (pub["id"], context[2]["item_a"], "uq_remediation_paper_item_signal"),
            (
                uuid4(),
                context[2]["item_b"],
                "fk_remediation_paper_item_plan_publication",
            ),
        ):
            with pytest.raises(IntegrityError, match=constraint):
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "INSERT INTO remediation_plan_paper_item_signals(remediation_plan_id,source_paper_publication_id,assessment_item_id) VALUES (:plan,:pub,:item)"
                        ),
                        {
                            "plan": plan["id"],
                            "pub": publication,
                            "item": item,
                        },
                    )


async def test_completed_batch_follow_up_and_zero_automatic_plans(execution_database):
    from tests.integration.test_paper_batch_orchestration_database import action
    from tests.integration.test_paper_result_publication_database import approval

    context = execution_database
    ids = context[2]
    _, _, pub, _ = await published(context)
    _, second = await approval(context, "b")
    await publish(context, second.latest_approved_revision_id, "b")
    assert await context[0].scalar(text("SELECT count(*) FROM remediation_plans")) == 0
    assert (await action(context, "finalize")).batch_status == "completed"
    assert await context[0].scalar(text("SELECT count(*) FROM remediation_plans")) == 0
    assert (await eligibility(context))["eligible"]
    candidates = await invoke(
        context,
        "paper_candidates",
        ids["teacher"],
        False,
        ids["paper_a"],
        PaperCandidateSearch(
            source_paper_publication_id=pub["id"],
            source_assessment_item_ids=[ids["item_a"]],
            mode="manual",
        ),
    )
    assert candidates == []  # Both task versions belong to the original assessment.
    plan = await create(
        context,
        pub,
        items=[ItemInput(task_version_id=ids["version_b"], selection_source="manual")],
    )
    await invoke(context, "assign", plan["id"], ids["teacher"], False, False)
    assert (await action(context, "progress")).batch_status == "completed"


@pytest.mark.parametrize(
    "role", ["teacher", "student", "unrelated_teacher", "class_teacher"]
)
async def test_http_teacher_authorization_and_allowlist(execution_database, role):
    import httpx
    from app.application.capabilities import capabilities_for_roles
    from app.application.principal import Principal
    from app.db.session import get_session
    from app.presentation.auth_dependencies import require_principal
    from app.presentation.remediation_routes import router
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    context = execution_database
    ids = context[2]
    _, _, pub, _ = await published(context)
    user = ids["teacher"]
    if role in {"unrelated_teacher", "class_teacher"}:
        user = uuid4()
        await context[0].execute(
            text(
                "INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:id,:login,:login,'Another teacher','hash')"
            ),
            {"id": user, "login": user.hex},
        )
        if role == "class_teacher":
            await context[0].execute(
                text(
                    "INSERT INTO class_group_teachers(class_group_id,teacher_user_id,assigned_by) VALUES (:group,:user,:teacher)"
                ),
                {**ids, "user": user},
            )
    roles = frozenset({"student" if role == "student" else "teacher"})
    app = FastAPI()
    app.include_router(router)

    async def session():
        async with context[1]() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[require_principal] = lambda: Principal(
        user, "actor", "Actor", roles, capabilities_for_roles(roles), None
    )

    @app.exception_handler(RemediationError)
    async def error_handler(request, error):
        return JSONResponse(status_code=error.status, content={"detail": error.code})

    base = f"/api/assessment-core/paper-submissions/{ids['paper_a']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://localhost:5173"},
    ) as client:
        result = await client.get(base + "/remediation-eligibility")
        expected = (
            403 if role == "student" else 404 if role == "unrelated_teacher" else 200
        )
        assert result.status_code == expected, result.text
        p = payload(context, pub)
        created = await client.post(
            base + "/remediations", json=p.model_dump(mode="json")
        )
        assert created.status_code == (201 if expected == 200 else expected), (
            created.text
        )
        if expected == 200:
            data = created.json()
            assert data["student_id"] == str(ids["student_a"])
            for forbidden in (
                "scan_page",
                "provider",
                "storage_reference",
                "source_check_finding",
                "publication_snapshot",
            ):
                assert forbidden not in created.text
            assert created.headers["Location"] == f"/api/remediations/{data['id']}"


async def test_grouping_to_existing_remediation_checking(tmp_path):
    from tests.integration.test_paper_batch_orchestration_database import (
        test_grouping_through_execution_review_publication_and_finalization,
    )
    from tests.integration.test_paper_checking_execution_database import start

    seed = execution_database.__wrapped__(tmp_path, confirmed=False)
    try:
        context = await anext(seed)
        # This shared PR7 fixture also seeds an unrelated digital submission.
        # Remove it here so the complete paper loop has no digital source row.
        await context[0].execute(
            text("DELETE FROM student_submissions WHERE id=:digital_submission"),
            context[2],
        )
        # Exercise PR10's genuine grouping confirmation through explicit publication.
        await test_grouping_through_execution_review_publication_and_finalization(
            context
        )
        ids = context[2]
        run = await start(context)
        review = await call(context, run, "start")
        review = await call(
            context,
            run,
            "edit",
            edited(
                review,
                items=tuple(
                    x.model_copy(
                        update={
                            "status": "partially_correct",
                            "accepted_score": x.max_score / 2,
                        }
                    )
                    for x in review.draft.items
                ),
            ),
            review.version,
        )
        review = await call(
            context,
            run,
            "approve",
            PaperReviewApproval(expected_version=review.version),
        )
        pub = await publish(context, review.latest_approved_revision_id)
        assert (await eligibility(context))["eligible"]
        plan = await create(
            context,
            pub,
            items=[
                ItemInput(task_version_id=ids["version_b"], selection_source="manual")
            ],
        )
        await invoke(context, "assign", plan["id"], ids["teacher"], False, False)
        rubric = uuid4()
        await context[0].execute(
            text(
                "INSERT INTO rubrics(id,task_version_id,max_score,grading_mode) VALUES (:rubric,:version_b,1,'points')"
            ),
            {**ids, "rubric": rubric},
        )
        await context[0].execute(
            text(
                "INSERT INTO rubric_items(rubric_id,criterion,max_points,required,order_index) VALUES (:rubric,'Criterion',1,true,0)"
            ),
            {"rubric": rubric},
        )
        execution = RemediationExecutionService(context[1])
        work, _ = await execution.start(plan["id"], ids["student_a"])
        await execution.save_answer(
            plan["id"],
            work["items"][0]["remediation_plan_item_id"],
            ids["student_a"],
            "answer",
        )
        submitted = await execution.submit(plan["id"], ids["student_a"])
        assert (
            await context[0].scalar(
                text(
                    "SELECT count(*) FROM check_runs WHERE submission_id=:id AND paper_submission_id IS NULL"
                ),
                {"id": submitted["submission_id"]},
            )
            == 1
        )
        assert (
            await context[0].scalar(
                text(
                    "SELECT count(*) FROM student_submissions WHERE assignment_participant_id=:participant_a AND id<>:digital_submission"
                ),
                ids,
            )
            == 0
        )
    finally:
        await seed.aclose()
