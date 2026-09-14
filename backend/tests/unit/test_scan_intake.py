from dataclasses import fields

import pytest

from app.application.scan_intake import (
    ArtifactExtractionStatus,
    ScanIntakeError,
    ScanPageRecord,
    ScanPageStatus,
    canonical_request_hash,
    validate_artifact_transition,
    validate_event_details,
    validate_page_metadata,
    validate_page_transition,
)


def test_request_hash_is_canonical_and_sensitive_to_request():
    from uuid import UUID

    assignment = UUID("00000000-0000-0000-0000-000000000001")
    assert canonical_request_hash(assignment, "Check") == canonical_request_hash(
        assignment, "Check"
    )
    assert canonical_request_hash(assignment, "Check") != canonical_request_hash(
        assignment, "Check all"
    )


def test_ready_page_uses_upright_dimensions_and_fingerprint():
    validate_page_metadata(
        source_page_index=0,
        width_px=1200,
        height_px=1600,
        rotation_degrees=0,
        coordinate_space_version="normalized_upright_v1",
        content_fingerprint="a" * 64,
        status=ScanPageStatus.READY,
    )
    with pytest.raises(ScanIntakeError, match="invalid_ready_page"):
        validate_page_metadata(
            source_page_index=0,
            width_px=None,
            height_px=1600,
            rotation_degrees=0,
            coordinate_space_version="normalized_upright_v1",
            content_fingerprint="a" * 64,
            status=ScanPageStatus.READY,
        )


@pytest.mark.parametrize("rotation", [-90, 1, 360])
def test_rotation_is_quarter_turn(rotation):
    with pytest.raises(ScanIntakeError, match="invalid_page_rotation"):
        validate_page_metadata(
            source_page_index=0,
            width_px=None,
            height_px=None,
            rotation_degrees=rotation,
            coordinate_space_version=None,
            content_fingerprint=None,
            status=ScanPageStatus.PENDING,
        )


def test_explicit_status_machines_reject_skips_and_terminal_changes():
    validate_artifact_transition(
        ArtifactExtractionStatus.PENDING, ArtifactExtractionStatus.RUNNING
    )
    with pytest.raises(ScanIntakeError):
        validate_artifact_transition(
            ArtifactExtractionStatus.PENDING, ArtifactExtractionStatus.COMPLETED
        )
    validate_page_transition(ScanPageStatus.PENDING, ScanPageStatus.READY)
    with pytest.raises(ScanIntakeError):
        validate_page_transition(ScanPageStatus.READY, ScanPageStatus.FAILED_TERMINAL)


def test_audit_details_reject_sensitive_keys_recursively():
    validate_event_details({"from": "pending", "to": "ready"})
    with pytest.raises(ScanIntakeError, match="unsafe_event_details"):
        validate_event_details({"nested": {"storage_reference": "secret"}})


def test_page_record_does_not_expose_storage_reference():
    assert "storage_reference" not in {field.name for field in fields(ScanPageRecord)}
