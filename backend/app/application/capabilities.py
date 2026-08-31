"""Central, static role-to-capability policy for coarse authorization."""

ALL_CAPABILITIES = frozenset(
    {
        "users.manage",
        "catalog.manage",
        "diagnostics.read",
        "content.read",
        "content.create",
        "content.edit",
        "content.review.submit",
        "content.review.return",
        "content.approve",
        "content.archive",
        "image_solving.use",
        "assessment.create",
        "assessment.manage",
        "assessment.results.read",
        "student.assignments.read",
        "student.attempts.submit",
        "student.results.read",
    }
)

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "admin": ALL_CAPABILITIES,
    "teacher": frozenset(
        {
            "content.read",
            "content.create",
            "content.edit",
            "content.review.submit",
            "image_solving.use",
            "assessment.create",
            "assessment.manage",
            "assessment.results.read",
        }
    ),
    "student": frozenset(
        {
            "student.assignments.read",
            "student.attempts.submit",
            "student.results.read",
        }
    ),
}


def capabilities_for_roles(roles: frozenset[str]) -> frozenset[str]:
    """Union known role grants; an unknown/no-role account receives no grants."""
    return frozenset().union(*(ROLE_CAPABILITIES.get(role, frozenset()) for role in roles))
