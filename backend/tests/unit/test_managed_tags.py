import pytest
from app.application.managed_tags import TagError, canonicalize_tag_name, normalize_tag_name

@pytest.mark.parametrize(("raw","expected"),[("ОГЭ","огэ"),("огэ","огэ"),("  ОГЭ  ","огэ"),("Для\u00a0  группы","для группы"),("Всё","все"),("все","все"),("ＡＢＣ","abc")])
def test_normalization(raw,expected): assert normalize_tag_name(raw)==expected

def test_display_preserves_case_and_yo(): assert canonicalize_tag_name("  Всё\u2003 Хорошо ")=="Всё Хорошо"

def test_length_boundary():
    assert len(canonicalize_tag_name("я"*80))==80
    with pytest.raises(TagError): canonicalize_tag_name("я"*81)

@pytest.mark.parametrize("value",["", " ; ", "... —", "ok\x00", "ok\u200b"])
def test_invalid_names(value):
    with pytest.raises(TagError) as exc: canonicalize_tag_name(value)
    assert exc.value.code=="tag_name_invalid"
