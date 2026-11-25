import asyncio
import io
import json
import os
import uuid
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from backend.config import settings
from backend.db import get_connection
from backend.routers.auth import verify_teacher_auth, verify_teacher_or_mcp_auth
from backend.schemas import (
    AssignTaskStudentRequest,
    OcrBatchConfirmRequest,
    OcrConfirmRequest,
    OcrParsedQuestion,
)
from backend.services.batch_worker import process_homework_batch
from backend.services.desensitize import desensitize_grade_rows, match_homework_filename
from backend.services.image_crop import compress_and_save_upload
from backend.services.import_service import confirm_ocr_items_for_student
from backend.services.tencent_ocr import recognize_grade_table_ocr, submit_and_poll_tencent_ocr

router = APIRouter(prefix="/api/ocr", tags=["ocr"])
batches_router = APIRouter(prefix="/api/batches", tags=["batches"])

# Temporary in-memory cache for recent mobile uploads
LATEST_MOBILE_UPLOADS = []
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_MAX_ZIP_IMAGES = 60


def _is_valid_image_name(name: str) -> bool:
    if not name or name.startswith("__MACOSX/") or "/." in name or os.path.basename(name).startswith("."):
        return False
    ext = os.path.splitext(name)[1].lower()
    return ext in _IMAGE_EXTS


def _collect_uploaded_images(raw_files: List[tuple]) -> List[tuple]:
    """
    Flattens uploaded images and safe .zip archives into [(logical_path, bytes)].
    """
    collected: List[tuple] = []
    for fname, raw_bytes in raw_files:
        ext = os.path.splitext(fname or "")[1].lower()
        if ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    norm = info.filename.replace("\\", "/")
                    if norm.startswith("/") or ".." in norm.split("/"):
                        continue
                    if _is_valid_image_name(norm):
                        collected.append((norm, zf.read(info.filename)))
                        if len(collected) >= _MAX_ZIP_IMAGES:
                            break
        elif _is_valid_image_name(fname):
            collected.append((fname, raw_bytes))
    return collected


@router.post("/upload", response_model=List[OcrParsedQuestion], dependencies=[Depends(verify_teacher_auth)])
async def upload_and_ocr(file: UploadFile = File(...)):
    """PC/Workbench single photo upload and OCR analysis."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供有效文件")

    local_url = compress_and_save_upload(file.file)
    parsed_items = await submit_and_poll_tencent_ocr(local_url)
    return parsed_items


@router.post("/mobile-upload")
async def mobile_photo_upload(
    file: UploadFile = File(...),
    student_id: Optional[int] = Query(None),
):
    """
    Direct mobile upload endpoint from WeChat scan (/upload.html).
    Supports optional ?student_id=... so QR scan can pre-bind a student.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供有效文件")

    local_url = compress_and_save_upload(file.file)
    parsed_items = await submit_and_poll_tencent_ocr(local_url)

    upload_info = {
        "photo_url": local_url,
        "filename": file.filename,
        "student_id": student_id,
        "uploaded_at": datetime.now().strftime("%H:%M:%S"),
        "parsed_items": parsed_items,
    }
    LATEST_MOBILE_UPLOADS.insert(0, upload_info)
    if len(LATEST_MOBILE_UPLOADS) > 10:
        LATEST_MOBILE_UPLOADS.pop()

    return {
        "success": True,
        "photo_url": local_url,
        "student_id": student_id,
        "item_count": len(parsed_items),
    }


@router.get("/latest-mobile", dependencies=[Depends(verify_teacher_auth)])
def get_latest_mobile_upload():
    """Polls latest mobile uploads to sync with workbench in real time."""
    if LATEST_MOBILE_UPLOADS:
        return {"has_new": True, "data": LATEST_MOBILE_UPLOADS[0]}
    return {"has_new": False, "data": None}


