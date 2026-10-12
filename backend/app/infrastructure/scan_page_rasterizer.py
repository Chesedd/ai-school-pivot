"""Bounded Pillow/PDFium implementation of scan page normalization.

Runtime dependencies: Pillow decodes and produces deterministic, metadata-free PNGs;
pypdfium2 rasterizes PDFs in process without a Poppler executable.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Protocol

from app.application.paper_operational_limits import (
    MAX_SECURITY_PAGE_PIXELS,
    MAX_SECURITY_PDF_PAGES,
    PaperOperationalLimits,
)

PDF_DPI = 200
MAX_PDF_PAGES = MAX_SECURITY_PDF_PAGES
MAX_SOURCE_LONG_EDGE = 20_000
MAX_NORMALIZED_LONG_EDGE = 5_000
MAX_PAGE_PIXELS = MAX_SECURITY_PAGE_PIXELS
MAX_NORMALIZED_PNG_BYTES = 25 * 1024 * 1024


class RasterizationError(ValueError):
    """Stable, disclosure-safe render failure."""

    def __init__(self, code: str, *, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NormalizedPage:
    source_page_index: int
    png_bytes: bytes
    width_px: int
    height_px: int
    content_hash_sha256: str


class PageRasterizer(Protocol):
    def rasterize(self, content: bytes, mime_type: str) -> Iterator[NormalizedPage]: ...


def _png(image, index: int, limits=None) -> NormalizedPage:
    from PIL import Image

    if image.width <= 0 or image.height <= 0:
        raise RasterizationError("normalized_page_too_large")
    if (
        image.width > MAX_NORMALIZED_LONG_EDGE
        or image.height > MAX_NORMALIZED_LONG_EDGE
    ):
        image.thumbnail(
            (MAX_NORMALIZED_LONG_EDGE, MAX_NORMALIZED_LONG_EDGE),
            Image.Resampling.LANCZOS,
        )
    if image.width * image.height > (limits or PaperOperationalLimits()).max_pixels_per_page:
        raise RasterizationError("normalized_page_too_large")
    # RGB/RGBA/L are browser-safe and avoid mode-specific metadata/palette behavior.
    if image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGB")
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    data = output.getvalue()
    if len(data) > MAX_NORMALIZED_PNG_BYTES:
        raise RasterizationError("normalized_page_too_large")
    return NormalizedPage(
        index, data, image.width, image.height, sha256(data).hexdigest()
    )


class PillowPdfiumPageRasterizer:
    def __init__(self, limits=None):
        self.limits = limits or PaperOperationalLimits()

    def page_count(self, content: bytes, mime_type: str) -> int:
        if mime_type != "application/pdf":
            return 1
        document = self._document(content)
        try:
            count = len(document)
            if count < 1:
                raise RasterizationError("invalid_pdf")
            self.limits.guard("max_pages_per_artifact", count,
                "scan_artifact_page_limit_exceeded", RasterizationError)
            return count
        finally:
            document.close()

    def rasterize(self, content: bytes, mime_type: str) -> Iterator[NormalizedPage]:
        if mime_type == "application/pdf":
            return self._pdf(content)
        if mime_type in {"image/png", "image/jpeg", "image/webp"}:
            return self._image(content, mime_type)
        raise RasterizationError("unsupported_artifact_type")

    def _image(self, content: bytes, mime_type: str) -> Iterator[NormalizedPage]:
        from PIL import Image, ImageFile, ImageOps

        ImageFile.LOAD_TRUNCATED_IMAGES = False
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as opened:
                    formats = {
                        "image/png": "PNG",
                        "image/jpeg": "JPEG",
                        "image/webp": "WEBP",
                    }
                    if opened.format != formats[mime_type]:
                        raise RasterizationError("invalid_image")
                    if (
                        max(opened.size) > MAX_SOURCE_LONG_EDGE
                        or opened.width * opened.height > self.limits.max_pixels_per_page
                    ):
                        raise RasterizationError("image_too_large")
                    opened.load()
                    upright = ImageOps.exif_transpose(opened)
                    upright.load()
                    page = _png(upright, 0, self.limits)
        except RasterizationError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise RasterizationError("image_too_large") from None
        except Exception:  # noqa: BLE001 - Pillow plugins have diverse decoding exceptions.
            raise RasterizationError("invalid_image") from None
        return iter((page,))

    def _document(self, content: bytes):
        try:
            import pypdfium2 as pdfium

            return pdfium.PdfDocument(content)
        except Exception as exc:  # noqa: BLE001 - PDFium errors vary across builds.
            # PDFium reports password/security failures differently across builds.
            message = type(exc).__name__.lower() + " " + str(exc).lower()
            code = (
                "encrypted_pdf"
                if "password" in message or "security" in message
                else "invalid_pdf"
            )
            raise RasterizationError(code) from None
    def _pdf(self, content: bytes) -> Iterator[NormalizedPage]:
        self.page_count(content, "application/pdf")
        document = self._document(content)
        count = len(document)
        if count < 1:
            document.close()
            raise RasterizationError("invalid_pdf")
        if count > MAX_PDF_PAGES:
            document.close()
            raise RasterizationError("pdf_page_limit_exceeded")

        def pages() -> Iterator[NormalizedPage]:
            try:
                for index in range(count):
                    try:
                        page = document[index]
                        width_pt, height_pt = page.get_size()
                        target_w = round(width_pt * PDF_DPI / 72)
                        target_h = round(height_pt * PDF_DPI / 72)
                        if (
                            width_pt <= 0
                            or height_pt <= 0
                            or target_w > MAX_NORMALIZED_LONG_EDGE
                            or target_h > MAX_NORMALIZED_LONG_EDGE
                            or target_w * target_h > self.limits.max_pixels_per_page
                        ):
                            raise RasterizationError("pdf_page_too_large")
                        bitmap = page.render(scale=PDF_DPI / 72, rotation=0)
                        image = bitmap.to_pil()
                        yield _png(image, index, self.limits)
                    except RasterizationError:
                        raise
                    except Exception:  # noqa: BLE001 - native rendering boundary.
                        raise RasterizationError(
                            "page_render_failed", retryable=True
                        ) from None
                    finally:
                        for item in (locals().get("bitmap"), locals().get("page")):
                            close = getattr(item, "close", None)
                            if close:
                                close()
            finally:
                document.close()

        return pages()
