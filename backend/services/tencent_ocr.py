import os
import json
import time
import asyncio
from typing import List, Dict, Any
from backend.config import settings
from backend.services.image_crop import crop_question_image
from backend.services.kb_retriever import retrieve_mother_questions

async def submit_and_poll_tencent_ocr(image_path: str) -> List[Dict[str, Any]]:
    """
    Submits image to Tencent Cloud Question Mark Agent and polls for results.
    In MOCK_EXTERNAL_APIS=true, returns mock Grade 6 test correction results.
    """
    if settings.MOCK_EXTERNAL_APIS or not settings.TENCENTCLOUD_SECRET_ID or settings.TENCENTCLOUD_SECRET_ID == "mock_id":
        return await _mock_tencent_ocr_result(image_path)

    # Real Tencent Cloud Call
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.ocr.v20181119 import ocr_client, models

        cred = credential.Credential(settings.TENCENTCLOUD_SECRET_ID, settings.TENCENTCLOUD_SECRET_KEY)
        httpProfile = HttpProfile()
        httpProfile.endpoint = "ocr.tencentcloudapi.com"
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile
        client = ocr_client.OcrClient(cred, settings.TENCENTCLOUD_REGION, clientProfile)

        # Read image base64
        import base64
        real_path = image_path.lstrip("/") if image_path.startswith("/data/") else image_path
        with open(real_path, "rb") as f:
            base64_data = base64.b64encode(f.read()).decode("utf-8")

        # Submit Job
        req = models.SubmitQuestionMarkAgentJobRequest()
        req.ImageBase64 = base64_data
        req.ModelType = "youtu_correction_model"
        req.QuestionConfigMap = json.dumps({"QuickGradingEnabled": False, "StepCorrection": True})
        req.EnableImageCrop = True

        resp = client.SubmitQuestionMarkAgentJob(req)
        job_id = resp.JobId

        # Poll Job
        desc_req = models.DescribeQuestionMarkAgentJobRequest()
        desc_req.JobId = job_id

        max_polls = 30  # 30 * 3s = 90s timeout
        for _ in range(max_polls):
            await asyncio.sleep(3)
            desc_resp = client.DescribeQuestionMarkAgentJob(desc_req)
            status = desc_resp.JobStatus
            if status == "SUCCESS":
                return _parse_tencent_ocr_response(desc_resp, image_path)
            elif status in ("FAILED", "TIMEOUT"):
                raise RuntimeError(f"Tencent OCR job failed with status: {status}")

        raise TimeoutError("Tencent OCR job timed out after 90 seconds")
    except Exception as e:
        print(f"Tencent OCR real call failed: {e}. Falling back to mock result.")
        return await _mock_tencent_ocr_result(image_path)

def _parse_tencent_ocr_response(resp, original_image_path: str) -> List[Dict[str, Any]]:
    parsed_items = []
    # Parse questions from Tencent Cloud JSON response
    questions = getattr(resp, "QuestionDetails", []) or []
    for idx, q in enumerate(questions):
        q_text = getattr(q, "QuestionText", "") or f"第{idx+1}题"
        student_ans = getattr(q, "StudentAnswer", "") or ""
        result_status = getattr(q, "Result", "CORRECT")  # CORRECT / WRONG / PARTIAL
        is_wrong = result_status != "CORRECT"

        # Bounding box
        polygon = getattr(q, "QuestionPolygon", None)
        crop_url = ""
        if polygon:
            crop_url = crop_question_image(original_image_path, polygon)

        # Match mother question via in-memory RAG
        matched = retrieve_mother_questions(q_text, top_k=1)
        kb_mother_id = matched[0]["id"] if matched else "G6-CALC-01"
        module_tag = matched[0]["module_tag"] if matched else "calc"
        script = matched[0].get("sections", {}).get("2. 3分钟面批反问话术", "") if matched else ""

        parsed_items.append({
            "temp_id": f"q_{idx+1}",
            "q_no": str(idx + 1),
            "question_text": q_text,
            "student_answer": student_ans,
            "is_wrong": is_wrong,
            "score_status": "wrong" if is_wrong else "correct",
            "error_tags": ["计算粗心"] if is_wrong else [],
            "face_to_face_script": script,
            "kb_mother_id": kb_mother_id,
            "module_tag": module_tag,
            "crop_url": crop_url or original_image_path
        })
    return parsed_items