@router.post("/confirm", dependencies=[Depends(verify_teacher_auth)])
def confirm_ocr_mistakes(req: OcrConfirmRequest):
    """2-second confirm card batch insertion into mistake_records."""
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    items_dicts = [item.model_dump() for item in req.items]
    inserted_ids = confirm_ocr_items_for_student(cursor, req.student_id, items_dicts, now_str, source_type="photo_ocr")
    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": f"成功将 {len(inserted_ids)} 道错题录入该生错题库",
        "inserted_count": len(inserted_ids),
        "inserted_ids": inserted_ids,
    }


@router.post("/batch-upload", dependencies=[Depends(verify_teacher_or_mcp_auth)])
async def batch_upload_homework(
    files: List[UploadFile] = File(...),
    student_id: Optional[int] = Form(None),
    title: str = Form("批量作业照片导入"),
    wait_for_completion: bool = Form(False),
):
    """
    Uploads multiple homework images and/or .zip archives.
    Matches filenames/folders to tracked students, queues compressed images, and runs Semaphore(2) OCR.
    """
    raw_pairs = []
    for f in files:
        if f.filename:
            raw_pairs.append((f.filename, await f.read()))

    try:
        images = _collect_uploaded_images(raw_pairs)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件损坏，无法解压")

    if not images:
        raise HTTPException(status_code=400, detail="未检测到有效作业图片（支持 JPG / PNG / ZIP）")

    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch_id = f"hw_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    cursor.execute(
        """
        INSERT INTO import_batches (
            id, batch_type, source, status, title, summary_json, created_at, updated_at
        ) VALUES (?, 'homework_batch', 'workbench', 'pending', ?, '{}', ?, ?);
        """,
        (batch_id, title or "批量作业照片导入", now_str, now_str),
    )

    matched_cnt = 0
    unmatched_cnt = 0
    created_tasks = []
    for logical_path, img_bytes in images:
        local_url = compress_and_save_upload(io.BytesIO(img_bytes))
        stu_id, match_status, match_note = match_homework_filename(cursor, logical_path, explicit_student_id=student_id)
        if stu_id:
            matched_cnt += 1
        else:
            unmatched_cnt += 1

        cursor.execute(
            """
            INSERT INTO ocr_tasks (
                batch_id, filename, file_path, student_id, match_status, match_note,
                ocr_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?);
            """,
            (batch_id, logical_path, local_url, stu_id, match_status, match_note, now_str, now_str),
        )
        created_tasks.append({
            "task_id": cursor.lastrowid,
            "filename": logical_path,
            "file_path": local_url,
            "student_id": stu_id,
            "match_status": match_status,
            "match_note": match_note,
        })

    summary = {
        "total_images": len(created_tasks),
        "matched_count": matched_cnt,
        "unmatched_count": unmatched_cnt,
    }
    cursor.execute(
        "UPDATE import_batches SET summary_json = ? WHERE id = ?;",
        (json.dumps(summary, ensure_ascii=False), batch_id),
    )
    conn.commit()
    conn.close()

    if wait_for_completion:
        await process_homework_batch(batch_id)
    else:
        asyncio.create_task(process_homework_batch(batch_id))

    return {
        "success": True,
        "batch_id": batch_id,
        "status": "ready" if wait_for_completion else "processing",
        "summary": summary,
        "tasks": created_tasks,
    }


