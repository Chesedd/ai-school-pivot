"""Anthropic vision adapter for anonymous paper checking."""
# ruff: noqa: E501, E701, E702
from __future__ import annotations
import base64
import json
from time import monotonic
from typing import Any, Mapping
from pydantic import ValidationError
from app.application.paper_checking_execution import PAPER_CHECK_SYSTEM_PROMPT, PaperCheckingExecutionError, PaperCheckingTelemetry
from app.application.scan_checking_contracts import PaperCheckRequest, PaperCheckResponse, validate_checking_response
from app.infrastructure.extraction_providers import _tool_input


class AnthropicPaperCheckingProvider:
    provider_id="anthropic"
    def __init__(self,client:Any,model_id:str): self._client,self.model_id=client,model_id
    async def check(self,request:PaperCheckRequest,content:Mapping[str,bytes]):
        started=monotonic(); blocks=[]
        safe=request.model_dump(mode="json")
        blocks.append({"type":"text","text":"Frozen anonymous checking metadata:\n"+json.dumps(safe,separators=(",",":"))})
        for page in request.pages:
            raw=content.get(page.content.content_token)
            if raw is None: raise PaperCheckingExecutionError("paper_render_missing")
            blocks.extend(({"type":"text","text":f"Untrusted page {page.page_token}:"},{"type":"image","source":{"type":"base64","media_type":"image/png","data":base64.b64encode(raw).decode("ascii")}}))
        try:
            response=await self._client.messages.create(model=self.model_id,system=PAPER_CHECK_SYSTEM_PROMPT,
                max_tokens=8192,temperature=0,messages=[{"role":"user","content":blocks}],
                tools=[{"name":"record_paper_check","description":"Record item checking evidence only.","input_schema":PaperCheckResponse.model_json_schema()}],
                tool_choice={"type":"tool","name":"record_paper_check"})
            result=PaperCheckResponse.model_validate_json(json.dumps(_tool_input(response,"record_paper_check"))); validate_checking_response(request,result)
            usage=response.usage
            return result,PaperCheckingTelemetry(getattr(response,"id",None),getattr(usage,"input_tokens",0),
                getattr(usage,"output_tokens",0),getattr(usage,"cache_read_input_tokens",0) or 0,max(0,int((monotonic()-started)*1000)))
        except PaperCheckingExecutionError: raise
        except (ValidationError,ValueError,TypeError,AttributeError,json.JSONDecodeError):
            raise PaperCheckingExecutionError("paper_response_invalid") from None
        except Exception: raise PaperCheckingExecutionError("paper_provider_failed") from None
