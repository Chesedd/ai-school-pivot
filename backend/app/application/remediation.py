"""Teacher-controlled remediation application boundary (no execution/providers)."""
import hashlib,json
from datetime import datetime,timezone
from uuid import UUID
class RemediationError(Exception):
 def __init__(self,code,status=409): self.code=code; self.status=status; super().__init__(code)
def fingerprint(payload:dict)->str:
 return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def validate_item_ids(items):
 ids=[str(x.task_version_id) for x in items]
 if len(ids)>20: raise RemediationError("remediation_too_many_items",422)
 if len(ids)!=len(set(ids)): raise RemediationError("remediation_duplicate_task",422)
def utcnow(): return datetime.now(timezone.utc)
