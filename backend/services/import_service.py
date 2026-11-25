import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from backend.schemas import (
    DshImportPlanRequest,
    StudentImportCommitRequest,
    StudentImportPreviewRequest,
)
from backend.services.desensitize import generate_anonymous_code, match_student
from backend.services.kb_retriever import get_mother_question_by_id

VALID_TIERS = {"critical", "watchlist", "archived"}
VALID_MODULES = {"calc", "fraction", "ratio", "geometry"}


def _row_matches_filter(
    score: Optional[float],
    prev_score: Optional[float],
    req: StudentImportPreviewRequest,
) -> (bool, str):
    if score is None:
        return False, "本次无有效数字分数"

    reasons = []
    in_range = True
    if req.filter_min_score is not None and score < req.filter_min_score:
        in_range = False
    if req.filter_max_score is not None and score > req.filter_max_score:
        in_range = False

    if in_range and (req.filter_min_score is not None or req.filter_max_score is not None):
        reasons.append(f"分数处于筛选区间 ({req.filter_min_score or 0}~{req.filter_max_score or 100})")

    declined = False
    if prev_score is not None and (prev_score - score) >= req.min_drop:
        declined = True
        reasons.append(f"较上次下降 {round(prev_score - score, 1)} 分")

    if req.only_declined:
        return declined, "；".join(reasons) if declined else "未达到退步阈值"
    return (in_range or declined), ("；".join(reasons) if reasons else "未命中筛选条件")


def preview_student_import(conn, req: StudentImportPreviewRequest) -> Dict[str, Any]:
    """
    Evaluates uploaded/parsed student rows against local DB and filter criteria.
    Persists a preview batch so teacher can confirm commit cleanly.
    """
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    items: List[Dict[str, Any]] = []
    create_cnt = 0
    update_cnt = 0
    recommended_cnt = 0

    for r in req.rows:
        no_clean = str(r.student_no or "").strip()
        if no_clean.isdigit() and len(no_clean) == 1:
            no_clean = no_clean.zfill(2)
        cls_clean = (r.class_name or "六(1)班").strip()
        existing = match_student(cursor, raw_name=r.name, raw_no=no_clean, class_name=cls_clean)

        prev_score = existing["current_est_score"] if existing else None
        recommended, reason = _row_matches_filter(r.score, prev_score, req)
        action = "update" if existing else "create"
        if action == "create":
            create_cnt += 1
        else:
            update_cnt += 1
        if recommended:
            recommended_cnt += 1

        items.append({
            "student_no": existing["student_no"] if existing else no_clean,
            "name": existing["name"] if existing else r.name.strip(),
            "class_name": existing["class_name"] if existing else cls_clean,
            "prev_score": prev_score,
            "score": r.score,
            "notes": r.notes or "",
            "action": action,
            "existing_id": existing["id"] if existing else None,
            "existing_tier": existing["focus_tier"] if existing else None,
            "focus_tier": req.focus_tier if req.focus_tier in VALID_TIERS else "critical",
            "recommended": recommended,
            "selected": recommended,
            "reason": reason,
        })

    summary = {
        "total_rows": len(items),
        "recommended_count": recommended_cnt,
        "create_count": create_cnt,
        "update_count": update_cnt,
        "paper_title": req.paper_title or "",
        "unit_tag": req.unit_tag or "",
        "exam_date": req.exam_date or datetime.now().strftime("%Y-%m-%d"),
    }

    cursor.execute(
        """
        INSERT INTO import_batches (
            id, batch_type, source, status, title, preview_plan_json, summary_json, created_at, updated_at
        ) VALUES (?, 'student_csv', 'workbench', 'previewed', ?, ?, ?, ?, ?);
        """,
        (
            batch_id,
            req.paper_title or "学生筛选导入批次",
            json.dumps({"items": items}, ensure_ascii=False),
            json.dumps(summary, ensure_ascii=False),
            now_str,
            now_str,
        ),
    )
    conn.commit()
    return {"batch_id": batch_id, "summary": summary, "items": items}


