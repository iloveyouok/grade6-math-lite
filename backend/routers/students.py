import json
from collections import Counter
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from backend.routers.auth import verify_teacher_auth
from backend.db import get_connection
from backend.schemas import (
    StudentOverviewResponse, StudentResponse,
    UpdateStudentSummaryRequest, ParentReportResponse
)
from backend.services.llm_service import generate_student_report

router = APIRouter(prefix="/api/students", tags=["students"], dependencies=[Depends(verify_teacher_auth)])

@router.get("", response_model=StudentOverviewResponse)
def get_students_overview():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, student_no, name, anonymous_code, class_name, focus_tier,
               baseline_score, current_est_score, l3_summary_override, created_at
        FROM students
        ORDER BY student_no ASC;
    """)
    students_raw = cursor.fetchall()

    student_list = []
    all_error_tags = []
    total_mistakes = 0
    passed_mistakes = 0

    for s in students_raw:
        stu_id = s["id"]

        # Fetch mistake records for this student
        cursor.execute("""
            SELECT id, module_tag, error_tags, mastery_status, wrong_count
            FROM mistake_records
            WHERE student_id = ?;
        """, (stu_id,))
        m_records = cursor.fetchall()

        module_unmastered = {"calc": 0, "fraction": 0, "ratio": 0, "geometry": 0}
        persistent_cnt = 0

        for m in m_records:
            total_mistakes += 1
            if m["mastery_status"] == "passed":
                passed_mistakes += 1
            else:
                mod = m["module_tag"] or "calc"
                if mod in module_unmastered:
                    module_unmastered[mod] += 1
                if m["wrong_count"] >= 2:
                    persistent_cnt += 1

                try:
                    tags = json.loads(m["error_tags"]) if m["error_tags"] else []
                    all_error_tags.extend(tags)
                except Exception:
                    pass

        # Calculate module status lights: 0 -> green, 1 -> yellow, >=2 -> red
        status_lights = {}
        for mod, cnt in module_unmastered.items():
            if cnt == 0:
                status_lights[mod] = "green"
            elif cnt == 1:
                status_lights[mod] = "yellow"
            else:
                status_lights[mod] = "red"

        # Check if student has pending consolidation sheet
        cursor.execute("""
            SELECT id FROM consolidation_sheets
            WHERE student_id = ? AND status = 'generated' LIMIT 1;
        """, (stu_id,))
        has_pending_sheet = cursor.fetchone() is not None

        # Determine primary weakness & next action from real data
        unmastered_total = sum(module_unmastered.values())
        if len(m_records) == 0:
            primary_weak = "未评估"
            next_act = "安排摸底测验"
        elif unmastered_total == 0:
            primary_weak = "全部错题已消项"
            next_act = "巩固良好，保持训练"
        else:
            # Pick most common error tag among unmastered
            stu_unmastered_tags = []
            for m in m_records:
                if m["mastery_status"] != "passed":
                    try:
                        tags = json.loads(m["error_tags"]) if m["error_tags"] else []
                        stu_unmastered_tags.extend(tags)
                    except Exception:
                        pass
            if stu_unmastered_tags:
                primary_weak = Counter(stu_unmastered_tags).most_common(1)[0][0]
            else:
                for mod, cnt in module_unmastered.items():
                    if cnt > 0:
                        mod_names = {"calc": "计算与简算", "fraction": "找错单位 1", "ratio": "按比分配", "geometry": "圆的周长与面积"}
                        primary_weak = mod_names.get(mod, "基础薄弱项")
                        break

            if has_pending_sheet:
                next_act = "先完成今天的验收"
            elif persistent_cnt > 0:
                next_act = "优先补一次基础练习"
            else:
                next_act = "安排本周巩固单"

        student_list.append(StudentResponse(
            id=s["id"],
            student_no=s["student_no"],
            name=s["name"],
            anonymous_code=s["anonymous_code"],
            class_name=s["class_name"],
            focus_tier=s["focus_tier"],
            baseline_score=s["baseline_score"],
            current_est_score=s["current_est_score"],
            l3_summary_override=s["l3_summary_override"],
            created_at=s["created_at"],
            module_status=status_lights,
            persistent_count=persistent_cnt,
            primary_weakness=primary_weak,
            next_action=next_act,
            unmastered_count=sum(module_unmastered.values()),
            total_mistakes_count=len(m_records)
        ))

    conn.close()

    tag_counter = Counter(all_error_tags)
    top_tags = [{"tag": tag, "count": count} for tag, count in tag_counter.most_common(5)]

    passed_rate = round((passed_mistakes / total_mistakes * 100), 1) if total_mistakes > 0 else 0.0

    return StudentOverviewResponse(
        critical_count=len(student_list),
        passed_rate=passed_rate,
        top_error_tags=top_tags,
        students=student_list
    )

@router.get("/{student_id}")
def get_student_detail(student_id: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM students WHERE id = ?;", (student_id,))
    stu = cursor.fetchone()
    if not stu:
        conn.close()
        raise HTTPException(status_code=404, detail="学生不存在")

    # Mistakes sorted by wrong_count >= 2 first, then unmastered, then created_at DESC
    cursor.execute("""
        SELECT * FROM mistake_records
        WHERE student_id = ?
        ORDER BY 
            CASE WHEN mastery_status = 'passed' THEN 3
                 WHEN wrong_count >= 2 THEN 1
                 ELSE 2 END,
            updated_at DESC, id DESC;
    """, (student_id,))
    mistakes = [dict(row) for row in cursor.fetchall()]

    for m in mistakes:
        try:
            m["error_tags"] = json.loads(m["error_tags"]) if m["error_tags"] else []
        except Exception:
            m["error_tags"] = []

    # Recent exam scores
    cursor.execute("""
        SELECT es.score, es.recorded_at, ep.title
        FROM exam_scores es
        JOIN exam_papers ep ON es.paper_id = ep.id
        WHERE es.student_id = ?
        ORDER BY es.id DESC LIMIT 5;
    """, (student_id,))
    scores = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return {
        "student": dict(stu),
        "mistakes": mistakes,
        "recent_scores": scores
    }

@router.put("/{student_id}/summary")
def update_student_summary(student_id: int, req: UpdateStudentSummaryRequest):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE students
        SET l3_summary_override = ?
        WHERE id = ?;
    """, (req.l3_summary, student_id))
    conn.commit()
    conn.close()
    return {"success": True, "message": "学情总结已更新"}

