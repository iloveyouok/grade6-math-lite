import os
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from backend.routers.auth import verify_teacher_auth
from backend.db import get_connection
from backend.schemas import (
    GenerateSheetsRequest, MondayCheckRequest,
    ConsolidationSheetDetail, SheetItemSchema
)
from backend.services.kb_retriever import get_mother_question_by_id
from backend.services.llm_service import generate_variants, generate_student_report
from backend.services.math_sandbox import run_math_sandbox
from backend.services.pdf_renderer import render_consolidated_pdf

router = APIRouter(prefix="/api/sheets", tags=["sheets"], dependencies=[Depends(verify_teacher_auth)])

# Predefined L1 Calculation Warmup Bank
L1_WARMUP_BANK = [
    {
        "module_tag": "calc",
        "question_md": "计算：$\\frac{3}{5} \\times \\frac{4}{7} + \\frac{3}{5} \\times \\frac{3}{7}$",
        "answer_steps_md": "原式 $= \\frac{3}{5} \\times (\\frac{4}{7} + \\frac{3}{7}) = \\frac{3}{5} \\times 1 = \\frac{3}{5}$",
        "expected_answer": "3/5",
        "verify_code": "from fractions import Fraction\nres = Fraction(3, 5) * Fraction(4, 7) + Fraction(3, 5) * Fraction(3, 7)\nprint(res)"
    },
    {
        "module_tag": "calc",
        "question_md": "计算：$(\\frac{5}{6} - \\frac{1}{3}) \\times 18$",
        "answer_steps_md": "原式 $= \\frac{5}{6} \\times 18 - \\frac{1}{3} \\times 18 = 15 - 6 = 9$",
        "expected_answer": "9",
        "verify_code": "from fractions import Fraction\nres = (Fraction(5, 6) - Fraction(1, 3)) * 18\nprint(int(res))"
    },
    {
        "module_tag": "calc",
        "question_md": "解简易方程：$x + \\frac{1}{4}x = 25$",
        "answer_steps_md": "解：$\\frac{5}{4}x = 25$，$x = 25 \\times \\frac{4}{5} = 20$",
        "expected_answer": "20",
        "verify_code": "from fractions import Fraction\nx = 25 / Fraction(5, 4)\nprint(int(x))"
    },
    {
        "module_tag": "calc",
        "question_md": "计算：$\\frac{2}{3} \\times \\frac{5}{8} \\times \\frac{9}{10}$",
        "answer_steps_md": "原式 $= \\frac{2 \\times 5 \\times 9}{3 \\times 8 \\times 10} = \\frac{90}{240} = \\frac{3}{8}$",
        "expected_answer": "3/8",
        "verify_code": "from fractions import Fraction\nres = Fraction(2, 3) * Fraction(5, 8) * Fraction(9, 10)\nprint(res)"
    }
]

