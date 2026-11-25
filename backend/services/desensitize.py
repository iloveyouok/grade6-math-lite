import csv
import io
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple


_HEADER_ALIASES = {
    "student_no": {"座号", "学号", "编号", "序号", "no", "student_no", "id"},
    "name": {"姓名", "学生姓名", "名字", "name", "student_name"},
    "class_name": {"班级", "所在班级", "class", "class_name"},
    "score": {"分数", "成绩", "总分", "得分", "本次成绩", "score"},
    "notes": {"备注", "说明", "评语", "notes", "remark"},
}


def _normalize_header(raw_col: str) -> str:
    col = str(raw_col or "").strip().lower()
    for canonical, aliases in _HEADER_ALIASES.items():
        if col in aliases or any(alias in col for alias in aliases if len(alias) >= 2):
            return canonical
    return col


def _parse_float_score(val: Any) -> Tuple[Optional[float], str]:
    """Parses numeric score safely, preserving non-numeric remarks (e.g. 病假/缺考)."""
    if val is None:
        return None, ""
    if isinstance(val, (int, float)):
        return round(float(val), 1), ""
    text = str(val).strip()
    if not text:
        return None, ""
    # Strip trailing '分'
    cleaned = text.rstrip("分").strip()
    try:
        return round(float(cleaned), 1), ""
    except ValueError:
        return None, text