@router.post("/{student_id}/wechat-report", response_model=ParentReportResponse)
async def get_parent_report(student_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, anonymous_code, baseline_score, current_est_score FROM students WHERE id = ?;", (student_id,))
    stu = cursor.fetchone()
    if not stu:
        conn.close()
        raise HTTPException(status_code=404, detail="学生不存在")

    cursor.execute("""
        SELECT module_tag, wrong_count, mastery_status
        FROM mistake_records
        WHERE student_id = ?;
    """, (student_id,))
    mistakes = cursor.fetchall()
    conn.close()

    stats = {
        "baseline_score": stu["baseline_score"],
        "current_est_score": stu["current_est_score"],
        "total_recorded": len(mistakes),
        "passed": sum(1 for m in mistakes if m["mastery_status"] == "passed"),
        "unmastered": sum(1 for m in mistakes if m["mastery_status"] != "passed")
    }

    # CRITICAL PRIVACY: Only pass anonymous_code to LLM!
    report = await generate_student_report(stu["anonymous_code"], stats)

    return ParentReportResponse(
        student_name=stu["name"],
        anonymous_code=stu["anonymous_code"],
        teacher_summary=report["teacher_summary"],
        wechat_msg=report["wechat_msg"]
    )

@router.post("/mistakes/{mistake_id}/pass")
def mark_mistake_pass(mistake_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        UPDATE mistake_records
        SET mastery_status = 'passed', updated_at = ?
        WHERE id = ?;
    """, (now_str, mistake_id))
    conn.commit()
    conn.close()
    return {"success": True, "message": "该错题已标记为面批过关"}
