import os
import json
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from backend.routers.auth import verify_teacher_auth
from backend.db import get_connection
from backend.schemas import OcrParsedQuestion, OcrConfirmRequest
from backend.services.image_crop import compress_and_save_upload
from backend.services.tencent_ocr import submit_and_poll_tencent_ocr

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

# Temporary in-memory cache for recent mobile uploads
LATEST_MOBILE_UPLOADS = []

@router.post("/upload", response_model=List[OcrParsedQuestion], dependencies=[Depends(verify_teacher_auth)])
async def upload_and_ocr(file: UploadFile = File(...)):
    """
    PC/Workbench photo upload and OCR analysis.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供有效文件")

    # 1. Compress & save
    local_url = compress_and_save_upload(file.file)

    # 2. Run Tencent OCR
    parsed_items = await submit_and_poll_tencent_ocr(local_url)
    return parsed_items

@router.post("/mobile-upload")
async def mobile_photo_upload(file: UploadFile = File(...)):
    """
    Direct mobile upload endpoint from WeChat scan (/upload.html).
    Allows mobile camera photo direct upload without requiring full teacher session login.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供有效文件")

    local_url = compress_and_save_upload(file.file)
    parsed_items = await submit_and_poll_tencent_ocr(local_url)

    upload_info = {
        "photo_url": local_url,
        "filename": file.filename,
        "uploaded_at": datetime.now().strftime("%H:%M:%S"),
        "parsed_items": parsed_items
    }
    LATEST_MOBILE_UPLOADS.insert(0, upload_info)
    if len(LATEST_MOBILE_UPLOADS) > 10:
        LATEST_MOBILE_UPLOADS.pop()

    return {"success": True, "photo_url": local_url, "item_count": len(parsed_items)}

@router.get("/latest-mobile", dependencies=[Depends(verify_teacher_auth)])
def get_latest_mobile_upload():
    """Polls latest mobile uploads to sync with workbench in real time."""
    if LATEST_MOBILE_UPLOADS:
        return {"has_new": True, "data": LATEST_MOBILE_UPLOADS[0]}
    return {"has_new": False, "data": None}

@router.post("/confirm", dependencies=[Depends(verify_teacher_auth)])
def confirm_ocr_mistakes(req: OcrConfirmRequest):
    """
    2-second confirm card batch insertion.
    Inserts confirmed wrong questions into mistake_records.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    inserted_ids = []
    for item in req.items:
        if not item.is_wrong:
            continue

        cursor.execute("""
            INSERT INTO mistake_records (
                student_id, source_type, kb_mother_id, module_tag, question_snapshot,
                error_tags, photo_url, ocr_note, face_to_face_script,
                mastery_status, wrong_count, created_at, updated_at
            ) VALUES (?, 'photo_ocr', ?, ?, ?, ?, ?, ?, ?, 'unmastered', 1, ?, ?);
        """, (
            req.student_id, item.kb_mother_id, item.module_tag, item.question_snapshot,
            json.dumps(item.error_tags, ensure_ascii=False), item.photo_url or "",
            item.ocr_note or "", item.face_to_face_script or "", now_str, now_str
        ))
        inserted_ids.append(cursor.lastrowid)

    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": f"成功将 {len(inserted_ids)} 道错题录入该生错题库",
        "inserted_count": len(inserted_ids),
        "inserted_ids": inserted_ids
    }
