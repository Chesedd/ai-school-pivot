"""Idempotently install a curated, versioned Russian school starter catalog."""
import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.db.session import async_session_factory
from app.infrastructure.catalog_lifecycle import (LIVE_CATALOG_STATUSES,
    resolve_effective_catalog_target)
from app.infrastructure.models import Grade, Skill, Subject, Subtopic, Topic, normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"


def _code(prefix: str, name: str) -> str:
    """Produce a stable code whose uniqueness matches the model's scoped key."""
    digest = hashlib.sha1(normalize_catalog_name(name).encode()).hexdigest()[:16]
    return f"seed-{prefix}-{digest}"


async def _existing_or_conflict(db, model, clauses, compatible):
    """Prefer a live identity, otherwise resolve its deprecated history safely."""
    rows = (await db.scalars(select(model).where(*clauses).order_by(model.id))).all()
    for row in rows:
        if row.status in LIVE_CATALOG_STATUSES and compatible(row):
            return row, False
    if not rows:
        return None, False
    for row in rows:
        if row.status == "deprecated":
            target = await resolve_effective_catalog_target(db, model, row.id)
            if target is not None and compatible(target):
                return target, False
    # A deliberately retired identity, broken chain, or cross-scope replacement
    # is operator-owned state. The seed never resurrects or repairs it.
    return None, True


def _descendant_conflicts(report, grade_data):
    for topic in grade_data["topics"]:
        report["topics"]["conflicts"] += 1
        for subtopic in topic["subtopics"]:
            report["subtopics"]["conflicts"] += 1
            report["skills"]["conflicts"] += len(subtopic["skills"])


async def seed_catalog(path: Path = DATA, *, session_factory=None) -> dict:
    """Apply the dataset without changing any pre-existing catalog row."""
    data = json.loads(path.read_text(encoding="utf-8"))
    report = defaultdict(lambda: {"created": 0, "reused": 0, "conflicts": 0})
    factory = session_factory or async_session_factory
    async with factory() as db:
        async with db.begin():
            grades = {}
            for number in range(1, 12):
                row, conflict = await _existing_or_conflict(
                    db, Grade, (Grade.number == number,), lambda target, n=number: target.number == n)
                if row:
                    report["grades"]["reused"] += 1
                elif conflict:
                    report["grades"]["conflicts"] += 1
                    continue
                else:
                    name = f"{number} класс"
                    row = Grade(number=number, name=name,
                                normalized_name=normalize_catalog_name(name), status="active")
                    db.add(row)
                    await db.flush()
                    report["grades"]["created"] += 1
                grades[number] = row

            for subject_data in data["subjects"]:
                norm = normalize_catalog_name(subject_data["name"])
                subject, conflict = await _existing_or_conflict(
                    db, Subject, (Subject.normalized_name == norm,), lambda _: True)
                if subject:
                    report["subjects"]["reused"] += 1
                elif conflict:
                    report["subjects"]["conflicts"] += 1
                    for grade_data in subject_data["grades"]:
                        _descendant_conflicts(report, grade_data)
                    continue
                else:
                    subject = Subject(code=_code("subject", norm), name=subject_data["name"],
                                      normalized_name=norm, status="active")
                    db.add(subject)
                    await db.flush()
                    report["subjects"]["created"] += 1

                for grade_data in subject_data["grades"]:
                    grade = grades.get(grade_data["number"])
                    if grade is None:
                        _descendant_conflicts(report, grade_data)
                        continue
                    for topic_data in grade_data["topics"]:
                        topic_norm = normalize_catalog_name(topic_data["name"])
                        scope = (Topic.subject_id == subject.id, Topic.grade_id == grade.id)
                        topic, conflict = await _existing_or_conflict(
                            db, Topic, (*scope, Topic.normalized_name == topic_norm),
                            lambda target, s=subject.id, g=grade.id:
                                target.subject_id == s and target.grade_id == g)
                        if topic:
                            report["topics"]["reused"] += 1
                        elif conflict:
                            report["topics"]["conflicts"] += 1
                            report["subtopics"]["conflicts"] += len(topic_data["subtopics"])
                            report["skills"]["conflicts"] += sum(
                                len(item["skills"]) for item in topic_data["subtopics"])
                            continue
                        else:
                            topic = Topic(subject_id=subject.id, grade_id=grade.id,
                                code=_code("topic", topic_data["name"]), name=topic_data["name"],
                                normalized_name=topic_norm, status="active")
                            db.add(topic)
                            await db.flush()
                            report["topics"]["created"] += 1

                        for sub_data in topic_data["subtopics"]:
                            sub_norm = normalize_catalog_name(sub_data["name"])
                            sub, conflict = await _existing_or_conflict(
                                db, Subtopic,
                                (Subtopic.topic_id == topic.id, Subtopic.normalized_name == sub_norm),
                                lambda target, parent=topic.id: target.topic_id == parent)
                            if sub:
                                report["subtopics"]["reused"] += 1
                            elif conflict:
                                report["subtopics"]["conflicts"] += 1
                                report["skills"]["conflicts"] += len(sub_data["skills"])
                                continue
                            else:
                                sub = Subtopic(topic_id=topic.id, code=_code("subtopic", sub_data["name"]),
                                    name=sub_data["name"], normalized_name=sub_norm, status="active")
                                db.add(sub)
                                await db.flush()
                                report["subtopics"]["created"] += 1

                            for skill_name in sub_data["skills"]:
                                skill_norm = normalize_catalog_name(skill_name)
                                skill, conflict = await _existing_or_conflict(
                                    db, Skill,
                                    (Skill.subtopic_id == sub.id, Skill.normalized_name == skill_norm),
                                    lambda target, parent=sub.id: target.subtopic_id == parent)
                                if skill:
                                    report["skills"]["reused"] += 1
                                elif conflict:
                                    report["skills"]["conflicts"] += 1
                                else:
                                    db.add(Skill(subtopic_id=sub.id, code=_code("skill", skill_name),
                                        name=skill_name, normalized_name=skill_norm, status="active"))
                                    await db.flush()
                                    report["skills"]["created"] += 1
    return {kind: dict(report[kind]) for kind in
            ("grades", "subjects", "topics", "subtopics", "skills")}


async def _main(path: Path):
    report = await seed_catalog(path)
    for kind, counts in report.items():
        print(f"{kind}: created={counts['created']} reused={counts['reused']} conflicts={counts['conflicts']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args()
    asyncio.run(_main(args.data))
