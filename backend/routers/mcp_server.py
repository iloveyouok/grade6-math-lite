import json
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from backend.db import get_connection
from backend.routers.auth import verify_mcp_auth
from backend.routers.sheets import generate_sheets_for_students
from backend.routers.students import build_students_overview
from backend.schemas import DshImportPlanRequest
from backend.services.import_service import (
    commit_dsh_import_batch,
    preview_dsh_import_plan,
)
import backend.services.kb_retriever as kb_retriever

router = APIRouter(prefix="/mcp", tags=["mcp"], dependencies=[Depends(verify_mcp_auth)])

_MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "math_list_students",
        "description": "查询当前跟踪的学生学情概览与四板块状态灯（严格脱敏，仅返回匿名编码 anonymous_code 与座号，不含学生真实姓名）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean", "default": False, "description": "是否包含已归档移除出卷名单的学生"}
            },
        },
    },
    {
        "name": "math_list_exams",
        "description": "查询已有测验试卷列表及每份试卷的题号、对应母题ID与模块分类。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "math_list_mothers",
        "description": "查询六年级数学母题库元数据（ID、标题、模块标签、关键词标签）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "math_get_batch",
        "description": "查看指定 batch_id 批处理任务的 OCR 脱敏识别结果、任务进度或已有预览方案。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string", "description": "批处理批次ID"}
            },
            "required": ["batch_id"],
        },
    },
    {
        "name": "math_preview_import_plan",
        "description": "提交经过清洗筛选的学生导入与成绩/错题归因方案进行确定性校验与预览（不会直接写库）。必须先向教师展示预览并获确认。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "exam_title": {"type": "string"},
                "unit_tag": {"type": "string"},
                "exam_date": {"type": "string"},
                "total_score": {"type": "number", "default": 100},
                "filter_rule": {"type": "string"},
                "students_to_import": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string", "description": "anonymous_code 或 cand_xxx"},
                            "student_no": {"type": "string"},
                            "class_name": {"type": "string"},
                            "score": {"type": ["number", "null"]},
                            "focus_tier": {"type": "string", "enum": ["critical", "watchlist", "archived"]},
                            "recommended": {"type": "boolean"},
                            "reason": {"type": "string"},
                            "warning": {"type": "string"},
                        },
                        "required": ["ref", "student_no"],
                    },
                },
                "mistakes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string"},
                            "q_no": {"type": "string"},
                            "kb_mother_id": {"type": "string"},
                            "module_tag": {"type": "string", "enum": ["calc", "fraction", "ratio", "geometry"]},
                            "error_tags": {"type": "array", "items": {"type": "string"}},
                            "question_snapshot": {"type": "string"},
                            "face_to_face_script": {"type": "string"},
                        },
                        "required": ["ref", "question_snapshot"],
                    },
                },
            },
            "required": ["batch_id", "students_to_import"],
        },
    },
    {
        "name": "math_commit_import_batch",
        "description": "仅在教师明确回复确认后调用：将已预览的 batch_id 正式写入本地 SQLite（students / exam_scores / mistake_records）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "confirmed_by_user": {"type": "boolean", "description": "必须为 true 才会写库"},
            },
            "required": ["batch_id", "confirmed_by_user"],
        },
    },
    {
        "name": "math_generate_sheets",
        "description": "为指定学生（或全部当前 critical 重点学生）生成本周8题巩固单并导出合并 A4 PDF。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "student_ids": {"type": "array", "items": {"type": "integer"}},
                "batch_title": {"type": "string", "default": "周末数学冲刺巩固单"},
            },
        },
    },
]


def _tool_result(data: Any, is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def _exec_list_students(args: Dict[str, Any]) -> Dict[str, Any]:
    include_archived = bool(args.get("include_archived", False))
    overview = build_students_overview(include_archived=include_archived, mask_names=True)
    return overview.model_dump()


def _exec_list_exams() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, unit_tag, exam_date, total_score FROM exam_papers ORDER BY id DESC LIMIT 10;")
    papers = [dict(r) for r in cursor.fetchall()]
    for p in papers:
        cursor.execute(
            "SELECT id, q_no, kb_mother_id, module_tag, max_score FROM exam_questions WHERE paper_id = ? ORDER BY CAST(q_no AS INTEGER);",
            (p["id"],),
        )
        p["questions"] = [dict(q) for q in cursor.fetchall()]
    conn.close()
    return {"exam_papers": papers}


def _exec_list_mothers() -> Dict[str, Any]:
    kb_retriever.load_kb_vectors()
    return {
        "mothers": [
            {
                "id": m.get("id"),
                "title": m.get("title"),
                "module_tag": m.get("module_tag"),
                "level": m.get("level"),
                "tags": m.get("tags", []),
            }
            for m in kb_retriever._KB_CACHE
        ]
    }


def _exec_get_batch(args: Dict[str, Any]) -> Dict[str, Any]:
    batch_id = args["batch_id"]
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, batch_type, source, status, title, paper_id, raw_ocr_json, preview_plan_json, created_at, updated_at FROM import_batches WHERE id = ?;",
        (batch_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise ValueError(f"批次不存在: {batch_id}")
    data = dict(row)
    data["raw_ocr"] = json.loads(data.pop("raw_ocr_json") or "[]")
    data["preview_plan"] = json.loads(data.pop("preview_plan_json") or "{}")
    return data


async def _handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "math_list_students":
        return _tool_result(_exec_list_students(args))
    if name == "math_list_exams":
        return _tool_result(_exec_list_exams())
    if name == "math_list_mothers":
        return _tool_result(_exec_list_mothers())
    if name == "math_get_batch":
        return _tool_result(_exec_get_batch(args))
    if name == "math_preview_import_plan":
        req = DshImportPlanRequest(**args)
        conn = get_connection()
        res = preview_dsh_import_plan(conn, req)
        conn.close()
        return _tool_result(res)
    if name == "math_commit_import_batch":
        conn = get_connection()
        res = commit_dsh_import_batch(
            conn,
            batch_id=args["batch_id"],
            confirmed_by_user=bool(args.get("confirmed_by_user", False)),
        )
        conn.close()
        return _tool_result(res)
    if name == "math_generate_sheets":
        student_ids = args.get("student_ids") or []
        if not student_ids:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE focus_tier = 'critical' ORDER BY student_no ASC;")
            student_ids = [r["id"] for r in cur.fetchall()]
            conn.close()
        res = await generate_sheets_for_students(student_ids, args.get("batch_title"))
        return _tool_result(res)
    return _tool_result({"error": f"Unknown tool: {name}"}, is_error=True)


@router.post("/math")
async def mcp_math_endpoint(request: Request):
    """
    Streamable HTTP MCP endpoint for DeepSeek Harness (@deepseek-ai/dsh-mcp-client).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}, status_code=400)

    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "notifications/initialized":
        return JSONResponse({}, status_code=202)

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "grade6-math-mcp", "version": "1.0.0"},
            },
        })

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": _MCP_TOOLS}})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            res = await _handle_tool_call(tool_name, tool_args)
        except Exception as exc:
            res = _tool_result({"error": str(exc)}, is_error=True)
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": res})

    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    })
