"""Read-only context for assigned paper teachers; authoring scopes stay intact."""

from sqlalchemy import select

from app.application.classroom_results import ResultActor
from app.application.object_access import ObjectAccessScope
from app.infrastructure.assessment_models import Assignment
from app.infrastructure.assessment_repository import SQLAlchemyAssessmentRepository
from app.infrastructure.classroom_repository import SQLAlchemyClassroomAccessRepository
from app.infrastructure.classroom_results_repository import (
    SQLAlchemyClassroomResultsReadRepository,
)


class PaperTeacherContextRepository:
    def __init__(self, session, scope: ObjectAccessScope):
        self.session = session
        self.scope = scope
        self.access = SQLAlchemyClassroomAccessRepository(session)
        self.assessments = SQLAlchemyAssessmentRepository(session)

    async def assignment(self, assignment_id):
        row = await self.session.get(Assignment, assignment_id)
        if row is None or not await self.access.can_access_class(
            row.class_group_id, self.scope.actor_id, self.scope.unrestricted
        ):
            return None
        return await self.assessments.get_assignment(assignment_id)

    async def assessment(self, assessment_id):
        # Preserve owner/Admin reads, including unassigned authoring drafts.
        row = await self.assessments.get_scoped(assessment_id, self.scope)
        if row is not None:
            return row
        class_ids = await self.access.accessible_class_ids(
            self.scope.actor_id, self.scope.unrestricted
        )
        assignment_id = await self.session.scalar(
            select(Assignment.id).where(
                Assignment.assessment_id == assessment_id,
                Assignment.class_group_id.in_(class_ids),
            ).limit(1)
        )
        if assignment_id is None:
            return None
        return await self.assessments.get(assessment_id)

    async def assignment_results(self, assignment_id, offset, limit):
        if await self.assignment(assignment_id) is None:
            return None
        # Only this authorized assignment projection bypasses assessment ownership.
        # Detailed student results and history keep their existing owner scopes.
        return await SQLAlchemyClassroomResultsReadRepository(self.session).assignment_results(
            assignment_id, ResultActor(self.scope.actor_id, True), offset, limit
        )
