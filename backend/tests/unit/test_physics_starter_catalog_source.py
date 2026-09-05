"""Bridge contract for the canonicalized historical Physics starter."""

import json
from pathlib import Path


DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"


def test_physics_source_contains_only_the_canonicalized_historical_starter():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    physics = [subject for subject in source["subjects"] if subject["name"] == "Физика"]
    assert len(physics) == 1
    assert {grade["number"] for grade in physics[0]["grades"]} == {7}

    topics = physics[0]["grades"][0]["topics"]
    assert [topic["name"] for topic in topics] == ["Движение и взаимодействие тел"]
    assert "Механика" not in {topic["name"] for topic in topics}
    assert topics[0]["subtopics"] == [{
        "name": "Равномерное движение",
        "skills": ["Вычислять скорость", "Строить график движения"],
    }]