def parse_roster_file(filename: str, content: bytes) -> List[Dict[str, Any]]:
    """
    Parses .csv or .xlsx student/grade roster bytes into normalized dicts.
    """
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    raw_rows: List[List[Any]] = []

    if ext in ("xlsx", "xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            if any(cell is not None and str(cell).strip() for cell in row):
                raw_rows.append(list(row))
        wb.close()
    else:
        text = None
        for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("无法识别文件编码，请另存为 UTF-8 CSV 或 XLSX 格式")
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if any(str(cell).strip() for cell in row):
                raw_rows.append(row)

    if len(raw_rows) < 2:
        return []

    headers = [_normalize_header(c) for c in raw_rows[0]]
    parsed: List[Dict[str, Any]] = []
    for idx, r in enumerate(raw_rows[1:], start=1):
        row_map = {headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
        name = str(row_map.get("name", "")).strip()
        student_no = str(row_map.get("student_no", "")).strip()
        if student_no.endswith(".0"):
            student_no = student_no[:-2]
        if student_no.isdigit() and len(student_no) == 1:
            student_no = student_no.zfill(2)
        if not name and not student_no:
            continue

        class_name = str(row_map.get("class_name", "")).strip() or "六(1)班"
        score_val, score_note = _parse_float_score(row_map.get("score"))
        notes = str(row_map.get("notes", "")).strip()
        if score_note:
            notes = f"{notes} {score_note}".strip()

        parsed.append({
            "student_no": student_no or str(idx).zfill(2),
            "name": name or f"学生{student_no or idx}",
            "class_name": class_name,
            "score": score_val,
            "notes": notes,
        })
    return parsed


def generate_anonymous_code(cursor, class_name: str, student_no: str) -> str:
    """Generates a unique anonymous student code (never containing real name)."""
    digits = "".join(re.findall(r"\d+", class_name or "")) or "01"
    cls_part = digits[-2:].zfill(2)
    no_digits = "".join(re.findall(r"\d+", student_no or ""))
    no_part = no_digits.zfill(2) if no_digits else uuid.uuid4().hex[:4]
    candidate = f"g6_c{cls_part}_s{no_part}"

    cursor.execute("SELECT 1 FROM students WHERE anonymous_code = ? LIMIT 1;", (candidate,))
    if not cursor.fetchone():
        return candidate
    return f"g6_c{cls_part}_s{no_part}_{uuid.uuid4().hex[:4]}"


def match_student(
    cursor,
    raw_name: str = "",
    raw_no: str = "",
    class_name: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Matches an existing student locally by (class_name, student_no), unique student_no, or exact name.
    """
    no_clean = str(raw_no or "").strip()
    if no_clean.isdigit() and len(no_clean) == 1:
        no_clean = no_clean.zfill(2)
    name_clean = str(raw_name or "").strip()
    cls_clean = str(class_name or "").strip()

    if no_clean and cls_clean:
        cursor.execute(
            "SELECT * FROM students WHERE class_name = ? AND student_no = ? LIMIT 1;",
            (cls_clean, no_clean),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)

    if name_clean and cls_clean:
        cursor.execute(
            "SELECT * FROM students WHERE class_name = ? AND name = ? LIMIT 1;",
            (cls_clean, name_clean),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)

    if name_clean:
        cursor.execute("SELECT * FROM students WHERE name = ?;", (name_clean,))
        rows = cursor.fetchall()
        if len(rows) == 1:
            return dict(rows[0])

    if no_clean:
        cursor.execute("SELECT * FROM students WHERE student_no = ?;", (no_clean,))
        rows = cursor.fetchall()
        if len(rows) == 1:
            return dict(rows[0])

    return None


def desensitize_grade_rows(
    conn,
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Strips real names before exposing OCR/table rows to DSH/external LLMs.
    Returns (desensitized_rows, local_ref_map).
    local_ref_map stays strictly in local SQLite so real names never leave the server.
    """
    cursor = conn.cursor()
    desensitized: List[Dict[str, Any]] = []
    ref_map: Dict[str, Dict[str, Any]] = {}
    cand_seq = 1

    for idx, row in enumerate(rows, start=1):
        raw_name = str(row.get("name", "")).strip()
        raw_no = str(row.get("student_no", "")).strip()
        cls_name = str(row.get("class_name", "")).strip() or "六(1)班"
        matched = match_student(cursor, raw_name=raw_name, raw_no=raw_no, class_name=cls_name)

        if matched:
            ref = matched["anonymous_code"]
            prev_score = matched.get("current_est_score")
            matched_flag = True
            stu_id = matched["id"]
            canonical_no = matched["student_no"]
            canonical_cls = matched["class_name"]
            real_name = matched["name"]
        else:
            ref = f"cand_{cand_seq:03d}"
            cand_seq += 1
            prev_score = None
            matched_flag = False
            stu_id = None
            canonical_no = raw_no.zfill(2) if raw_no.isdigit() and len(raw_no) == 1 else (raw_no or f"{idx:02d}")
            canonical_cls = cls_name
            real_name = raw_name or f"未识别_{canonical_no}"

        raw_score = row.get("score")
        parsed_score, score_note = _parse_float_score(raw_score)
        notes = str(row.get("notes", "") or "").strip()
        if score_note and score_note not in notes:
            notes = f"{notes} {score_note}".strip()

        ref_map[ref] = {
            "ref": ref,
            "matched": matched_flag,
            "student_id": stu_id,
            "student_no": canonical_no,
            "name": real_name,
            "class_name": canonical_cls,
            "prev_score": prev_score,
        }

        safe_item = {
            "ref": ref,
            "matched": matched_flag,
            "student_no": canonical_no,
            "class_name": canonical_cls,
            "prev_score": prev_score,
            "raw_score_text": str(raw_score) if raw_score is not None else "",
            "parsed_score": parsed_score,
            "notes": notes,
        }
        if row.get("question_details"):
            safe_item["question_details"] = row["question_details"]
        desensitized.append(safe_item)

    return desensitized, ref_map


def match_homework_filename(
    cursor,
    path_str: str,
    explicit_student_id: Optional[int] = None,
) -> Tuple[Optional[int], str, str]:
    """
    Matches a homework image filename/subpath (e.g. '08_李芳_1.jpg' or '08/p1.jpg') to an active student.
    """
    if explicit_student_id:
        cursor.execute("SELECT id, student_no, name FROM students WHERE id = ? LIMIT 1;", (explicit_student_id,))
        stu = cursor.fetchone()
        if stu:
            return stu["id"], "matched", f"指定归属: {stu['student_no']}号 {stu['name']}"

    normalized = (path_str or "").replace("\\", "/")
    cursor.execute("SELECT id, student_no, name, class_name FROM students WHERE focus_tier != 'archived';")
    students = [dict(r) for r in cursor.fetchall()]

    # 1. Exact name match inside path/filename
    for stu in students:
        if stu["name"] and stu["name"] in normalized:
            return stu["id"], "matched", f"按文件名姓名匹配: {stu['student_no']}号 {stu['name']}"

    # 2. Match leading student_no token in folder or basename (e.g. '08_hw.jpg' or '08/p1.jpg')
    parts = [p for p in normalized.split("/") if p]
    tokens = []
    for part in parts:
        m = re.match(r"^(\d{1,3})(?:[_\-\s.]|$)", part)
        if m:
            tokens.append(m.group(1).zfill(2) if len(m.group(1)) == 1 else m.group(1))

    for tok in tokens:
        matched_nos = [s for s in students if s["student_no"] == tok]
        if len(matched_nos) == 1:
            stu = matched_nos[0]
            return stu["id"], "matched", f"按文件座号匹配: {stu['student_no']}号 {stu['name']}"

    return None, "unmatched", "未能从文件名识别学生，待人工或页眉OCR指派"
