import asyncio
import json
from datetime import datetime
from typing import List
from backend.db import get_connection
from backend.services.desensitize import match_student
from backend.services.tencent_ocr import extract_header_identity_ocr, submit_and_poll_tencent_ocr

# Limit concurrent OCR tasks to keep RAM strictly within low-memory server budgets
_OCR_SEMAPHORE = asyncio.Semaphore(2)


async def _process_single_task(task_id: int, file_path: str, need_header_match: bool):
    async with _OCR_SEMAPHORE:
        conn = get_connection()
        cursor = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "UPDATE ocr_tasks SET ocr_status = 'processing', updated_at = ? WHERE id = ?;",
            (now_str, task_id),
        )
        conn.commit()
        conn.close()

        try:
            header_match_id = None
            header_note = ""
            if need_header_match:
                header_info = await extract_header_identity_ocr(file_path)
                if header_info.get("student_no") or header_info.get("name"):
                    conn_m = get_connection()
                    cur_m = conn_m.cursor()
                    stu = match_student(
                        cur_m,
                        raw_name=header_info.get("name", ""),
                        raw_no=header_info.get("student_no", ""),
                    )
                    conn_m.close()
                    if stu:
                        header_match_id = stu["id"]
                        header_note = f"页眉OCR匹配: {stu['student_no']}号 {stu['name']}"

            parsed_items = await submit_and_poll_tencent_ocr(file_path)
            done_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn2 = get_connection()
            cur2 = conn2.cursor()
            if header_match_id:
                cur2.execute(
                    """
                    UPDATE ocr_tasks
                    SET student_id = ?, match_status = 'matched', match_note = ?,
                        ocr_status = 'ready', parsed_items_json = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (header_match_id, header_note, json.dumps(parsed_items, ensure_ascii=False), done_str, task_id),
                )
            else:
                cur2.execute(
                    """
                    UPDATE ocr_tasks
                    SET ocr_status = 'ready', parsed_items_json = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (json.dumps(parsed_items, ensure_ascii=False), done_str, task_id),
                )
            conn2.commit()
            conn2.close()
        except Exception as exc:
            err_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn_err = get_connection()
            cur_err = conn_err.cursor()
            cur_err.execute(
                """
                UPDATE ocr_tasks
                SET ocr_status = 'failed', error_msg = ?, updated_at = ?
                WHERE id = ?;
                """,
                (str(exc), err_str, task_id),
            )
            conn_err.commit()
            conn_err.close()


async def process_homework_batch(batch_id: str) -> None:
    """
    Background runner for a homework upload batch.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "UPDATE import_batches SET status = 'processing', updated_at = ? WHERE id = ?;",
        (now_str, batch_id),
    )
    cursor.execute(
        "SELECT id, file_path, match_status FROM ocr_tasks WHERE batch_id = ? AND ocr_status = 'pending';",
        (batch_id,),
    )
    tasks = [dict(r) for r in cursor.fetchall()]
    conn.commit()
    conn.close()

    if tasks:
        await asyncio.gather(
            *[
                _process_single_task(
                    t["id"],
                    t["file_path"],
                    need_header_match=(t["match_status"] == "unmatched"),
                )
                for t in tasks
            ]
        )

    conn_end = get_connection()
    cur_end = conn_end.cursor()
    cur_end.execute("SELECT ocr_status FROM ocr_tasks WHERE batch_id = ?;", (batch_id,))
    statuses: List[str] = [r["ocr_status"] for r in cur_end.fetchall()]
    if any(s == "failed" for s in statuses) and not any(s == "ready" for s in statuses):
        final_status = "failed"
    elif any(s == "failed" for s in statuses):
        final_status = "partial_failed"
    else:
        final_status = "ready"

    end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur_end.execute(
        "UPDATE import_batches SET status = ?, updated_at = ? WHERE id = ?;",
        (final_status, end_str, batch_id),
    )
    conn_end.commit()
    conn_end.close()