async def _mock_tencent_ocr_result(image_path: str) -> List[Dict[str, Any]]:
    """
    Returns 3 typical Grade 6 questions: 1 correct, 2 wrong.
    """
    await asyncio.sleep(0.3)  # Brief simulated latency

    # Generate cropped regions if image exists
    crop1 = crop_question_image(image_path, [20, 20, 480, 200])
    crop2 = crop_question_image(image_path, [20, 220, 480, 450])
    crop3 = crop_question_image(image_path, [20, 470, 480, 700])

    items = [
        {
            "temp_id": "ocr_q_1",
            "q_no": "1",
            "question_text": "计算：(3/4 - 1/6) * 12",
            "student_answer": "学生书写：3/4 * 12 - 1/6 = 9 - 1/6 = 8又5/6",
            "is_wrong": True,
            "score_status": "wrong",
            "error_tags": ["乘法分配律漏乘", "符号运算粗心"],
            "face_to_face_script": "乘法分配律展开时，括号里的每一项都要乘以括号外面的数。你看看第2项 1/6 有没有乘到 12？",
            "kb_mother_id": "G6-CALC-01",
            "module_tag": "calc",
            "crop_url": crop1
        },
        {
            "temp_id": "ocr_q_2",
            "q_no": "2",
            "question_text": "一个长方形花坛周长是 48 米，长和宽的比是 5:3。这个花坛的长和宽各是多少米？",
            "student_answer": "学生书写：48 * 5/8 = 30米，48 * 3/8 = 18米",
            "is_wrong": True,
            "score_status": "wrong",
            "error_tags": ["周长未除以2", "找错总量"],
            "face_to_face_script": "长方形的周长是 48 米，周长是由两个长和两个宽组成的。那么一个长和一个宽的和是多少？应该先算哪一步？",
            "kb_mother_id": "G6-RATIO-01",
            "module_tag": "ratio",
            "crop_url": crop2
        },
        {
            "temp_id": "ocr_q_3",
            "q_no": "3",
            "question_text": "计算：5/6 * 2/3 + 5/6 * 1/3",
            "student_answer": "学生书写：= 5/6 * (2/3 + 1/3) = 5/6 * 1 = 5/6",
            "is_wrong": False,
            "score_status": "correct",
            "error_tags": [],
            "face_to_face_script": "",
            "kb_mother_id": "G6-CALC-01",
            "module_tag": "calc",
            "crop_url": crop3
        }
    ]
    return items


