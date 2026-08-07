from uuid import uuid4
import pytest
from app.application.folders import FolderDomainError, normalize_folder_name
from app.infrastructure.repository import SQLAlchemyContentBankRepository
from sqlalchemy import String


async def test_subject_tree_advisory_lock_binds_uuid_as_explicit_text():
    subject_id = uuid4()
    calls = []
    class Session:
        async def execute(self, statement, parameters): calls.append((statement, parameters))
    await SQLAlchemyContentBankRepository(Session()).lock_subject_tree(subject_id)
    statement, parameters = calls[0]
    sql = str(statement)
    assert parameters == {"id": str(subject_id)}
    assert type(parameters["id"]) is str
    assert isinstance(statement._bindparams["id"].type, String)
    assert "pg_advisory_xact_lock" in sql
    assert "digest(CAST(:id AS text),'sha256')" in sql
    assert "hash(" not in sql

@pytest.mark.parametrize(("raw","expected"),[("\u2003Алгебра\u2003","Алгебра"),("x","x"),("я"*120,"я"*120)])
def test_folder_name_unicode_trim_and_boundaries(raw,expected): assert normalize_folder_name(raw)==expected
@pytest.mark.parametrize("raw",["", " \t\n", "x"*121, ".", "..", "a/b", "a\\b"])
def test_invalid_folder_names_have_structured_contract_error(raw):
    with pytest.raises(FolderDomainError) as caught: normalize_folder_name(raw)
    assert caught.value.code=="folder_name_invalid"
    assert caught.value.status==422
    assert caught.value.details["field"]=="name"
