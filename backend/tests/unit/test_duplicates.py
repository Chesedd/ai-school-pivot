from uuid import UUID

from app.application.content_bank import DuplicateCandidateRecord, DuplicatePolicy, DuplicateQuery

SKILL=UUID(int=10)
def row(n,similarity,statement="other",skill=None,answer=None):
    return DuplicateCandidateRecord(UUID(int=n),UUID(int=100+n),1,None,"draft",statement,similarity,skill,answer)
def evaluate(records,**kwargs):
    return DuplicatePolicy.evaluate(DuplicateQuery(kwargs.get("statement","Solve x + 1"),SKILL,kwargs.get("answer"),limit=kwargs.get("limit",5)),tuple(records))

def test_exact_statement_and_normalization():
    out=evaluate([row(1,.60,"  solve   X + 1  ")]); assert out[0].reasons[0]=="exact_statement"
def test_high_similarity():
    assert "high_statement_similarity" in evaluate([row(1,.85)])[0].reasons
def test_similarity_and_primary_skill():
    assert evaluate([row(1,.70,skill=SKILL)])[0].same_primary_skill
def test_similarity_and_final_answer():
    assert evaluate([row(1,.65,answer="  FOUR ")],answer="four")[0].same_final_answer
def test_answer_alone_does_not_match():
    assert not evaluate([row(1,.20,answer="four")],answer="four")
def test_deterministic_order_and_limit():
    out=evaluate([row(3,.90),row(2,.90,skill=SKILL),row(1,.60,"solve x + 1")],limit=2)
    assert [x.task_id.int for x in out]==[1,2]
