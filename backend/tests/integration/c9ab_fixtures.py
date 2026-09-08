"""Compact SQL fixtures shared by the C9A/C9B PostgreSQL acceptance tests."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text


async def seed_execution_world(connection, *, plans=2):
    """Create two independent assessment chains and assigned remediation plans."""
    ids = {name: uuid4() for name in ("owner", "group_a", "group_b", "assessment",
        "variant", "subject", "grade", "topic")}
    for n in range(plans):
        for name in ("user", "student", "assignment", "participant", "source_submission",
                     "source_run", "task", "version", "assessment_item", "plan"):
            ids[f"{name}_{n}"] = uuid4()
        for item in range(2):
            for name in ("task", "version", "plan_item"):
                ids[f"{name}_{n}_{item}"] = uuid4()
    for sql in (
        "INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:owner,'c9ab-owner','c9ab-owner','C9AB Owner','hash')",
        "INSERT INTO class_groups(id,name,created_by) VALUES (:group_a,'C9AB 7A',:owner),(:group_b,'C9AB 7B',:owner)",
        "INSERT INTO subjects(id,code,name,normalized_name) VALUES (:subject,'c9ab','C9AB','c9ab')",
        "INSERT INTO grades(id,number,name,normalized_name) VALUES (:grade,7,'C9AB Grade 7','c9ab grade 7')",
        "INSERT INTO topics(id,subject_id,grade_id,code,name,normalized_name) VALUES (:topic,:subject,:grade,'c9ab','C9AB Topic','c9ab topic')",
        "INSERT INTO assessments(id,title,created_by) VALUES (:assessment,'C9AB Assessment',:owner)",
        "INSERT INTO assessment_variants(id,assessment_id,name,position) VALUES (:variant,:assessment,'A',1)",
    ):
        await connection.execute(text(sql), ids)
    for n in range(plans):
        values = {**ids, "student": ids[f"student_{n}"], "assignment": ids[f"assignment_{n}"],
            "user": ids[f"user_{n}"], "participant": ids[f"participant_{n}"], "submission": ids[f"source_submission_{n}"],
            "run": ids[f"source_run_{n}"], "task": ids[f"task_{n}"],
            "version": ids[f"version_{n}"], "assessment_item": ids[f"assessment_item_{n}"],
            "plan": ids[f"plan_{n}"], "display": f"C9AB Student {n}", "position": n + 1,
            "creation_key": f"c9ab-plan-{n}", "due": datetime.now(timezone.utc)+timedelta(days=1)}
        for sql in (
          "INSERT INTO users(id,login,normalized_login,display_name,password_hash) VALUES (:user,:display,:display,:display,'hash')",
          "INSERT INTO students(id,class_group_id,display_name) VALUES (:student,:group_a,:display)",
          "INSERT INTO student_user_links(user_id,student_id) VALUES (:user,:student)",
          "INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:owner)",
          "INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,answer_format,difficulty,status,created_by,approved_by,approved_at) VALUES (:version,:task,1,'Source','Source statement','problem','short_text',40,'approved',:owner,:owner,clock_timestamp())",
          "INSERT INTO assessment_items(id,variant_id,task_version_id,position,points) VALUES (:assessment_item,:variant,:version,:position,1)",
          "INSERT INTO assignments(id,assessment_id,class_group_id,start_at,due_at,created_by) VALUES (:assignment,:assessment,:group_a,clock_timestamp()-interval '1 hour',clock_timestamp()+interval '1 day',:owner)",
          "INSERT INTO assignment_participants(id,assignment_id,student_id,assigned_variant_id,variant_assigned_at) VALUES (:participant,:assignment,:student,:variant,clock_timestamp())",
          "INSERT INTO student_submissions(id,assignment_participant_id,attempt_no,status,submitted_at) VALUES (:submission,:participant,1,'submitted',clock_timestamp())",
          "INSERT INTO check_runs(id,submission_id,request_key,request_hash,handoff_version,input_snapshot,input_fingerprint,snapshot_schema_version,routing_version,checker_set_version,threshold_policy_version,prompt_model_policy_version,status,attempt_no,started_at,finished_at) VALUES (:run,:submission,'source','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',1,'{}','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','v1','v1','v1','v1','v1','completed',1,clock_timestamp(),clock_timestamp())",
          "INSERT INTO remediation_plans(id,owner_user_id,student_id,class_group_id,source_assignment_id,source_assignment_participant_id,source_submission_id,source_check_run_id,status,title,instructions,due_at,creation_key,creation_fingerprint,assigned_at) VALUES (:plan,:owner,:student,:group_a,:assignment,:participant,:submission,:run,'assigned','C9AB Plan',NULL,:due,:creation_key,'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',clock_timestamp())",
        ):
            await connection.execute(text(sql), values)
        for item in range(2):
            v = {**values, "task": ids[f"task_{n}_{item}"], "version": ids[f"version_{n}_{item}"],
                 "plan_item": ids[f"plan_item_{n}_{item}"], "item_position": item}
            await connection.execute(text("INSERT INTO tasks(id,subject_id,grade_id,topic_id,created_by) VALUES (:task,:subject,:grade,:topic,:owner)"), v)
            await connection.execute(text("INSERT INTO task_versions(id,task_id,version_no,title,statement,task_type,answer_format,difficulty,status,created_by,approved_by,approved_at) VALUES (:version,:task,1,'Practice','Practice statement','problem','short_text',40,'approved',:owner,:owner,clock_timestamp())"), v)
            await connection.execute(text("INSERT INTO remediation_plan_items(id,remediation_plan_id,position,task_version_id,selection_source) VALUES (:plan_item,:plan,:item_position,:version,'manual')"), v)
    return ids
