import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from backend.routers.auth import verify_teacher_auth
from backend.db import get_connection
from backend.schemas import BatchTickRequest, ExamPaperSchema, ExamQuestionSchema

router = APIRouter(prefix="/api/exams", tags=["exams"], dependencies=[Depends(verify_teacher_auth)])

@router.get("")
def list_exams():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, unit_tag, exam_date, total_score FROM exam_papers ORDER BY id DESC;")
    papers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return papers

@router.get("/{paper_id}")
def get_exam_detail(paper_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM exam_papers WHERE id = ?;", (paper_id,))
    paper = cursor.fetchone()
    if not paper:
        conn.close()
        raise HTTPException(status_code=404, detail="试卷不存在")

    cursor.execute("""
        SELECT id, paper_id, q_no, kb_mother_id, module_tag, max_score, quick_error_tags
        FROM exam_questions
        WHERE paper_id = ?
        ORDER BY CAST(q_no AS INTEGER), q_no ASC;
    """, (paper_id,))
    questions = []
    for q in cursor.fetchall():
        q_dict = dict(q)
        try:
            q_dict["quick_error_tags"] = json.loads(q_dict["quick_error_tags"]) if q_dict["quick_error_tags"] else []
        except Exception:
            q_dict["quick_error_tags"] = []
        questions.append(q_dict)

    conn.close()
    result = dict(paper)
    result["questions"] = questions
    return result

@router.post("/batch-tick")
def batch_tick_mistakes(req: BatchTickRequest):
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Record exam score
    cursor.execute("""
        INSERT INTO exam_scores (paper_id, student_id, score, recorded_at)
        VALUES (?, ?, ?, ?);
    """, (req.paper_id, req.student_id, req.score, now_str))

    # Update student current_est_score (weighted rolling average)
    cursor.execute("SELECT current_est_score FROM students WHERE id = ?;", (req.student_id,))
    curr_score = cursor.fetchone()
    if curr_score:
        new_est = round(curr_score[0] * 0.4 + req.score * 0.6, 1)
        cursor.execute("UPDATE students SET current_est_score = ? WHERE id = ?;", (new_est, req.student_id))

    # 2. Process mistakes
    affected_mistake_ids = []
    for m in req.mistakes:
        q_id = m.exam_question_id
        mid = m.kb_mother_id
        mod = m.module_tag or "calc"
        q_no = m.q_no

        # Check existing unmastered record for this student and question/mother
        cursor.execute("""
            SELECT id, wrong_count, error_tags FROM mistake_records
            WHERE student_id = ? 
              AND (exam_question_id = ? OR (kb_mother_id = ? AND module_tag = ?))
              AND mastery_status != 'passed'
            LIMIT 1;
        """, (req.student_id, q_id, mid, mod))
        existing = cursor.fetchone()

        if existing:
            # Increment wrong_count!
            m_id = existing["id"]
            new_cnt = existing["wrong_count"] + 1
            try:
                prev_tags = json.loads(existing["error_tags"]) if existing["error_tags"] else []
            except Exception:
                prev_tags = []
            combined_tags = list(set(prev_tags + m.error_tags))

            cursor.execute("""
                UPDATE mistake_records
                SET wrong_count = ?, error_tags = ?, updated_at = ?
                WHERE id = ?;
            """, (new_cnt, json.dumps(combined_tags, ensure_ascii=False), now_str, m_id))
            affected_mistake_ids.append(m_id)
        else:
            snapshot = f"第 {q_no} 题 [母题考点: {mid}]"
            cursor.execute("""
                INSERT INTO mistake_records (
                    student_id, source_type, paper_id, exam_question_id, kb_mother_id,
                    module_tag, question_snapshot, error_tags, mastery_status, wrong_count,
                    created_at, updated_at
                ) VALUES (?, 'exam_tick', ?, ?, ?, ?, ?, ?, 'unmastered', 1, ?, ?);
            """, (
                req.student_id, req.paper_id, q_id, mid, mod, snapshot,
                json.dumps(m.error_tags, ensure_ascii=False), now_str, now_str
            ))
            affected_mistake_ids.append(cursor.lastrowid)

    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": f"成功记录成绩 {req.score} 分并处理 {len(req.mistakes)} 道错题",
        "affected_mistake_ids": affected_mistake_ids
    }