@router.get("/batches", dependencies=[Depends(verify_teacher_auth)])
def list_homework_batches():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, status, title, summary_json, created_at, updated_at
        FROM import_batches
        WHERE batch_type = 'homework_batch'
        ORDER BY created_at DESC LIMIT 20;
        """
    )
    batches = []
    for row in cursor.fetchall():
        b = dict(row)
        b["summary"] = json.loads(b.pop("summary_json") or "{}")
        batches.append(b)
    conn.close()
    return batches


@router.get("/batches/{batch_id}", dependencies=[Depends(verify_teacher_auth)])
def get_homework_batch_detail(batch_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM import_batches WHERE id = ?;", (batch_id,))
    b_row = cursor.fetchone()
    if not b_row:
        conn.close()
        raise HTTPException(status_code=404, detail="作业批次不存在")

    cursor.execute(
        """
        SELECT t.*, s.name AS student_name, s.student_no AS student_no, s.anonymous_code
        FROM ocr_tasks t
        LEFT JOIN students s ON t.student_id = s.id
        WHERE t.batch_id = ?
        ORDER BY t.id ASC;
        """,
        (batch_id,),
    )
    tasks = []
    for t in cursor.fetchall():
        td = dict(t)
        td["parsed_items"] = json.loads(td.pop("parsed_items_json") or "[]")
        tasks.append(td)
    conn.close()

    batch_dict = dict(b_row)
    batch_dict["summary"] = json.loads(batch_dict.get("summary_json") or "{}")
    batch_dict["tasks"] = tasks
    return batch_dict


@router.patch("/tasks/{task_id}/assign", dependencies=[Depends(verify_teacher_auth)])
def assign_task_student(task_id: int, req: AssignTaskStudentRequest):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, student_no, name FROM students WHERE id = ?;", (req.student_id,))
    stu = cursor.fetchone()
    if not stu:
        conn.close()
        raise HTTPException(status_code=404, detail="指定学生不存在")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        UPDATE ocr_tasks
        SET student_id = ?, match_status = 'matched', match_note = ?, updated_at = ?
        WHERE id = ?;
        """,
        (stu["id"], f"人工指派: {stu['student_no']}号 {stu['name']}", now_str, task_id),
    )
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="作业任务不存在")
    conn.commit()
    conn.close()
    return {"success": True, "task_id": task_id, "student_id": stu["id"]}


@router.post("/batches/{batch_id}/confirm", dependencies=[Depends(verify_teacher_auth)])
def confirm_homework_batch(batch_id: str, req: OcrBatchConfirmRequest):
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_inserted = 0

    for t_req in req.tasks:
        items_dicts = [it.model_dump() for it in t_req.items]
        inserted_ids = confirm_ocr_items_for_student(
            cursor, t_req.student_id, items_dicts, now_str, source_type="photo_ocr"
        )
        total_inserted += len(inserted_ids)
        cursor.execute(
            "UPDATE ocr_tasks SET student_id = ?, match_status = 'matched', ocr_status = 'confirmed', updated_at = ? WHERE id = ? AND batch_id = ?;",
            (t_req.student_id, now_str, t_req.task_id, batch_id),
        )

    cursor.execute(
        "UPDATE import_batches SET status = 'committed', updated_at = ? WHERE id = ?;",
        (now_str, batch_id),
    )
    conn.commit()
    conn.close()
    return {"success": True, "batch_id": batch_id, "inserted_count": total_inserted}