async def recognize_grade_table_ocr(file_path: str, filename: str = "") -> Dict[str, Any]:
    """
    Extracts structured student score rows from an uploaded grade sheet (.csv/.xlsx or photo).
    In MOCK_EXTERNAL_APIS=true, returns realistic Grade 6 test table rows (40-58 critical, high-score, and edge remarks).
    """
    ext = (filename or file_path).rsplit(".", 1)[-1].lower()
    if ext in ("csv", "xlsx", "xlsm"):
        from backend.services.desensitize import parse_roster_file
        real_path = file_path.lstrip("/") if file_path.startswith("/data/") else file_path
        with open(real_path, "rb") as f:
            rows = parse_roster_file(filename or file_path, f.read())
        return {"source": "spreadsheet", "rows": rows}

    if settings.MOCK_EXTERNAL_APIS or not settings.TENCENTCLOUD_SECRET_ID or settings.TENCENTCLOUD_SECRET_ID == "mock_id":
        await asyncio.sleep(0.15)
        return {
            "source": "mock_table_ocr",
            "rows": [
                {"student_no": "01", "name": "张伟", "class_name": "六(1)班", "score": 49, "notes": "分数计算丢分", "question_details": [{"q_no": "3", "wrong": True, "topic": "乘法分配律"}]},
                {"student_no": "08", "name": "李芳", "class_name": "六(1)班", "score": 44, "notes": "退步明显", "question_details": [{"q_no": "11", "wrong": True, "topic": "长方形周长按比分配"}]},
                {"student_no": "12", "name": "周子涵", "class_name": "六(1)班", "score": 53, "notes": "新进临界区间", "question_details": [{"q_no": "7", "wrong": True, "topic": "工程问题单位1"}]},
                {"student_no": "19", "name": "林小雨", "class_name": "六(1)班", "score": 57, "notes": "圆面积平方算错", "question_details": [{"q_no": "15", "wrong": True, "topic": "圆的面积"}]},
                {"student_no": "25", "name": "刘思远", "class_name": "六(1)班", "score": 84, "notes": "达标优秀"},
                {"student_no": "29", "name": "孙浩然", "class_name": "六(1)班", "score": "病假", "notes": "本次缺考"}
            ]
        }

    # Real Tencent Cloud Table Accurate OCR
    try:
        import base64
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.ocr.v20181119 import ocr_client, models

        cred = credential.Credential(settings.TENCENTCLOUD_SECRET_ID, settings.TENCENTCLOUD_SECRET_KEY)
        httpProfile = HttpProfile()
        httpProfile.endpoint = "ocr.tencentcloudapi.com"
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile
        client = ocr_client.OcrClient(cred, settings.TENCENTCLOUD_REGION, clientProfile)

        real_path = file_path.lstrip("/") if file_path.startswith("/data/") else file_path
        with open(real_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        req = models.RecognizeTableAccurateOCRRequest()
        req.ImageBase64 = b64
        resp = client.RecognizeTableAccurateOCR(req)

        rows: List[Dict[str, Any]] = []
        for tbl in (getattr(resp, "TableDetections", []) or []):
            cells = getattr(tbl, "Cells", []) or []
            row_map: Dict[int, Dict[int, str]] = {}
            for cell in cells:
                r_idx = int(getattr(cell, "RowTl", 0))
                c_idx = int(getattr(cell, "ColTl", 0))
                txt = (getattr(cell, "Text", "") or "").strip()
                row_map.setdefault(r_idx, {})[c_idx] = txt
            sorted_r = [row_map[k] for k in sorted(row_map.keys())]
            if len(sorted_r) < 2:
                continue
            max_cols = max(max(r.keys(), default=0) for r in sorted_r) + 1
            header = [sorted_r[0].get(c, f"col_{c}") for c in range(max_cols)]
            for r in sorted_r[1:]:
                vals = [r.get(c, "") for c in range(max_cols)]
                if any(v.strip() for v in vals):
                    raw_obj = {header[i]: vals[i] for i in range(max_cols)}
                    rows.append({
                        "student_no": raw_obj.get("座号") or raw_obj.get("学号") or vals[0],
                        "name": raw_obj.get("姓名") or (vals[1] if len(vals) > 1 else ""),
                        "class_name": raw_obj.get("班级") or "六(1)班",
                        "score": raw_obj.get("分数") or raw_obj.get("成绩") or raw_obj.get("总分") or (vals[2] if len(vals) > 2 else ""),
                        "notes": raw_obj.get("备注") or "",
                        "raw_cells": raw_obj,
                    })
        return {"source": "tencent_table_ocr", "rows": rows}
    except Exception as e:
        print(f"Tencent Table OCR failed: {e}, returning empty rows for review.")
        return {"source": "tencent_table_ocr_error", "error": str(e), "rows": []}


async def extract_header_identity_ocr(image_path: str) -> Dict[str, str]:
    """
    Reads top 18% header strip of a homework image to guess student_no / name locally.
    """
    if settings.MOCK_EXTERNAL_APIS or not settings.TENCENTCLOUD_SECRET_ID or settings.TENCENTCLOUD_SECRET_ID == "mock_id":
        return {"student_no": "", "name": ""}

    try:
        import base64
        import io
        import re
        from PIL import Image
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.ocr.v20181119 import ocr_client, models

        real_path = image_path.lstrip("/") if image_path.startswith("/data/") else image_path
        img = Image.open(real_path)
        w, h = img.size
        crop = img.crop((0, 0, w, max(80, int(h * 0.18))))
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        cred = credential.Credential(settings.TENCENTCLOUD_SECRET_ID, settings.TENCENTCLOUD_SECRET_KEY)
        httpProfile = HttpProfile()
        httpProfile.endpoint = "ocr.tencentcloudapi.com"
        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile
        client = ocr_client.OcrClient(cred, settings.TENCENTCLOUD_REGION, clientProfile)

        req = models.GeneralBasicOCRRequest()
        req.ImageBase64 = b64
        resp = client.GeneralBasicOCR(req)
        lines = [getattr(d, "DetectedText", "") for d in (getattr(resp, "TextDetections", []) or [])]
        joined = " ".join(lines)
        no_m = re.search(r"(?:学号|座号|编号)\s*[:：]?\s*(\d{1,3})", joined)
        name_m = re.search(r"(?:姓名)\s*[:：]?\s*([\u4e00-\u9fa5]{2,4})", joined)
        return {
            "student_no": no_m.group(1) if no_m else "",
            "name": name_m.group(1) if name_m else "",
            "raw_text": joined[:200],
        }
    except Exception:
        return {"student_no": "", "name": ""}
