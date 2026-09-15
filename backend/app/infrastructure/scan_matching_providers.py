"""Anthropic vision adapter for page matching; bytes/SDK objects stay private."""

from __future__ import annotations
import base64
import json
from time import monotonic
from typing import Any, Mapping
from pydantic import ValidationError
from app.application.authoring import FailureCode, ProviderFailure
from app.application.scan_checking_contracts import (
    PageMatchingRequest,
    PageMatchingResponse,
    validate_matching_response,
)
from app.application.scan_matching import MATCHING_SYSTEM_PROMPT, MatchingTelemetry
from app.infrastructure.authoring_providers import _failure
from app.infrastructure.extraction_providers import _tool_input


class AnthropicScanPageMatchingProvider:
    provider_id = "anthropic"

    def __init__(self, client: Any, model_id: str):
        self._client = client
        self.model_id = model_id

    async def match(self, request: PageMatchingRequest, content: Mapping[str, bytes]):
        started = monotonic()
        try:
            blocks = []
            for page in request.pages:
                value = content.get(page.content.content_token)
                if not value or page.content.mime_type != "image/png":
                    raise ProviderFailure(FailureCode.MALFORMED_RESPONSE)
                blocks.extend(
                    (
                        {
                            "type": "text",
                            "text": f"Untrusted page document {page.page_token}, source order {page.source_order}:",
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(value).decode("ascii"),
                            },
                        },
                    )
                )
            safe = {
                "schema_version": request.schema_version,
                "batch_token": request.batch_token,
                "assessment": request.assessment.model_dump(mode="json"),
                "roster": [x.model_dump(mode="json") for x in request.roster],
                "pages": [
                    {
                        "page_token": p.page_token,
                        "source_order": p.source_order,
                        "content_token": p.content.content_token,
                        "content_sha256": p.content.content_sha256,
                    }
                    for p in request.pages
                ],
            }
            blocks.insert(
                0,
                {
                    "type": "text",
                    "text": "Matching request metadata:\n"
                    + json.dumps(safe, separators=(",", ":")),
                },
            )
            response = await self._client.messages.create(
                model=self.model_id,
                system=MATCHING_SYSTEM_PROMPT,
                max_tokens=8192,
                messages=[{"role": "user", "content": blocks}],
                tools=[
                    {
                        "name": "record_page_matching",
                        "description": "Record identity/grouping proposals only.",
                        "input_schema": PageMatchingResponse.model_json_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": "record_page_matching"},
            )
            result = PageMatchingResponse.model_validate_json(
                json.dumps(_tool_input(response, "record_page_matching"))
            )
            validate_matching_response(request, result)
            usage = response.usage
            telemetry = MatchingTelemetry(
                getattr(response, "id", None),
                getattr(usage, "input_tokens", 0),
                getattr(usage, "output_tokens", 0),
                getattr(usage, "cache_read_input_tokens", 0) or 0,
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
                max(0, int((monotonic() - started) * 1000)),
            )
            return result, telemetry
        except ProviderFailure:
            raise
        except (
            ValidationError,
            ValueError,
            TypeError,
            AttributeError,
            json.JSONDecodeError,
        ):
            raise ProviderFailure(FailureCode.MALFORMED_RESPONSE) from None
        except Exception as exc:
            raise _failure(exc) from None
