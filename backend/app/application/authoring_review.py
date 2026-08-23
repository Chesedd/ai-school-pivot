"""Controlled human review of immutable semantic authoring artifacts."""
from __future__ import annotations

import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator
from sqlalchemy import func, select, update

from app.application.authoring import AuthoringRequestV1
from app.application.authoring_pipeline import (
    ChoiceOptionV1, GeneratedTaskDraftV1, MAX_ANSWER, MAX_HINTS, MAX_SOLUTION,
    MAX_STATEMENT, MAX_TITLE,
)
from app.infrastructure.authoring_models import (AuthoringReview, AuthoringReviewAudit,
    AuthoringReviewRevision, AuthoringSession)

DIFF_FIELDS = (("title", "title"), ("statement", "statement"),
    ("choices", "choice_options"), ("expected_answer", "expected_answer"),
    ("solution", "solution"), ("hints", "hints"))


def revision_diff(previous: object, current: object) -> list[dict]:
    """Return changed reviewer fields in a stable, public order."""
    before, after = dict(previous), dict(current)
    return [{"field": public, "from": before.get(stored), "to": after.get(stored)}
        for public, stored in DIFF_FIELDS if before.get(stored) != after.get(stored)]


class AuthoringReviewDraftV1(BaseModel):
    """Bounded editable fields only; catalog and execution metadata are absent by design."""
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["authoring_review_draft.v1"] = "authoring_review_draft.v1"
    title: StrictStr | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    statement: StrictStr = Field(min_length=1, max_length=MAX_STATEMENT)
    task_type: Literal["test","calculation","problem","open_question","essay"]
    answer_format: Literal["single_choice","multiple_choice","short_text","number","expression","long_text"]
    choice_options: tuple[ChoiceOptionV1, ...] = Field(default=(), max_length=32)
    expected_answer: StrictStr = Field(min_length=1, max_length=MAX_ANSWER)
    solution: StrictStr = Field(min_length=1, max_length=MAX_SOLUTION)
    hints: tuple[StrictStr, ...] = Field(default=(), max_length=MAX_HINTS)

    @model_validator(mode="after")
    def generated_constraints(self):
        GeneratedTaskDraftV1.model_validate({**self.model_dump(), "schema_version":"generated_task_draft.v1"})
        return self

    @classmethod
    def from_generated(cls, value: object) -> "AuthoringReviewDraftV1":
        generated = GeneratedTaskDraftV1.model_validate(value)
        return cls.model_validate({**generated.model_dump(), "schema_version":"authoring_review_draft.v1"})


def review_dto(review: AuthoringReview) -> dict:
    draft = AuthoringReviewDraftV1.model_validate_json(json.dumps(review.draft)).model_dump()
    return {"schema_version":"authoring_review_response.v1", "session_id":review.session_id,
        "state":review.state, "version":review.version, "draft":draft,
        "created_at":review.created_at, "updated_at":review.updated_at}


