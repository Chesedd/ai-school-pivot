"""Bound file bytes during the existing Starlette multipart streaming parser."""
from contextlib import asynccontextmanager

from fastapi import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser


class BoundedScanMultipartParser(MultiPartParser):
    def __init__(self, headers, stream, max_file_bytes):
        super().__init__(headers, stream, max_files=1, max_fields=1)
        self.max_file_bytes = max_file_bytes
        self.file_bytes = 0

    def on_part_begin(self):
        super().on_part_begin()
        self.file_bytes = 0

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > self.max_file_bytes:
                # Starlette closes every spool on MultiPartException, including
                # spools from earlier parts, before propagating this rejection.
                raise MultiPartException("scan_artifact_too_large")
        super().on_part_data(data, start, end)


@asynccontextmanager
async def scan_upload_form(request, limits):
    parser = BoundedScanMultipartParser(request.headers, request.stream(), limits.max_single_artifact_bytes)
    try:
        form = await parser.parse()
    except MultiPartException as exc:
        code = "scan_artifact_too_large" if exc.message == "scan_artifact_too_large" else "invalid_upload_form"
        raise HTTPException(413 if code == "scan_artifact_too_large" else 422, code) from None
    except BaseException:
        # Disconnect/cancellation also closes any partially received file.
        for spool in parser._files_to_close_on_error:
            spool.close()
        raise
    try:
        yield form
    finally:
        await form.close()
