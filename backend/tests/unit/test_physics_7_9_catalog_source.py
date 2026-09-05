"""Source contract for the canonical Physics grades 7–9 curriculum."""
import json
from pathlib import Path

from app.infrastructure.models import normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"
TOPICS = {
    7: {"Физика и её роль в познании окружающего мира", "Первоначальные сведения о строении вещества", "Движение и взаимодействие тел", "Давление твёрдых тел, жидкостей и газов", "Работа и мощность. Энергия"},
    8: {"Тепловые явления", "Электрические и магнитные явления"},
    9: {"Механические явления", "Механические колебания и волны", "Электромагнитное поле и электромагнитные волны", "Световые явления", "Квантовые явления"},
}
COUNTS = {7: (5, 120, 235), 8: (2, 97, 192), 9: (5, 154, 301)}
REPRESENTATIVE = {
  7: {"Физика и её роль в познании окружающего мира":{"Цена деления прибора"}, "Движение и взаимодействие тел":{"Равномерное движение","Плотность вещества","Закон Гука"}, "Давление твёрдых тел, жидкостей и газов":{"Закон Паскаля","Закон Архимеда"}, "Работа и мощность. Энергия":{"КПД простого механизма"}},
  8: {"Тепловые явления":{"Уравнение теплового баланса","Удельная теплота парообразования"}, "Электрические и магнитные явления":{"Закон Ома для участка цепи","Последовательное соединение проводников","Электромагнитная индукция"}},
  9: {"Механические явления":{"Второй закон Ньютона","Закон сохранения импульса"}, "Механические колебания и волны":{"Резонанс"}, "Электромагнитное поле и электромагнитные волны":{"Шкала электромагнитных волн"}, "Световые явления":{"Закон преломления света","Оптическая сила линзы"}, "Квантовые явления":{"Период полураспада","Ядерная реакция"}},
}

def unique(names):
    values=[normalize_catalog_name(n) for n in names]
    assert len(values)==len(set(values))

def test_physics_source_contract_and_normalized_uniqueness():
    source=json.loads(DATA.read_text(encoding="utf-8"))
    matches=[s for s in source["subjects"] if s["name"]=="Физика"]
    assert len(matches)==1
    grades={g["number"]:g for g in matches[0]["grades"]}
    assert set(grades)=={7,8,9}
    for number, expected in TOPICS.items():
        topics=grades[number]["topics"]
        assert {t["name"] for t in topics}==expected
        assert (len(topics),sum(len(t["subtopics"]) for t in topics),sum(len(s["skills"]) for t in topics for s in t["subtopics"]))==COUNTS[number]
        unique(t["name"] for t in topics)
        by_name={t["name"]:t for t in topics}
        for topic in topics:
            unique(s["name"] for s in topic["subtopics"])
            for subtopic in topic["subtopics"]:
                assert subtopic["skills"]
                unique(subtopic["skills"])
        for topic,names in REPRESENTATIVE[number].items():
            assert names <= {s["name"] for s in by_name[topic]["subtopics"]}

def test_historical_starter_is_preserved_exactly_and_boundaries_hold():
    source=json.loads(DATA.read_text(encoding="utf-8"))
    physics=next(s for s in source["subjects"] if s["name"]=="Физика")
    grades={g["number"]:g for g in physics["grades"]}
    topic=next(t for t in grades[7]["topics"] if t["name"]=="Движение и взаимодействие тел")
    uniform=[s for s in topic["subtopics"] if s["name"]=="Равномерное движение"]
    assert uniform==[{"name":"Равномерное движение","skills":["Вычислять скорость","Строить график движения"]}]
    payload={n:json.dumps(grades[n],ensure_ascii=False).casefold() for n in grades}
    assert "механика" not in {t["name"].casefold() for t in grades[7]["topics"]}
    assert "повторительно-обобщающий модуль" not in "".join(payload.values())
    assert all(x not in payload[7] for x in ("закон ома для участка цепи","второй закон ньютона","период полураспада"))
    assert all(x not in payload[8] for x in ("второй закон ньютона","закон преломления света","период полураспада"))
    assert all(x not in "".join(payload.values()) for x in ("подготовка к огэ","задание 17 огэ","уравнение шрёдингера","волновая функция"))