class AuthoringReviewService:
    def __init__(self, db): self.db = db

    async def _session(self, session_id: UUID, owner_id: UUID, *, lock: bool = False):
        query = select(AuthoringSession).where(AuthoringSession.id == session_id,
            AuthoringSession.owner_id == owner_id)
        row = await self.db.scalar(query.with_for_update() if lock else query)
        if row is None: self._error("authoring_session_not_found", 404)
        return row

    async def _review(self, session_id: UUID, owner_id: UUID, *, lock: bool = False):
        query = select(AuthoringReview).where(AuthoringReview.session_id == session_id,
            AuthoringReview.owner_id == owner_id)
        review = await self.db.scalar(query.with_for_update() if lock else query)
        if review is None: self._error("authoring_review_not_started", 409)
        return review

    async def start(self, session_id: UUID, owner_id: UUID) -> dict:
        session = await self._session(session_id, owner_id, lock=True)
        existing = await self.db.scalar(select(AuthoringReview).where(AuthoringReview.session_id == session_id))
        if existing is not None:
            await self.db.commit()
            return review_dto(existing)
        if session.generated_draft is None or session.validation_result is None:
            self._error("authoring_review_not_ready", 409)
        draft = AuthoringReviewDraftV1.from_generated(session.generated_draft)
        review = AuthoringReview(id=uuid4(), session_id=session_id, owner_id=owner_id,
            state="reviewing", draft=draft.model_dump(mode="json"), version=1)
        self.db.add(review); await self.db.flush()
        revision = AuthoringReviewRevision(id=uuid4(), session_id=session_id, review_id=review.id,
            revision_number=0, snapshot=draft.model_dump(mode="json"), actor_id=owner_id,
            change_summary={"source":"ai_draft","changed_fields":[]})
        self.db.add(revision)
        self._audit(review, owner_id, "review_started")
        self._audit(review, owner_id, "review_revision_created", {"revision_id":str(revision.id),"revision_number":0})
        await self.db.commit(); await self.db.refresh(review)
        return review_dto(review)

    async def get(self, session_id: UUID, owner_id: UUID) -> dict:
        await self._session(session_id, owner_id)
        return review_dto(await self._review(session_id, owner_id))

    async def edit(self, session_id: UUID, owner_id: UUID, draft: AuthoringReviewDraftV1,
                   expected_version: int) -> dict:
        session = await self._session(session_id, owner_id)
        request = AuthoringRequestV1.model_validate(session.frozen_request)
        if (draft.task_type, draft.answer_format) != (request.task_type, request.answer_format):
            self._error("authoring_review_catalog_protected", 422)
        changed = await self.db.execute(update(AuthoringReview).where(
            AuthoringReview.session_id == session_id, AuthoringReview.owner_id == owner_id,
            AuthoringReview.state == "reviewing", AuthoringReview.version == expected_version,
        ).values(draft=draft.model_dump(mode="json"), version=AuthoringReview.version + 1,
            updated_at=func.clock_timestamp()).returning(AuthoringReview))
        review = changed.scalar_one_or_none()
        if review is None:
            current = await self.db.scalar(select(AuthoringReview).where(AuthoringReview.session_id == session_id))
            if current is None: self._error("authoring_review_not_started", 409)
            if current.state != "reviewing": self._error("authoring_review_not_editable", 409)
            self._error("authoring_review_version_conflict", 409)
        previous = await self.db.scalar(select(AuthoringReviewRevision).where(
            AuthoringReviewRevision.review_id == review.id).order_by(AuthoringReviewRevision.revision_number.desc()))
        number = previous.revision_number + 1
        changes = revision_diff(previous.snapshot, draft.model_dump(mode="json"))
        revision = AuthoringReviewRevision(id=uuid4(), session_id=session_id, review_id=review.id,
            revision_number=number, snapshot=draft.model_dump(mode="json"), actor_id=owner_id,
            change_summary={"source":"human_edit","changed_fields":[x["field"] for x in changes]})
        self.db.add(revision)
        self._audit(review, owner_id, "review_changed", {"previous_version":expected_version,"revision_number":number})
        self._audit(review, owner_id, "review_revision_created", {"revision_id":str(revision.id),"revision_number":number})
        await self.db.commit(); return review_dto(review)

    async def history(self, session_id: UUID, owner_id: UUID) -> dict:
        await self._session(session_id, owner_id)
        review = await self._review(session_id, owner_id)
        revisions = list((await self.db.scalars(select(AuthoringReviewRevision).where(
            AuthoringReviewRevision.review_id == review.id).order_by(AuthoringReviewRevision.revision_number))).all())
        return {"schema_version":"authoring_review_history.v1","session_id":session_id,
            "current_revision":revisions[-1].revision_number,
            "accepted_revision_id":review.accepted_revision_id,
            "revisions":[{"id":x.id,"revision_number":x.revision_number,"snapshot":x.snapshot,
                "created_at":x.created_at,"actor_id":x.actor_id,"change_summary":x.change_summary} for x in revisions]}

    async def diff(self, session_id: UUID, owner_id: UUID, from_revision: int | None,
                   to_revision: int | None) -> dict:
        await self._session(session_id, owner_id)
        review = await self._review(session_id, owner_id)
        revisions = list((await self.db.scalars(select(AuthoringReviewRevision).where(
            AuthoringReviewRevision.review_id == review.id).order_by(AuthoringReviewRevision.revision_number))).all())
        latest = revisions[-1].revision_number
        target, source = latest if to_revision is None else to_revision, from_revision
        if source is None: source = max(0, target - 1)
        by_number = {x.revision_number:x for x in revisions}
        if source not in by_number or target not in by_number:
            self._error("authoring_review_revision_not_found", 404)
        return {"schema_version":"authoring_review_diff.v1","session_id":session_id,
            "from_revision":source,"to_revision":target,
            "changes":revision_diff(by_number[source].snapshot, by_number[target].snapshot)}

    async def reject(self, session_id: UUID, owner_id: UUID, *, reason: str | None) -> dict:
        session = await self._session(session_id, owner_id, lock=True)
        review = await self._review(session_id, owner_id, lock=True)
        if review.state == "accepted": self._error("authoring_review_already_accepted", 409)
        if review.state == "rejected":
            await self.db.commit(); return review_dto(review)
        review.state = "rejected"; review.updated_at = func.clock_timestamp(); session.status = "rejected"
        self._audit(review, owner_id, "rejected", {"reason":reason} if reason else {})
        await self.db.commit(); await self.db.refresh(review); return review_dto(review)

    def _audit(self, review, actor_id, action, details=None):
        self.db.add(AuthoringReviewAudit(id=uuid4(), session_id=review.session_id,
            review_id=review.id, actor_id=actor_id, action=action, review_version=review.version,
            details=details or {}))

    @staticmethod
    def _error(code, status):
        from app.application.authoring_api import AuthoringApiError
        raise AuthoringApiError(code, status)