async def generate_sheets_for_students(student_ids: List[int], batch_title: Optional[str] = "周末数学冲刺巩固单") -> Dict[str, Any]:
    if not student_ids:
        raise HTTPException(status_code=400, detail="请至少勾选一名临界生")

    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch_code = datetime.now().strftime("%Y%m%d_%H%M%S")

    created_sheets = []
    template_students = []

    for stu_id in student_ids:
        # Fetch student info
        cursor.execute("SELECT * FROM students WHERE id = ?;", (stu_id,))
        stu = cursor.fetchone()
        if not stu:
            continue

        # Fetch unmastered mistakes, prioritized by wrong_count >= 2
        cursor.execute("""
            SELECT * FROM mistake_records
            WHERE student_id = ? AND mastery_status != 'passed'
            ORDER BY wrong_count DESC, id DESC;
        """, (stu_id,))
        unmastered_mistakes = [dict(row) for row in cursor.fetchall()]

        # Generate face-to-face script & WeChat message
        stats = {
            "baseline_score": stu["baseline_score"],
            "current_est_score": stu["current_est_score"],
            "unmastered_count": len(unmastered_mistakes)
        }
        report = await generate_student_report(stu["anonymous_code"], stats)
        parent_wechat = report.get("wechat_msg", "")
        f2f_script = ""
        if unmastered_mistakes and unmastered_mistakes[0].get("face_to_face_script"):
            f2f_script = unmastered_mistakes[0]["face_to_face_script"]
        else:
            f2f_script = "聚焦单位'1'找法与分配律去括号，启发孩子口述第一步计算依据。"

        # Create consolidation sheet record
        title = f"{stu['name']} - {batch_title or '周末数学个性化巩固单'}"
        cursor.execute("""
            INSERT INTO consolidation_sheets (
                batch_code, student_id, title, status, face_to_face_script, parent_wechat_msg, created_at
            ) VALUES (?, ?, ?, 'generated', ?, ?, ?);
        """, (batch_code, stu_id, title, f2f_script, parent_wechat, now_str))
        sheet_id = cursor.lastrowid

        sheet_items_data = []

        # ----------------------------------------------------
        # 1. Q1–Q3: L1 Basic Calculation Warmup
        # ----------------------------------------------------
        for i in range(3):
            warmup = L1_WARMUP_BANK[i % len(L1_WARMUP_BANK)]
            sheet_items_data.append({
                "sort_order": i + 1,
                "source_mistake_id": None,
                "kb_mother_id": "G6-CALC-01",
                "item_type": "calc_warmup",
                "question_md": warmup["question_md"],
                "answer_steps_md": warmup["answer_steps_md"],
                "sandbox_verified": 1
            })

        # ----------------------------------------------------
        # 2. Q4–Q6: Persistent / Unmastered Mistake Redo
        # ----------------------------------------------------
        for i in range(3):
            sort_order = i + 4
            if i < len(unmastered_mistakes):
                m = unmastered_mistakes[i]
                q_text = m.get("question_snapshot") or f"错题重练 (考点: {m.get('kb_mother_id')})"
                # Look up mother answer steps if snapshot is brief
                ans_steps = "详见学生原错题面批订正记录与标准解题规范。"
                if m.get("kb_mother_id"):
                    mother = get_mother_question_by_id(m["kb_mother_id"])
                    if mother:
                        ans_steps = mother.get("sections", {}).get("3. 小学算术规范步骤与扣分红线", ans_steps)

                sheet_items_data.append({
                    "sort_order": sort_order,
                    "source_mistake_id": m["id"],
                    "kb_mother_id": m.get("kb_mother_id", "G6-CALC-01"),
                    "item_type": "mistake_redo",
                    "question_md": q_text,
                    "answer_steps_md": ans_steps,
                    "sandbox_verified": 1
                })
            else:
                # Fallback to variant pool or warmup
                cursor.execute("SELECT * FROM variant_pool WHERE verified = 1 ORDER BY RANDOM() LIMIT 1;")
                vp = cursor.fetchone()
                if vp:
                    sheet_items_data.append({
                        "sort_order": sort_order,
                        "source_mistake_id": None,
                        "kb_mother_id": vp["kb_mother_id"],
                        "item_type": "mistake_redo",
                        "question_md": vp["question_md"],
                        "answer_steps_md": vp["answer_steps_md"],
                        "sandbox_verified": 1
                    })

        # ----------------------------------------------------
        # 3. Q7–Q8: Same-Source Baseline Variants
        # ----------------------------------------------------
        # Find target mother ID based on student's mistakes
        target_mother_id = "G6-FRAC-01"
        target_module = "fraction"
        if unmastered_mistakes:
            target_mother_id = unmastered_mistakes[0].get("kb_mother_id") or "G6-FRAC-01"
            target_module = unmastered_mistakes[0].get("module_tag") or "fraction"

        # Try to fetch from verified variant_pool first
        cursor.execute("""
            SELECT * FROM variant_pool
            WHERE kb_mother_id = ? AND verified = 1
            LIMIT 2;
        """, (target_mother_id,))
        v_pool_candidates = [dict(row) for row in cursor.fetchall()]

        # If not enough, generate via LLM with math sandbox verification
        generated_variants = []
        if len(v_pool_candidates) < 2:
            needed = 2 - len(v_pool_candidates)
            mother_doc = get_mother_question_by_id(target_mother_id) or {"id": target_mother_id, "module_tag": target_module}
            llm_items = await generate_variants(mother_doc, ["基础变式", "单位1"], count=needed)

            for cand in llm_items:
                v_code = cand.get("verify_code", "")
                exp_ans = cand.get("expected_answer", "")
                is_ok, msg = run_math_sandbox(exp_ans, v_code)
                if is_ok:
                    cand["sandbox_verified"] = 1
                    generated_variants.append(cand)
                else:
                    # Retry once or fallback
                    retry_items = await generate_variants(mother_doc, ["重试变式"], count=1)
                    if retry_items:
                        r_item = retry_items[0]
                        r_ok, _ = run_math_sandbox(r_item.get("expected_answer", ""), r_item.get("verify_code", ""))
                        if r_ok:
                            r_item["sandbox_verified"] = 1
                            generated_variants.append(r_item)

        final_variants = []
        for vp in v_pool_candidates:
            final_variants.append({
                "question_md": vp["question_md"],
                "answer_steps_md": vp["answer_steps_md"],
                "kb_mother_id": vp["kb_mother_id"],
                "sandbox_verified": 1
            })
        for gv in generated_variants:
            final_variants.append({
                "question_md": gv["question_md"],
                "answer_steps_md": gv["answer_steps_md"],
                "kb_mother_id": target_mother_id,
                "sandbox_verified": 1
            })

        # Fill items 7 and 8
        for i in range(2):
            sort_order = i + 7
            if i < len(final_variants):
                var_item = final_variants[i]
                sheet_items_data.append({
                    "sort_order": sort_order,
                    "source_mistake_id": None,
                    "kb_mother_id": var_item.get("kb_mother_id", target_mother_id),
                    "item_type": "variant",
                    "question_md": var_item["question_md"],
                    "answer_steps_md": var_item["answer_steps_md"],
                    "sandbox_verified": var_item.get("sandbox_verified", 1)
                })
            else:
                # Ultimate fallback to L1 warmup
                w = L1_WARMUP_BANK[(i + 2) % len(L1_WARMUP_BANK)]
                sheet_items_data.append({
                    "sort_order": sort_order,
                    "source_mistake_id": None,
                    "kb_mother_id": "G6-CALC-01",
                    "item_type": "variant",
                    "question_md": w["question_md"],
                    "answer_steps_md": w["answer_steps_md"],
                    "sandbox_verified": 1
                })

        # Save all 8 sheet_items to DB
        for item in sheet_items_data:
            cursor.execute("""
                INSERT INTO sheet_items (
                    sheet_id, sort_order, source_mistake_id, kb_mother_id, item_type,
                    question_md, answer_steps_md, sandbox_verified, check_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending');
            """, (
                sheet_id, item["sort_order"], item["source_mistake_id"],
                item["kb_mother_id"], item["item_type"], item["question_md"],
                item["answer_steps_md"], item["sandbox_verified"]
            ))

        template_students.append({
            "student_id": stu["id"],
            "student_name": stu["name"],
            "student_no": stu["student_no"],
            "class_name": stu["class_name"],
            "anonymous_code": stu["anonymous_code"],
            "batch_code": batch_code,
            "face_to_face_script": f2f_script,
            "parent_wechat_msg": parent_wechat,
            "sheet_items": sheet_items_data,
            "items": sheet_items_data
        })

        created_sheets.append(sheet_id)

    conn.commit()

    # 4. Render Merged PDF with Playwright (Strictly on-demand!)
    template_data = {
        "batch_title": batch_title or "周末数学冲刺巩固单",
        "batch_code": batch_code,
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "students_data": template_students
    }

    pdf_url = await render_consolidated_pdf(batch_code, template_data)

    # Update created sheets with PDF path
    cursor.execute("""
        UPDATE consolidation_sheets
        SET pdf_path = ?
        WHERE batch_code = ?;
    """, (pdf_url, batch_code))
    conn.commit()
    conn.close()

    return {
        "success": True,
        "batch_code": batch_code,
        "student_count": len(template_students),
        "sheet_ids": created_sheets,
        "pdf_path": pdf_url
    }


