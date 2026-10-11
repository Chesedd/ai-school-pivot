"""Transactional review overlay; never writes AI evidence or intake lifecycle."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update

from app.application.paper_checking_execution import (
    PaperCheckingExecutionError,
    compile_paper_request,
)
from app.application.paper_checking_review import (
    PaperReviewApproval,
    PaperReviewDraft,
    PaperReviewError,
    PaperReviewFinding,
    PaperReviewGrade,
    PaperReviewItem,
    PaperReviewResponse,
    PaperReviewRevision,
    approve_scores,
    canonicalize_draft,
    changed_domains,
    seed_review_item,
)
from app.application.scan_checking_contracts import (
    PaperCheckResponse,
    validate_checking_response,
)
from app.application.scan_grading_policy import ScanGradingPolicy
from app.infrastructure.checking_models import (
    CheckFinding,
    CheckResult,
    CheckRun,
    ModelRun,
    PaperAIRevision,
    PaperCheckFindingRegion,
)
from app.infrastructure.paper_checking_review_models import (
    PaperCheckReview,
    PaperCheckReviewRevision,
)
from app.infrastructure.scan_checking_models import (
    PaperSubmission,
    PaperSubmissionPage,
    ScanGradingPolicyRevision,
)


def _draft(value) -> PaperReviewDraft:
    return PaperReviewDraft.model_validate_json(json.dumps(value))


class PaperCheckingReviewRepository:
    def __init__(self, db):
        self.db = db

    async def _manual_policy(self, paper, review=None):
        query = select(ScanGradingPolicyRevision).where(
            ScanGradingPolicyRevision.batch_id == paper.batch_id,
            ScanGradingPolicyRevision.assessment_variant_id
            == paper.assigned_variant_id,
        )
        if review is not None:
            seed = await self.db.scalar(
                select(PaperCheckReviewRevision).where(
                    PaperCheckReviewRevision.review_id == review.id,
                    PaperCheckReviewRevision.revision_number == 0,
                )
            )
            if seed is None:
                raise PaperReviewError("paper_review_policy_mismatch")
            query = query.where(
                ScanGradingPolicyRevision.revision == seed.grading_policy_revision,
                ScanGradingPolicyRevision.policy_fingerprint
                == seed.grading_policy_fingerprint,
            )
        row = await self.db.scalar(
            query.order_by(ScanGradingPolicyRevision.revision.desc()).limit(1)
        )
        if row is None:
            raise PaperReviewError("paper_grading_policy_incomplete", 409)
        policy = ScanGradingPolicy.model_validate_json(json.dumps(row.policy_json))
        if (
            policy.fingerprint != row.policy_fingerprint
            or policy.schema_version != row.policy_schema_version
        ):
            raise PaperReviewError("paper_review_policy_mismatch")
        return row

    async def _source(self, paper_id, run_id, *, lock=False):
        if run_id is None:
            from app.infrastructure.paper_batch_orchestration_repository import (
                PaperBatchOrchestrationRepository,
            )

            batch_id = await self.db.scalar(
                select(PaperSubmission.batch_id).where(PaperSubmission.id == paper_id)
            )
            if batch_id is None:
                raise PaperReviewError("paper_submission_not_found", 404)
            if lock:
                await PaperBatchOrchestrationRepository(self.db).load(
                    batch_id, lock=True
                )
            query = (
                select(PaperSubmission)
                .where(PaperSubmission.id == paper_id)
                .execution_options(populate_existing=True)
            )
            paper = await self.db.scalar(query.with_for_update() if lock else query)
            review = await self._review(paper_id, None)
            return paper, None, await self._manual_policy(paper, review)

        paper = await self.db.get(PaperSubmission, paper_id)
        query = select(CheckRun).where(CheckRun.id == run_id)
        run = await self.db.scalar(query.with_for_update() if lock else query)
        if (
            paper is None
            or run is None
            or run.paper_submission_id != paper_id
            or run.submission_id is not None
        ):
            raise PaperReviewError("paper_check_run_not_found", 404)
        ai = await self.db.scalar(
            select(PaperAIRevision).where(PaperAIRevision.check_run_id == run_id)
        )
        if (
            run.status != "completed_with_review_required"
            or ai is None
            or ai.paper_submission_id != paper_id
        ):
            raise PaperReviewError("paper_review_not_ready", 409)
        return paper, run, ai

    async def _policy(self, paper, run, ai):
        """Use the exact immutable revision, never current assessment configuration."""
        if run is None:
            policy = ScanGradingPolicy.model_validate_json(json.dumps(ai.policy_json))
            return policy, tuple(x.assessment_item_id for x in policy.assessment_items)
        try:
            snapshot = run.input_snapshot
            frozen = snapshot["grading_policy"]
            row = await self.db.get(
                ScanGradingPolicyRevision, UUID(frozen["revision_id"])
            )
            policy = ScanGradingPolicy.model_validate_json(json.dumps(frozen["policy"]))
            if row is None or (
                row.batch_id != paper.batch_id
                or row.assessment_variant_id != paper.assigned_variant_id
                or snapshot["assigned_variant_id"] != str(paper.assigned_variant_id)
                or snapshot["paper_submission_id"] != str(paper.id)
                or snapshot["batch_id"] != str(paper.batch_id)
                or row.revision != frozen["revision"]
                or row.revision != ai.grading_policy_revision
                or row.policy_fingerprint != ai.grading_policy_fingerprint
                or row.policy_fingerprint != frozen["fingerprint"]
                or row.policy_fingerprint != policy.fingerprint
                or row.policy_schema_version != frozen["schema_version"]
                or row.policy_schema_version != policy.schema_version
                or row.policy_json != frozen["policy"]
            ):
                raise ValueError("policy_provenance")
            items = snapshot["items"]
            maxima = {UUID(x["assessment_item_id"]): x["points"] for x in items}
            if len(maxima) != len(items) or {
                k: Decimal(v) for k, v in maxima.items()
            } != {x.assessment_item_id: x.max_score for x in policy.assessment_items}:
                raise ValueError("policy_items")
            order = tuple(
                UUID(x["assessment_item_id"])
                for x in sorted(
                    items, key=lambda x: (x["position"], x["assessment_item_id"])
                )
            )
            return policy, order
        except (KeyError, TypeError, ValueError, ValidationError):
            raise PaperReviewError("paper_review_policy_mismatch") from None

    async def _evidence(self, paper, run, ai):
        """Validate canonical rows against the immutable, validated PR7 response."""
        results = tuple(
            (
                await self.db.scalars(
                    select(CheckResult).where(CheckResult.check_run_id == run.id)
                )
            ).all()
        )
        pairs = (
            await self.db.execute(
                select(CheckFinding, PaperCheckFindingRegion)
                .join(
                    PaperCheckFindingRegion,
                    PaperCheckFindingRegion.check_finding_id == CheckFinding.id,
                )
                .join(CheckResult, CheckResult.id == CheckFinding.check_result_id)
                .where(CheckResult.check_run_id == run.id)
            )
        ).all()
        try:
            compiled = compile_paper_request(
                run.input_snapshot, provider_id="review", model_id="review"
            )
            response = PaperCheckResponse.model_validate_json(
                json.dumps(ai.validated_response)
            )
            validate_checking_response(compiled.request, response)
            model = await self.db.get(ModelRun, ai.model_run_id)
            if (
                model is None
                or model.status != "succeeded"
                or model.check_run_id != run.id
                or model.paper_submission_id != paper.id
                or model.validated_output != ai.validated_response
                or ai.response_schema_version != response.schema_version
            ):
                raise ValueError("model_provenance")
            by_item = {x.assessment_item_id: x for x in results}
            if len(by_item) != len(results) or set(by_item) != {
                x[0] for x in compiled.item_ids.values()
            }:
                raise ValueError("result_coverage")
            expected_findings = {}
            for item in response.items:
                item_id, task_id = compiled.item_ids[item.item_token]
                result = by_item[item_id]
                if (
                    result.task_version_id != task_id
                    or result.max_score != item.rubric_max_score
                    or result.score_suggested != item.rubric_score
                    or result.result_status != item.status
                    or result.checker_type != "paper_vision"
                    or result.validated_result != item.model_dump(mode="json")
                ):
                    raise ValueError("result_inconsistent")
                for f in item.findings:
                    expected_findings[f.finding_token] = (result.id, f)
            count = await self.db.scalar(
                select(func.count())
                .select_from(CheckFinding)
                .join(CheckResult)
                .where(CheckResult.check_run_id == run.id)
            )
            if (
                count != len(pairs)
                or len(pairs) != len(expected_findings)
                or ai.summary_draft != response.summary_draft
            ):
                raise ValueError("finding_coverage")
            for finding, region in pairs:
                result_id, f = expected_findings.pop(region.finding_token)
                if (
                    region.paper_ai_revision_id != ai.id
                    or finding.check_result_id != result_id
                    or region.scan_page_id != compiled.page_ids[f.page_token]
                    or region.provider_category != f.category
                    or region.short_explanation != f.short_explanation
                    or region.detailed_explanation != f.detailed_explanation
                    or region.region != f.region.model_dump(mode="json")
                    or region.coordinate_space_version != "normalized_upright_v1"
                    or finding.severity
                    != {"info": "info", "warning": "minor", "error": "major"}[
                        f.severity
                    ]
                ):
                    raise ValueError("finding_inconsistent")
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
            PaperCheckingExecutionError,
        ):
            raise PaperReviewError("paper_review_source_inconsistent", 409) from None
        return results, pairs

    async def _validate(self, draft, paper, run, ai):
        # Revalidate even when a caller used Pydantic's unchecked model_copy.
        draft = _draft(draft.model_dump(mode="json"))
        policy, order = await self._policy(paper, run, ai)
        if run is None:
            if draft.source_kind != "manual" or draft.paper_submission_id != paper.id:
                raise PaperReviewError("paper_review_provenance_mismatch")
            draft = canonicalize_draft(draft, order)
            maxima = {
                x.assessment_item_id: x.max_score for x in policy.assessment_items
            }
            if any(x.max_score != maxima[x.assessment_item_id] for x in draft.items):
                raise PaperReviewError("paper_review_policy_mismatch")
            pages = set(
                (
                    await self.db.scalars(
                        select(PaperSubmissionPage.scan_page_id).where(
                            PaperSubmissionPage.paper_submission_id == paper.id
                        )
                    )
                ).all()
            )
            if any(x.scan_page_id not in pages for x in draft.findings):
                raise PaperReviewError("paper_review_foreign_page")
            return draft, policy
        if draft.source_kind != "ai":
            raise PaperReviewError("paper_review_provenance_mismatch")
        results, pairs = await self._evidence(paper, run, ai)
        if (draft.paper_submission_id, draft.check_run_id, draft.ai_revision_id) != (
            paper.id,
            run.id,
            ai.id,
        ):
            raise PaperReviewError("paper_review_provenance_mismatch")
        draft = canonicalize_draft(draft, order)
        canonical = {x.assessment_item_id: x for x in results}
        for item in draft.items:
            source = canonical[item.assessment_item_id]
            if source.id != item.source_check_result_id:
                raise PaperReviewError("paper_review_result_lineage_mismatch")
            if item.max_score != source.max_score:
                raise PaperReviewError("paper_review_policy_mismatch")
        pages = set(
            (
                await self.db.scalars(
                    select(PaperSubmissionPage.scan_page_id).where(
                        PaperSubmissionPage.paper_submission_id == paper.id
                    )
                )
            ).all()
        )
        frozen_pages = {UUID(x["scan_page_id"]) for x in run.input_snapshot["pages"]}
        if pages != frozen_pages:
            raise PaperReviewError("paper_review_source_inconsistent", 409)
        lineage = {finding.id for finding, _ in pairs}
        for finding in draft.findings:
            if finding.scan_page_id not in pages:
                raise PaperReviewError("paper_review_foreign_page")
            if (
                finding.source_check_finding_id is not None
                and finding.source_check_finding_id not in lineage
            ):
                raise PaperReviewError("paper_review_finding_lineage_mismatch")
        return draft, policy

    async def _review(self, paper_id, run_id, *, lock=False):
        query = (
            select(PaperCheckReview)
            .where(
                PaperCheckReview.paper_submission_id == paper_id,
                PaperCheckReview.check_run_id == run_id,
            )
            .execution_options(populate_existing=True)
        )
        row = await self.db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise PaperReviewError("paper_review_not_started", 409)
        return row

    def _revision(
        self,
        review,
        ai,
        actor,
        number,
        kind,
        draft,
        changes,
        grade=None,
        approved_at=None,
    ):
        row = PaperCheckReviewRevision(
            id=uuid4(),
            review_id=review.id,
            paper_submission_id=review.paper_submission_id,
            check_run_id=review.check_run_id,
            ai_revision_id=review.ai_revision_id,
            source_kind=review.source_kind,
            revision_number=number,
            revision_kind=kind,
            snapshot=draft.model_dump(mode="json"),
            actor_user_id=actor,
            grading_policy_revision=ai.revision
            if review.source_kind == "manual"
            else ai.grading_policy_revision,
            grading_policy_fingerprint=ai.policy_fingerprint
            if review.source_kind == "manual"
            else ai.grading_policy_fingerprint,
            change_summary=list(changes),
            approved_at=approved_at,
            **(grade.model_dump() if grade else {}),
        )
        self.db.add(row)
        return row

    async def start_manual(self, paper_id, actor):
        from app.application.paper_batch_orchestration import PaperBatchError
        from app.infrastructure.paper_batch_orchestration_repository import (
            PaperBatchOrchestrationRepository,
        )
        from app.infrastructure.scan_checking_models import ScanCheckingEvent

        batch_id = await self.db.scalar(
            select(PaperSubmission.batch_id).where(PaperSubmission.id == paper_id)
        )
        if batch_id is None:
            raise PaperReviewError("paper_submission_not_found", 404)
        orchestration = PaperBatchOrchestrationRepository(self.db)
        batch = await orchestration.load(batch_id, lock=True)
        paper = await self.db.scalar(
            select(PaperSubmission)
            .where(PaperSubmission.id == paper_id)
            .with_for_update()
        )
        existing = await self.db.scalar(
            select(PaperCheckReview).where(
                PaperCheckReview.paper_submission_id == paper_id,
                PaperCheckReview.source_kind == "manual",
            )
        )
        if existing:
            return await self._response(existing)
        if await self.db.scalar(
            select(PaperCheckReview.id).where(
                PaperCheckReview.paper_submission_id == paper_id
            )
        ):
            raise PaperReviewError("paper_manual_review_ai_result_available", 409)
        runs = (
            await self.db.scalars(
                select(CheckRun)
                .where(CheckRun.paper_submission_id == paper_id)
                .order_by(CheckRun.attempt_no.desc())
            )
        ).all()
        if any(x.status in {"pending", "running"} for x in runs):
            raise PaperReviewError("paper_manual_review_check_in_progress", 409)
        if any(
            x.status in {"completed", "completed_with_review_required"} for x in runs
        ):
            raise PaperReviewError("paper_manual_review_ai_result_available", 409)
        if runs and runs[0].status not in {"failed_retryable", "failed_terminal"}:
            raise PaperReviewError("paper_manual_review_not_available", 409)
        policy_row = await self._manual_policy(paper)
        policy, _ = await self._policy(paper, None, policy_row)
        try:
            await orchestration.begin_attempt(batch, actor_id=actor)
        except PaperBatchError as exc:
            raise PaperReviewError(exc.code, exc.status) from None
        draft = PaperReviewDraft(
            paper_submission_id=paper_id,
            source_kind="manual",
            check_run_id=None,
            ai_revision_id=None,
            summary="",
            items=tuple(
                PaperReviewItem(
                    assessment_item_id=x.assessment_item_id,
                    source_check_result_id=None,
                    status=None,
                    accepted_score=None,
                    max_score=x.max_score,
                )
                for x in policy.assessment_items
            ),
        )
        review = PaperCheckReview(
            id=uuid4(),
            paper_submission_id=paper_id,
            source_kind="manual",
            check_run_id=None,
            ai_revision_id=None,
            state="reviewing",
            version=1,
            draft=draft.model_dump(mode="json"),
            created_by_user_id=actor,
            updated_by_user_id=actor,
        )
        self.db.add(review)
        await self.db.flush()
        self._revision(review, policy_row, actor, 0, "manual_seed", draft, ())
        self.db.add(
            ScanCheckingEvent(
                batch_id=batch_id,
                aggregate_type="paper_submission",
                aggregate_id=paper_id,
                event_type="paper.manual_review_started",
                actor_user_id=actor,
                details={"review_id": str(review.id)},
            )
        )
        await self.db.flush()
        return await self._response(review)

    async def start(self, paper_id, run_id, actor):
        paper, run, ai = await self._source(paper_id, run_id, lock=True)
        existing = await self.db.scalar(
            select(PaperCheckReview).where(PaperCheckReview.check_run_id == run_id)
        )
        if existing is not None:
            return await self._response(existing)
        await self._policy(paper, run, ai)
        results, pairs = await self._evidence(paper, run, ai)
        items = tuple(
            seed_review_item(
                assessment_item_id=x.assessment_item_id,
                source_check_result_id=x.id,
                status=x.result_status,
                suggested_score=x.score_suggested,
                max_score=x.max_score,
            )
            for x in results
        )
        findings = tuple(
            PaperReviewFinding.model_validate_json(
                json.dumps(
                    {
                        "finding_id": str(f.id),
                        "source_check_finding_id": str(f.id),
                        "scan_page_id": str(r.scan_page_id),
                        "category": r.provider_category,
                        "severity": {
                            "info": "info",
                            "minor": "warning",
                            "major": "error",
                        }[f.severity],
                        "short_explanation": r.short_explanation,
                        "detailed_explanation": r.detailed_explanation,
                        "region": r.region,
                        "coordinate_space_version": r.coordinate_space_version,
                    }
                )
            )
            for f, r in pairs
        )
        draft = PaperReviewDraft(
            paper_submission_id=paper_id,
            check_run_id=run_id,
            ai_revision_id=ai.id,
            summary=ai.summary_draft,
            items=items,
            findings=findings,
        )
        draft, _ = await self._validate(draft, paper, run, ai)
        row = PaperCheckReview(
            id=uuid4(),
            paper_submission_id=paper_id,
            check_run_id=run_id,
            ai_revision_id=ai.id,
            state="reviewing",
            version=1,
            draft=draft.model_dump(mode="json"),
            created_by_user_id=actor,
            updated_by_user_id=actor,
        )
        self.db.add(row)
        await self.db.flush()
        self._revision(row, ai, actor, 0, "ai_seed", draft, ())
        await self.db.flush()
        return await self._response(row)

    async def get(self, paper_id, run_id):
        await self._source(paper_id, run_id)
        return await self._response(await self._review(paper_id, run_id))

    async def edit(self, paper_id, run_id, actor, draft, expected_version):
        paper, run, ai = await self._source(paper_id, run_id, lock=run_id is None)
        review = await self._review(paper_id, run_id, lock=True)
        if review.version != expected_version:
            raise PaperReviewError("paper_review_version_conflict", 409)
        draft, _ = await self._validate(draft, paper, run, ai)
        previous = _draft(review.draft)
        changes = changed_domains(previous, draft)
        # An identical canonical replacement has no new persisted teacher edit.
        if not changes:
            return await self._response(review)
        await self._advance(review, expected_version, actor, draft, "reviewing")
        self._revision(
            review, ai, actor, expected_version, "human_edit", draft, changes
        )
        await self.db.flush()
        return await self._response(review)

    async def approve(self, paper_id, run_id, actor, command: PaperReviewApproval):
        paper, run, ai = await self._source(paper_id, run_id, lock=run_id is None)
        review = await self._review(paper_id, run_id, lock=True)
        if review.version != command.expected_version:
            raise PaperReviewError("paper_review_version_conflict", 409)
        draft, policy = await self._validate(_draft(review.draft), paper, run, ai)
        grade = approve_scores(policy, draft, command.grade_override)
        now = await self.db.scalar(select(func.clock_timestamp()))
        revision = self._revision(
            review,
            ai,
            actor,
            command.expected_version,
            "approved",
            draft,
            ("grade_override_intent",) if command.grade_override else (),
            grade,
            now,
        )
        await self.db.flush()
        await self._advance(
            review, command.expected_version, actor, draft, "approved", revision.id
        )
        if run_id is None:
            from app.application.paper_batch_orchestration import (
                PaperBatchOrchestrationService,
            )
            from app.infrastructure.paper_batch_orchestration_repository import (
                PaperBatchOrchestrationRepository,
            )

            await PaperBatchOrchestrationService(
                PaperBatchOrchestrationRepository(self.db)
            ).reconcile(paper.batch_id, actor)
        return await self._response(review)

    async def _advance(self, review, expected, actor, draft, state, pointer=None):
        values = {
            "draft": draft.model_dump(mode="json"),
            "state": state,
            "version": expected + 1,
            "updated_by_user_id": actor,
            "updated_at": func.clock_timestamp(),
        }
        if pointer is not None:
            values["latest_approved_revision_id"] = pointer
        result = await self.db.execute(
            update(PaperCheckReview)
            .where(
                PaperCheckReview.id == review.id, PaperCheckReview.version == expected
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise PaperReviewError("paper_review_version_conflict", 409)
        await self.db.refresh(review)

    async def history(self, paper_id, run_id):
        await self._source(paper_id, run_id)
        review = await self._review(paper_id, run_id)
        rows = (
            await self.db.scalars(
                select(PaperCheckReviewRevision)
                .where(PaperCheckReviewRevision.review_id == review.id)
                .order_by(PaperCheckReviewRevision.revision_number)
            )
        ).all()
        return {
            "review_id": review.id,
            "revisions": [self._revision_response(x) for x in rows],
        }

    def _revision_response(self, row):
        grade = (
            None
            if row.revision_kind != "approved"
            else PaperReviewGrade(
                **{
                    field: getattr(row, field)
                    for field in PaperReviewGrade.model_fields
                }
            )
        )
        return PaperReviewRevision(
            id=row.id,
            review_id=row.review_id,
            paper_submission_id=row.paper_submission_id,
            check_run_id=row.check_run_id,
            ai_revision_id=row.ai_revision_id,
            source_kind=row.source_kind,
            revision_number=row.revision_number,
            revision_kind=row.revision_kind,
            snapshot=_draft(row.snapshot),
            actor_user_id=row.actor_user_id,
            grading_policy_revision=row.grading_policy_revision,
            grading_policy_fingerprint=row.grading_policy_fingerprint,
            change_summary=tuple(row.change_summary),
            grade_result=grade,
            approved_at=row.approved_at,
            created_at=row.created_at,
        )

    async def _response(self, row):
        approved = (
            None
            if row.latest_approved_revision_id is None
            else await self.db.get(
                PaperCheckReviewRevision, row.latest_approved_revision_id
            )
        )
        return PaperReviewResponse(
            review_id=row.id,
            paper_submission_id=row.paper_submission_id,
            check_run_id=row.check_run_id,
            ai_revision_id=row.ai_revision_id,
            source_kind=row.source_kind,
            state=row.state,
            version=row.version,
            draft=_draft(row.draft),
            latest_approved_revision_id=row.latest_approved_revision_id,
            latest_approved_revision=self._revision_response(approved)
            if approved
            else None,
        )
