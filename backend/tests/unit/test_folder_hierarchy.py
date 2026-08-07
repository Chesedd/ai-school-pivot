from uuid import uuid4
import pytest
from app.application.folders import FolderDomainError, normalize_folder_name

@pytest.mark.parametrize(("raw","expected"),[("\u2003Алгебра\u2003","Алгебра"),("x","x"),("я"*120,"я"*120)])
def test_folder_name_unicode_trim_and_boundaries(raw,expected): assert normalize_folder_name(raw)==expected
@pytest.mark.parametrize("raw",["", " \t\n", "x"*121, ".", "..", "a/b", "a\\b"])
def test_invalid_folder_names_have_structured_contract_error(raw):
    with pytest.raises(FolderDomainError) as caught: normalize_folder_name(raw)
    assert caught.value.code=="folder_name_invalid"
    assert caught.value.status==422
    assert caught.value.details["field"]=="name"