@router.post("/generate")
async def generate_consolidation_sheets(req: GenerateSheetsRequest):
    return await generate_sheets_for_students(req.student_ids, req.batch_title)

@router.get("")
def list_sheets():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cs.id, cs.batch_code, cs.student_id, cs.title, cs.status,
               cs.pdf_path, cs.created_at, cs.checked_at,
               s.name as student_name, s.student_no, s.class_name
        FROM consolidation_sheets cs
        JOIN students s ON cs.student_id = s.id
        ORDER BY cs.id DESC;
    """)
    sheets = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return sheets

@router.get("/{sheet_id}", response_model=ConsolidationSheetDetail)
def get_sheet_detail(sheet_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT cs.*, s.name as student_name, s.student_no
        FROM consolidation_sheets cs
        JOIN students s ON cs.student_id = s.id
        WHERE cs.id = ?;
    """, (sheet_id,))
    sheet = cursor.fetchone()
    if not sheet:
        conn.close()
        raise HTTPException(status_code=404, detail="巩固单不存在")

    cursor.execute("""
        SELECT * FROM sheet_items
        WHERE sheet_id = ?
        ORDER BY sort_order ASC;
    """, (sheet_id,))
    items = [dict(row) for row in cursor.fetchall()]
    conn.close()

    res = dict(sheet)
    res["items"] = items
    return res

