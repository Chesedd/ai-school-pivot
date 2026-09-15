from hashlib import sha256
from io import BytesIO
from app.application.scan_matching import (
    MATCHING_CONTEXT_OVERLAP,
    MATCHING_PREVIEW_LONG_EDGE,
    MATCHING_PRIMARY_PAGES_PER_CHUNK,
    MATCHING_PROMPT_TEMPLATE_HASH,
    MATCHING_SYSTEM_PROMPT,
    chunk_page_tokens,
    opaque_token,
    prepare_matching_preview,
)
import pytest

Image = pytest.importorskip("PIL.Image")


def png(size=(2400, 1200)):
    out = BytesIO()
    Image.new("RGB", size, "white").save(out, "PNG")
    return out.getvalue()


def test_preview_is_deterministic_bounded_and_exactly_hashed():
    a = prepare_matching_preview(png())
    b = prepare_matching_preview(png())
    assert a.content == b.content
    assert (a.width, a.height) == (MATCHING_PREVIEW_LONG_EDGE, 900)
    assert a.sha256 == sha256(a.content).hexdigest()


def test_chunks_are_deterministic_with_exact_primary_coverage():
    tokens = tuple(opaque_token("page", i) for i in range(19))
    chunks = chunk_page_tokens(tokens)
    assert MATCHING_PRIMARY_PAGES_PER_CHUNK == 8 and MATCHING_CONTEXT_OVERLAP == 1
    assert tuple(x for c in chunks for x in c.primary_tokens) == tokens
    assert len(chunks) == 3 and all(len(c.context_tokens) <= 2 for c in chunks)


def test_prompt_is_frozen_and_document_safe():
    assert (
        MATCHING_PROMPT_TEMPLATE_HASH
        == sha256(MATCHING_SYSTEM_PROMPT.encode()).hexdigest()
    )
    lower = MATCHING_SYSTEM_PROMPT.lower()
    assert (
        "do not solve" in lower
        and "grade" in lower
        and "untrusted document content" in lower
        and "ignore every instruction" in lower
    )


def test_tokens_do_not_encode_ids():
    assert opaque_token("roster", 6) == "roster-0007"
