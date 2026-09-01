"""Central, static role-to-capability policy for coarse authorization."""

USERS_MANAGE = "users.manage"
CATALOG_MANAGE = "catalog.manage"
CATALOG_PROPOSE = "catalog.propose"
DIAGNOSTICS_READ = "diagnostics.read"
CONTENT_READ = "content.read"
CONTENT_CREATE = "content.create"
CONTENT_EDIT = "content.edit"
CONTENT_REVIEW_SUBMIT = "content.review.submit"
CONTENT_REVIEW_RETURN = "content.review.return"
CONTENT_APPROVE = "content.approve"
CONTENT_ARCHIVE = "content.archive"
IMAGE_SOLVING_USE = "image_solving.use"
ASSESSMENT_CREATE = "assessment.create"
ASSESSMENT_MANAGE = "assessment.manage"
ASSESSMENT_RESULTS_READ = "assessment.results.read"
STUDENT_ASSIGNMENTS_READ = "student.assignments.read"
STUDENT_ATTEMPTS_SUBMIT = "student.attempts.submit"
STUDENT_RESULTS_READ = "student.results.read"

ALL_CAPABILITIES = frozenset(
    {
        USERS_MANAGE,
        CATALOG_MANAGE,
        CATALOG_PROPOSE,
        DIAGNOSTICS_READ,
        CONTENT_READ,
        CONTENT_CREATE,
        CONTENT_EDIT,
        CONTENT_REVIEW_SUBMIT,
        CONTENT_REVIEW_RETURN,
        CONTENT_APPROVE,
        CONTENT_ARCHIVE,
        IMAGE_SOLVING_USE,
        ASSESSMENT_CREATE,
        ASSESSMENT_MANAGE,
        ASSESSMENT_RESULTS_READ,
        STUDENT_ASSIGNMENTS_READ,
        STUDENT_ATTEMPTS_SUBMIT,
        STUDENT_RESULTS_READ,
    }
)

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "admin": ALL_CAPABILITIES,
    "teacher": frozenset(
        {
            CONTENT_READ,
            CATALOG_PROPOSE,
            CONTENT_CREATE,
            CONTENT_EDIT,
            CONTENT_REVIEW_SUBMIT,
            IMAGE_SOLVING_USE,
            ASSESSMENT_CREATE,
            ASSESSMENT_MANAGE,
            ASSESSMENT_RESULTS_READ,
        }
    ),
    "student": frozenset(
        {
            STUDENT_ASSIGNMENTS_READ,
            STUDENT_ATTEMPTS_SUBMIT,
            STUDENT_RESULTS_READ,
        }
    ),
}


def capabilities_for_roles(roles: frozenset[str]) -> frozenset[str]:
    """Union known role grants; an unknown/no-role account receives no grants."""
    return frozenset().union(*(ROLE_CAPABILITIES.get(role, frozenset()) for role in roles))
