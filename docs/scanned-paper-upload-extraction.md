# Scanned-paper upload and extraction

Scan intake keeps every original upload byte-for-byte in private artifact storage.  Each
image, or each logical PDF page, is separately rendered as a metadata-free PNG and stored
as another immutable `InputArtifact`.  Page APIs only resolve these derived PNG artifacts.

The runtime uses Pillow 11.3.0 for strict image decoding, EXIF orientation, resizing, and
PNG encoding.  It uses pypdfium2 4.30.0 for deterministic in-process PDF rendering at
200 DPI; no Poppler executable is required.

Resource limits are deliberately centralized in `scan_page_rasterizer.py`: 200 PDF pages,
20,000 pixels on a source-image edge, 5,000 pixels on a normalized edge, 25 million pixels
per page, and 25 MiB per normalized PNG.  Original uploads retain the existing 25 MiB cap.
Malformed, encrypted, and deterministically oversized inputs are terminal. Unexpected
page rendering or storage failures are retryable. Ready pages are never recreated on a
retry, and each page commit is independent, so earlier pages survive a later-page failure.

The database schema from migration `20260914_03` already supports the pipeline; this
change requires no migration.
