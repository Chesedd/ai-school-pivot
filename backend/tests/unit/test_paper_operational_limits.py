"""Exact operational boundaries, startup validation and pre-materialization guards."""
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from app.application.paper_checking_execution import (
    PaperCheckingExecutionError,
    compile_paper_request,
)
from app.application.paper_operational_limits import PaperOperationalLimits
from app.config import Settings
from app.infrastructure.scan_page_rasterizer import (
    PillowPdfiumPageRasterizer,
    RasterizationError,
)
from tests.unit.test_paper_checking_execution import snapshot


@pytest.mark.parametrize("field", list(PaperOperationalLimits.model_fields))
def test_each_default_is_positive_and_exact_boundary_is_inclusive(field):
    limits = PaperOperationalLimits()
    maximum = getattr(limits, field)
    assert maximum > 0
    limits.guard(field, maximum, "limit", ValueError)
    with pytest.raises(ValueError, match="limit"):
        limits.guard(field, maximum + 1, "limit", ValueError)


@pytest.mark.parametrize("field", list(PaperOperationalLimits.model_fields))
@pytest.mark.parametrize("value", [0, -1])
def test_each_limit_rejects_invalid_startup_values(field, value):
    with pytest.raises(ValidationError):
        PaperOperationalLimits(**{field: value})


@pytest.mark.parametrize("values", [
    {"max_single_artifact_bytes": 26 * 1024 * 1024},
    {"max_pixels_per_page": 25_000_001},
    {"max_provider_pages_per_paper": 21},
    {"max_total_upload_bytes_per_batch": 1},
    {"max_pages_per_batch": 99},
    {"max_parallel_matching_runs": 2},
    {"execution_lease_seconds": 30},
])
def test_impossible_or_weakened_security_configuration_fails(values):
    with pytest.raises(ValidationError):
        PaperOperationalLimits(**values)


def test_normal_environment_configuration_validates_at_settings_startup(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_PARALLEL_PAPER_CHECKS_PER_BATCH", "4")
    settings = Settings(database_url="postgresql+asyncpg://localhost/example_test")
    assert settings.paper_operational_limits.max_parallel_paper_checks_per_batch == 4
    monkeypatch.setenv("PAPER_MAX_PARALLEL_PAPER_CHECKS_PER_BATCH", "0")
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+asyncpg://localhost/example_test")


def test_provider_page_rejection_precedes_descriptor_materialization_and_never_truncates():
    source = snapshot()
    limits = PaperOperationalLimits(max_provider_pages_per_paper=1)
    assert len(compile_paper_request(source, provider_id="fake", model_id="fake", limits=limits).request.pages) == 1
    source["pages"].append(deepcopy(source["pages"][0]))
    with pytest.raises(PaperCheckingExecutionError, match="paper_check_provider_page_limit_exceeded"):
        compile_paper_request(source, provider_id="fake", model_id="fake", limits=limits)
    assert len(source["pages"]) == 2


@pytest.mark.parametrize("size,accepted", [((2, 2), True), ((5, 1), False)])
def test_source_pixels_checked_before_normalization(size, accepted):
    output = BytesIO()
    Image.new("RGB", size).save(output, format="PNG")
    rasterizer = PillowPdfiumPageRasterizer(PaperOperationalLimits(max_pixels_per_page=4))
    if accepted:
        assert len(list(rasterizer.rasterize(output.getvalue(), "image/png"))) == 1
    else:
        with pytest.raises(RasterizationError, match="image_too_large"):
            rasterizer.rasterize(output.getvalue(), "image/png")


def test_pdf_page_count_rejects_before_render(monkeypatch):
    from tests.integration.test_paper_checking_execution_database import source_pdf
    limits = PaperOperationalLimits(max_pages_per_artifact=2)
    assert PillowPdfiumPageRasterizer(limits).page_count(source_pdf(), "application/pdf") == 2
    rasterizer = PillowPdfiumPageRasterizer(PaperOperationalLimits(max_pages_per_artifact=1))
    with pytest.raises(RasterizationError, match="scan_artifact_page_limit_exceeded"):
        rasterizer.rasterize(source_pdf(), "application/pdf")


@pytest.mark.parametrize("extra", [0, 1])
async def test_streamed_upload_stops_at_byte_limit_and_closes_spool(monkeypatch, extra):
    from tempfile import SpooledTemporaryFile

    import starlette.formparsers
    from starlette.datastructures import Headers
    from starlette.formparsers import MultiPartException

    from app.presentation.scan_upload_form import BoundedScanMultipartParser

    spools = []
    def spool(**kwargs):
        value = SpooledTemporaryFile(**kwargs)  # noqa: SIM115 - parser owns and closes this spool.
        spools.append(value)
        return value
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", spool)
    async def stream():
        yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="paper.png"\r\nContent-Type: image/png\r\n\r\n'
        yield b"abcd"
        if extra:
            yield b"e"
        yield b'\r\n--boundary\r\nContent-Disposition: form-data; name="upload_position"\r\n\r\n0\r\n--boundary--\r\n'
    parser = BoundedScanMultipartParser(Headers({"content-type": "multipart/form-data; boundary=boundary"}), stream(), 4)
    if extra:
        with pytest.raises(MultiPartException, match="scan_artifact_too_large"):
            await parser.parse()
    else:
        form = await parser.parse()
        assert await form["file"].read() == b"abcd"
        await form.close()
    assert len(spools) == 1 and spools[0].closed
