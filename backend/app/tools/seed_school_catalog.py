"""Idempotently install a curated, versioned Russian school starter catalog."""
import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.db.session import async_session_factory
from app.infrastructure.models import Grade, Skill, Subject, Subtopic, Topic, normalize_catalog_name

DATA = Path(__file__).parents[2] / "data" / "school_catalog_ru_v1.json"

def _code(prefix: str, name: str) -> str:
    # Stable UUID-independent code; normalized identity, not code, owns reuse.
    import hashlib
    return f"seed-{prefix}-{hashlib.sha1(normalize_catalog_name(name).encode()).hexdigest()[:16]}"

async def seed_catalog(path: Path = DATA) -> dict:
    data = json.loads(path.read_text(encoding="utf-8")); report = defaultdict(lambda: {"created":0,"reused":0,"conflicts":0})
    async with async_session_factory() as db:
      async with db.begin():
        grades = {}
        for number in range(1, 12):
            row = await db.scalar(select(Grade).where(Grade.number == number, Grade.status.in_(("active","provisional"))))
            if row: report["grades"]["reused"] += 1
            else:
                row=Grade(number=number,name=f"{number} класс",normalized_name=normalize_catalog_name(f"{number} класс"),status="active")
                db.add(row); await db.flush(); report["grades"]["created"] += 1
            grades[number]=row
        for subject_data in data["subjects"]:
            norm=normalize_catalog_name(subject_data["name"])
            subject=await db.scalar(select(Subject).where(Subject.normalized_name==norm,Subject.status.in_(("active","provisional"))))
            if subject: report["subjects"]["reused"]+=1
            else:
                subject=Subject(code=_code("subject",norm),name=subject_data["name"],normalized_name=norm,status="active")
                db.add(subject); await db.flush(); report["subjects"]["created"]+=1
            for grade_data in subject_data["grades"]:
              for topic_data in grade_data["topics"]:
                topic=await db.scalar(select(Topic).where(Topic.subject_id==subject.id,Topic.grade_id==grades[grade_data["number"]].id,Topic.normalized_name==normalize_catalog_name(topic_data["name"]),Topic.status.in_(("active","provisional"))))
                if topic: report["topics"]["reused"]+=1
                else:
                    topic=Topic(subject_id=subject.id,grade_id=grades[grade_data["number"]].id,code=_code("topic",topic_data["name"]),name=topic_data["name"],normalized_name=normalize_catalog_name(topic_data["name"]),status="active")
                    db.add(topic); await db.flush(); report["topics"]["created"]+=1
                for sub_data in topic_data["subtopics"]:
                    sub=await db.scalar(select(Subtopic).where(Subtopic.topic_id==topic.id,Subtopic.normalized_name==normalize_catalog_name(sub_data["name"]),Subtopic.status.in_(("active","provisional"))))
                    if sub: report["subtopics"]["reused"]+=1
                    else:
                        sub=Subtopic(topic_id=topic.id,code=_code("subtopic",sub_data["name"]),name=sub_data["name"],normalized_name=normalize_catalog_name(sub_data["name"]),status="active")
                        db.add(sub); await db.flush(); report["subtopics"]["created"]+=1
                    for skill_name in sub_data["skills"]:
                        skill=await db.scalar(select(Skill).where(Skill.subtopic_id==sub.id,Skill.normalized_name==normalize_catalog_name(skill_name),Skill.status.in_(("active","provisional"))))
                        if skill: report["skills"]["reused"]+=1
                        else:
                            db.add(Skill(subtopic_id=sub.id,code=_code("skill",skill_name),name=skill_name,normalized_name=normalize_catalog_name(skill_name),status="active")); await db.flush(); report["skills"]["created"]+=1
    return dict(report)

async def _main(path: Path):
    report=await seed_catalog(path)
    for kind, counts in report.items(): print(f"{kind}: created={counts['created']} reused={counts['reused']} conflicts={counts['conflicts']}")

if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--data",type=Path,default=DATA); args=parser.parse_args()
    asyncio.run(_main(args.data))