def _upsert_student_and_score(
    cursor,
    student_no: str,
    name: str,
    class_name: str,
    score: Optional[float],
    focus_tier: str,
    summary_note: str,
    paper_id: Optional[int],
    now_str: str,
) -> Dict[str, Any]:
    no_clean = str(student_no or "").strip()
    if no_clean.isdigit() and len(no_clean) == 1:
        no_clean = no_clean.zfill(2)
    cls_clean = (class_name or "六(1)班").strip()
    tier = focus_tier if focus_tier in VALID_TIERS else "critical"

    existing = match_student(cursor, raw_name=name, raw_no=no_clean, class_name=cls_clean)
    if existing:
        stu_id = existing["id"]
        new_est = existing["current_est_score"]
        if score is not None:
            new_est = round(float(existing["current_est_score"]) * 0.4 + float(score) * 0.6, 1)
        new_summary = summary_note.strip() or existing.get("l3_summary_override", "")
        cursor.execute(
            """
            UPDATE students
            SET focus_tier = ?, current_est_score = ?, l3_summary_override = ?
            WHERE id = ?;
            """,
            (tier, new_est, new_summary, stu_id),
        )
        action = "updated"
        anon_code = existing["anonymous_code"]
    else:
        anon_code = generate_anonymous_code(cursor, cls_clean, no_clean)
        base_score = float(score) if score is not None else 50.0
        cursor.execute(
            """
            INSERT INTO students (
                student_no, name, anonymous_code, class_name, focus_tier,
                baseline_score, current_est_score, l3_summary_override, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (no_clean, name.strip(), anon_code, cls_clean, tier, base_score, base_score, summary_note.strip(), now_str),
        )
        stu_id = cursor.lastrowid
        action = "created"

    if paper_id and score is not None:
        cursor.execute(
            "INSERT INTO exam_scores (paper_id, student_id, score, recorded_at) VALUES (?, ?, ?, ?);",
            (paper_id, stu_id, float(score), now_str),
        )

    return {"student_id": stu_id, "anonymous_code": anon_code, "action": action}


def _ensure_exam_paper(
    cursor,
    paper_id: Optional[int],
    paper_title: str,
    unit_tag: str,
    exam_date: Optional[str],
    total_score: float,
    now_str: str,
) -> Optional[int]:
    if paper_id:
        return paper_id
    if not paper_title or not paper_title.strip():
        return None
    cursor.execute(
        """
        INSERT INTO exam_papers (title, unit_tag, exam_date, total_score, created_at)
        VALUES (?, ?, ?, ?, ?);
        """,
        (
            paper_title.strip(),
            unit_tag.strip(),
            exam_date or datetime.now().strftime("%Y-%m-%d"),
            total_score,
            now_str,
        ),
    )
    return cursor.lastrowid


def commit_student_import(conn, req: StudentImportCommitRequest) -> Dict[str, Any]:
    """
    Commits selected students from a roster/score import preview into SQLite.
    """
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    selected_items = [s for s in req.students if s.selected]
    has_scores = any(s.score is not None for s in selected_items)
    paper_id = _ensure_exam_paper(
        cursor,
        req.paper_id,
        req.paper_title if has_scores else "",
        req.unit_tag or "",
        req.exam_date,
        req.total_score,
        now_str,
    )

    created = 0
    updated = 0
    results = []
    for item in selected_items:
        res = _upsert_student_and_score(
            cursor=cursor,
            student_no=item.student_no,
            name=item.name,
            class_name=item.class_name,
            score=item.score,
            focus_tier=item.focus_tier,
            summary_note=item.l3_summary_override or "",
            paper_id=paper_id,
            now_str=now_str,
        )
        if res["action"] == "created":
            created += 1
        else:
            updated += 1
        results.append(res)

    if req.batch_id:
        cursor.execute(
            "UPDATE import_batches SET status = 'committed', paper_id = ?, updated_at = ? WHERE id = ?;",
            (paper_id, now_str, req.batch_id),
        )

    conn.commit()
    return {
        "success": True,
        "created_count": created,
        "updated_count": updated,
        "paper_id": paper_id,
        "results": results,
    }


def confirm_ocr_items_for_student(
    cursor,
    student_id: int,
    items: List[Dict[str, Any]],
    now_str: str,
    source_type: str = "photo_ocr",
) -> List[int]:
    """
    Shared helper to record confirmed wrong questions from OCR into mistake_records.
    """
    inserted_ids: List[int] = []
    for item in items:
        if not item.get("is_wrong", True):
            continue
        cursor.execute(
            """
            INSERT INTO mistake_records (
                student_id, source_type, kb_mother_id, module_tag, question_snapshot,
                error_tags, photo_url, ocr_note, face_to_face_script,
                mastery_status, wrong_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unmastered', 1, ?, ?);
            """,
            (
                student_id,
                source_type,
                item.get("kb_mother_id") or "G6-CALC-01",
                item.get("module_tag") or "calc",
                item.get("question_snapshot") or "",
                json.dumps(item.get("error_tags") or [], ensure_ascii=False),
                item.get("photo_url") or "",
                item.get("ocr_note") or "",
                item.get("face_to_face_script") or "",
                now_str,
                now_str,
            ),
        )
        inserted_ids.append(cursor.lastrowid)
    return inserted_ids


def preview_dsh_import_plan(conn, req: DshImportPlanRequest) -> Dict[str, Any]:
    """
    Validates DSH's proposed import plan against local ref_map and constraints.
    Returns sanitized summary for DSH (never exposing real names back into LLM context).
    """
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM import_batches WHERE id = ?;", (req.batch_id,))
    batch = cursor.fetchone()
    if not batch:
        raise ValueError(f"批次不存在: {req.batch_id}")

    summary_meta = json.loads(batch["summary_json"] or "{}")
    ref_map = summary_meta.get("ref_map", {})
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    validated_students: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for stu in req.students_to_import:
        meta = ref_map.get(stu.ref)
        item_warn = stu.warning or ""
        if not meta:
            # Try matching directly by anonymous_code in DB
            cursor.execute("SELECT * FROM students WHERE anonymous_code = ? LIMIT 1;", (stu.ref,))
            db_stu = cursor.fetchone()
            if db_stu:
                meta = {
                    "ref": stu.ref,
                    "matched": True,
                    "student_id": db_stu["id"],
                    "student_no": db_stu["student_no"],
                    "name": db_stu["name"],
                    "class_name": db_stu["class_name"],
                    "prev_score": db_stu["current_est_score"],
                }
            else:
                item_warn = (item_warn + " 未找到本地映射编码，需在网页端核对姓名").strip()
                warnings.append(f"{stu.ref} (座号 {stu.student_no}): {item_warn}")

        score_val = stu.score
        if score_val is not None and (score_val < 0 or score_val > req.total_score):
            item_warn = (item_warn + f" 分数 {score_val} 超出 0~{req.total_score} 范围").strip()
            warnings.append(f"{stu.ref} (座号 {stu.student_no}): {item_warn}")

        tier = stu.focus_tier if stu.focus_tier in VALID_TIERS else "critical"
        validated_students.append({
            "ref": stu.ref,
            "matched": bool(meta and meta.get("matched")),
            "student_no": (meta.get("student_no") if meta else stu.student_no) or stu.student_no,
            "class_name": (meta.get("class_name") if meta else stu.class_name) or stu.class_name,
            "prev_score": meta.get("prev_score") if meta else None,
            "score": score_val,
            "focus_tier": tier,
            "recommended": stu.recommended,
            "reason": stu.reason or "",
            "warning": item_warn,
        })

    validated_mistakes: List[Dict[str, Any]] = []
    for m in req.mistakes:
        mod = m.module_tag if m.module_tag in VALID_MODULES else "calc"
        mid = m.kb_mother_id
        if mid and not get_mother_question_by_id(mid):
            warnings.append(f"{m.ref} 题 {m.q_no}: 母题 {mid} 不在库中，已回退默认归类")
            mid = "G6-CALC-01"
        validated_mistakes.append({
            "ref": m.ref,
            "q_no": m.q_no or "",
            "kb_mother_id": mid or "G6-CALC-01",
            "module_tag": mod,
            "error_tags": m.error_tags,
            "question_snapshot": m.question_snapshot,
            "face_to_face_script": m.face_to_face_script or "",
        })

    plan_payload = {
        "exam_title": req.exam_title or "测验成绩批处理导入",
        "unit_tag": req.unit_tag or "",
        "exam_date": req.exam_date or datetime.now().strftime("%Y-%m-%d"),
        "total_score": req.total_score,
        "filter_rule": req.filter_rule or "",
        "students_to_import": validated_students,
        "mistakes": validated_mistakes,
        "warnings": warnings,
    }

    cursor.execute(
        """
        UPDATE import_batches
        SET status = 'previewed', title = ?, preview_plan_json = ?, updated_at = ?
        WHERE id = ?;
        """,
        (plan_payload["exam_title"], json.dumps(plan_payload, ensure_ascii=False), now_str, req.batch_id),
    )
    conn.commit()

    return {
        "batch_id": req.batch_id,
        "status": "previewed",
        "exam_title": plan_payload["exam_title"],
        "filter_rule": plan_payload["filter_rule"],
        "recommended_students_count": sum(1 for s in validated_students if s["recommended"]),
        "total_students_in_plan": len(validated_students),
        "mistakes_count": len(validated_mistakes),
        "warnings": warnings,
        "students_preview": validated_students,
    }


def commit_dsh_import_batch(conn, batch_id: str, confirmed_by_user: bool) -> Dict[str, Any]:
    """
    Commits a previewed DSH import batch after explicit confirmation.
    Resolves desensitized refs locally to real student names in SQLite.
    """
    if not confirmed_by_user:
        raise ValueError("必须先向教师展示预览并获得明确确认 (confirmed_by_user=true) 才允许写库")

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM import_batches WHERE id = ?;", (batch_id,))
    batch = cursor.fetchone()
    if not batch:
        raise ValueError(f"批次不存在: {batch_id}")
    if batch["status"] == "committed":
        return {"success": True, "already_committed": True, "batch_id": batch_id}
    if batch["status"] != "previewed":
        raise ValueError(f"批次状态为 {batch['status']}，请先生成预览方案后再确认提交")

    plan = json.loads(batch["preview_plan_json"] or "{}")
    summary_meta = json.loads(batch["summary_json"] or "{}")
    ref_map = summary_meta.get("ref_map", {})
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    students_plan = [s for s in plan.get("students_to_import", []) if s.get("recommended", True)]
    has_scores = any(s.get("score") is not None for s in students_plan)
    paper_id = _ensure_exam_paper(
        cursor,
        batch["paper_id"],
        plan.get("exam_title", "测验成绩批处理导入") if has_scores else "",
        plan.get("unit_tag", ""),
        plan.get("exam_date"),
        float(plan.get("total_score", 100.0)),
        now_str,
    )

    ref_to_student_id: Dict[str, int] = {}
    created_cnt = 0
    updated_cnt = 0

    for stu in students_plan:
        ref = stu["ref"]
        local_info = ref_map.get(ref, {})
        real_name = local_info.get("name") or f"学生{stu.get('student_no', '')}"
        res = _upsert_student_and_score(
            cursor=cursor,
            student_no=stu.get("student_no") or local_info.get("student_no", ""),
            name=real_name,
            class_name=stu.get("class_name") or local_info.get("class_name", "六(1)班"),
            score=stu.get("score"),
            focus_tier=stu.get("focus_tier", "critical"),
            summary_note=stu.get("reason", ""),
            paper_id=paper_id,
            now_str=now_str,
        )
        ref_to_student_id[ref] = res["student_id"]
        if res["action"] == "created":
            created_cnt += 1
        else:
            updated_cnt += 1

    mistakes_inserted = 0
    mistakes_incremented = 0
    for m in plan.get("mistakes", []):
        ref = m["ref"]
        stu_id = ref_to_student_id.get(ref)
        if not stu_id:
            local_info = ref_map.get(ref, {})
            if local_info.get("student_id"):
                stu_id = local_info["student_id"]
            else:
                cursor.execute("SELECT id FROM students WHERE anonymous_code = ? LIMIT 1;", (ref,))
                row = cursor.fetchone()
                if row:
                    stu_id = row["id"]
        if not stu_id:
            continue

        mid = m.get("kb_mother_id") or "G6-CALC-01"
        mod = m.get("module_tag") or "calc"
        cursor.execute(
            """
            SELECT id, wrong_count, error_tags FROM mistake_records
            WHERE student_id = ? AND kb_mother_id = ? AND module_tag = ? AND mastery_status != 'passed'
            LIMIT 1;
            """,
            (stu_id, mid, mod),
        )
        existing_m = cursor.fetchone()
        if existing_m:
            prev_tags = json.loads(existing_m["error_tags"] or "[]")
            merged_tags = list(dict.fromkeys(prev_tags + (m.get("error_tags") or [])))
            cursor.execute(
                """
                UPDATE mistake_records
                SET wrong_count = ?, error_tags = ?, updated_at = ?
                WHERE id = ?;
                """,
                (existing_m["wrong_count"] + 1, json.dumps(merged_tags, ensure_ascii=False), now_str, existing_m["id"]),
            )
            mistakes_incremented += 1
        else:
            cursor.execute(
                """
                INSERT INTO mistake_records (
                    student_id, source_type, paper_id, kb_mother_id, module_tag,
                    question_snapshot, error_tags, face_to_face_script,
                    mastery_status, wrong_count, created_at, updated_at
                ) VALUES (?, 'dsh_batch', ?, ?, ?, ?, ?, ?, 'unmastered', 1, ?, ?);
                """,
                (
                    stu_id,
                    paper_id,
                    mid,
                    mod,
                    m.get("question_snapshot") or "",
                    json.dumps(m.get("error_tags") or [], ensure_ascii=False),
                    m.get("face_to_face_script") or "",
                    now_str,
                    now_str,
                ),
            )
            mistakes_inserted += 1

    cursor.execute(
        "UPDATE import_batches SET status = 'committed', paper_id = ?, updated_at = ? WHERE id = ?;",
        (paper_id, now_str, batch_id),
    )
    conn.commit()

    return {
        "success": True,
        "batch_id": batch_id,
        "paper_id": paper_id,
        "students_created": created_cnt,
        "students_updated": updated_cnt,
        "mistakes_inserted": mistakes_inserted,
        "mistakes_incremented": mistakes_incremented,
    }


def get_import_batch_for_ui(conn, batch_id: str) -> Optional[Dict[str, Any]]:
    """
    Returns batch preview resolved with real names strictly for authenticated teacher Web UI.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM import_batches WHERE id = ?;", (batch_id,))
    row = cursor.fetchone()
    if not row:
        return None
    data = dict(row)
    summary = json.loads(data.get("summary_json") or "{}")
    plan = json.loads(data.get("preview_plan_json") or "{}")
    ref_map = summary.get("ref_map", {})

    resolved_students = []
    for stu in plan.get("students_to_import", []):
        ref = stu.get("ref", "")
        local_info = ref_map.get(ref, {})
        resolved_students.append({
            **stu,
            "name": local_info.get("name") or f"学生{stu.get('student_no', '')}",
        })

    if resolved_students:
        plan["students_to_import"] = resolved_students

    data["summary"] = summary
    data["preview_plan"] = plan
    data["raw_ocr"] = json.loads(data.get("raw_ocr_json") or "[]")
    return data