@batches_router.post("/from-dsh", dependencies=[Depends(verify_teacher_or_mcp_auth)])
async def upload_batch_from_dsh(
    files: List[UploadFile] = File(...),
    batch_type: str = Form("dsh_grade_sheet"),
    title: str = Form("DSH 批量处理任务"),
):
    """
    Bridge endpoint for DSH attachments.
    Runs Tencent OCR locally and strictly desensitizes real names before returning rows to DSH.
    """
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    os.makedirs(settings.BATCHES_DIR, exist_ok=True)
    batch_id = f"dsh_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if batch_type == "homework_batch":
        # Delegate to homework batch flow & wait so DSH receives summary immediately
        raw_pairs = [(f.filename, await f.read()) for f in files if f.filename]
        images = _collect_uploaded_images(raw_pairs)
        if not images:
            raise HTTPException(status_code=400, detail="未检测到有效作业图片")

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO import_batches (id, batch_type, source, status, title, summary_json, created_at, updated_at)
            VALUES (?, 'homework_batch', 'dsh', 'pending', ?, '{}', ?, ?);
            """,
            (batch_id, title, now_str, now_str),
        )
        for logical_path, img_bytes in images:
            local_url = compress_and_save_upload(io.BytesIO(img_bytes))
            stu_id, match_status, _ = match_homework_filename(cursor, logical_path)
            cursor.execute(
                """
                INSERT INTO ocr_tasks (batch_id, filename, file_path, student_id, match_status, ocr_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?);
                """,
                (batch_id, logical_path, local_url, stu_id, match_status, now_str, now_str),
            )
        conn.commit()
        conn.close()

        await process_homework_batch(batch_id)

        # Return strictly desensitized summary to DSH
        conn2 = get_connection()
        cur2 = conn2.cursor()
        cur2.execute(
            """
            SELECT t.id, t.filename, t.match_status, t.ocr_status, t.parsed_items_json, s.anonymous_code, s.student_no
            FROM ocr_tasks t
            LEFT JOIN students s ON t.student_id = s.id
            WHERE t.batch_id = ?;
            """,
            (batch_id,),
        )
        safe_tasks = []
        for row in cur2.fetchall():
            items = json.loads(row["parsed_items_json"] or "[]")
            safe_tasks.append({
                "task_id": row["id"],
                "filename": row["filename"],
                "ref": row["anonymous_code"] or "unmatched",
                "student_no": row["student_no"] or "",
                "match_status": row["match_status"],
                "ocr_status": row["ocr_status"],
                "wrong_questions": [
                    {
                        "q_no": it.get("q_no"),
                        "question_text": it.get("question_text"),
                        "kb_mother_id": it.get("kb_mother_id"),
                        "module_tag": it.get("module_tag"),
                        "error_tags": it.get("error_tags"),
                    }
                    for it in items if it.get("is_wrong")
                ],
            })
        conn2.close()
        return {"success": True, "batch_id": batch_id, "batch_type": "homework_batch", "tasks": safe_tasks}

    # Default: dsh_grade_sheet (extract tables from photos or spreadsheets & desensitize)
    combined_raw_rows: List[Dict[str, Any]] = []
    sources_used = []
    for f in files:
        if not f.filename:
            continue
        raw_bytes = await f.read()
        ext = os.path.splitext(f.filename)[1].lower()
        if ext in (".csv", ".xlsx", ".xlsm"):
            save_path = os.path.join(settings.BATCHES_DIR, f"{batch_id}_{os.path.basename(f.filename)}")
            with open(save_path, "wb") as fp:
                fp.write(raw_bytes)
            ocr_res = await recognize_grade_table_ocr(save_path, f.filename)
        elif ext in _IMAGE_EXTS:
            local_url = compress_and_save_upload(io.BytesIO(raw_bytes))
            ocr_res = await recognize_grade_table_ocr(local_url, f.filename)
        else:
            continue

        sources_used.append(ocr_res.get("source", "unknown"))
        combined_raw_rows.extend(ocr_res.get("rows", []))

    conn = get_connection()
    desensitized_rows, ref_map = desensitize_grade_rows(conn, combined_raw_rows)
    summary = {
        "sources": sources_used,
        "row_count": len(desensitized_rows),
        "ref_map": ref_map,  # Stored strictly in local SQLite, NEVER returned to DSH!
    }
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO import_batches (
            id, batch_type, source, status, title, raw_ocr_json, summary_json, created_at, updated_at
        ) VALUES (?, 'dsh_grade_sheet', 'dsh', 'ready', ?, ?, ?, ?, ?);
        """,
        (
            batch_id,
            title,
            json.dumps(desensitized_rows, ensure_ascii=False),
            json.dumps(summary, ensure_ascii=False),
            now_str,
            now_str,
        ),
    )
    conn.commit()
    conn.close()

    return {
        "success": True,
        "batch_id": batch_id,
        "batch_type": "dsh_grade_sheet",
        "sources": sources_used,
        "desensitized_rows": desensitized_rows,
    }