@router.post("/monday-check")
def monday_reverse_check(req: MondayCheckRequest):
    """
    Monday 5-second reverse check.
    All items are checked (passed) by default. The teacher unchecks 1-2 still failed.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    passed_cnt = 0
    failed_cnt = 0

    for chk in req.checks:
        item_id = chk.item_id
        is_passed = chk.passed

        # Update sheet_item
        chk_str = "passed" if is_passed else "failed"
        cursor.execute("""
            UPDATE sheet_items
            SET check_result = ?
            WHERE id = ?;
        """, (chk_str, item_id))

        # Check if linked to source_mistake_id
        cursor.execute("SELECT source_mistake_id FROM sheet_items WHERE id = ?;", (item_id,))
        row = cursor.fetchone()
        if row and row["source_mistake_id"]:
            m_id = row["source_mistake_id"]
            if is_passed:
                passed_cnt += 1
                cursor.execute("""
                    UPDATE mistake_records
                    SET mastery_status = 'passed', updated_at = ?
                    WHERE id = ?;
                """, (now_str, m_id))
            else:
                failed_cnt += 1
                cursor.execute("""
                    UPDATE mistake_records
                    SET wrong_count = wrong_count + 1, mastery_status = 'unmastered', updated_at = ?
                    WHERE id = ?;
                """, (now_str, m_id))
        else:
            if is_passed:
                passed_cnt += 1
            else:
                failed_cnt += 1

    # Update sheet status to checked
    cursor.execute("""
        UPDATE consolidation_sheets
        SET status = 'checked', checked_at = ?
        WHERE id = ?;
    """, (now_str, req.sheet_id))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": f"反向验收完成：{passed_cnt} 题过关，{failed_cnt} 题锁定至下周追踪",
        "passed_count": passed_cnt,
        "failed_count": failed_cnt
    }
