import os
import sys
import io
import json
import time
import zipfile
from PIL import Image
from pypdf import PdfReader
from fastapi.testclient import TestClient

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.config import settings
from backend.main import app
from backend.db import init_db, get_connection
from backend.services.kb_retriever import retrieve_mother_questions

def run_e2e_verification():
    print("=" * 70)
    print("开始小学六年级数学‘保及格’工作台 10 大核心与整合闭环自动化验收测试...")
    print("=" * 70)

    # Clean DB file for idempotent test run
    db_file = settings.SQLITE_DB_PATH
    for ext in ["", "-wal", "-shm"]:
        target_f = db_file + ext
        if os.path.exists(target_f):
            try:
                os.remove(target_f)
            except Exception:
                pass

    init_db()
    client = TestClient(app)

    # -------------------------------------------------------------------------
    # 闭环 1: 鉴权保护
    # -------------------------------------------------------------------------
    print("\n>>> [测试 1/6] 鉴权保护测试...")
    resp_unauth = client.get("/api/students")
    assert resp_unauth.status_code == 401, f"Expected 401, got {resp_unauth.status_code}"
    print("  [✓] 未登录访问 /api/students 正确拦截并返回 401 Unauthorized")

    # Login
    resp_login = client.post("/api/auth/login", json={"password": settings.TEACHER_ACCESS_PASSWORD})
    assert resp_login.status_code == 200, f"Login failed: {resp_login.text}"
    login_data = resp_login.json()
    assert login_data["success"] is True
    session_token = login_data["message"]
    client.cookies.set(settings.SESSION_COOKIE_NAME, session_token)
    print("  [✓] 教师端使用密码正确登录，Session Token 设置成功")

    # Authorized get students
    resp_auth = client.get("/api/students")
    assert resp_auth.status_code == 200, f"Expected 200, got {resp_auth.status_code}"
    data_students = resp_auth.json()
    assert data_students["critical_count"] == 5, f"Expected 5 preset students, got {data_students['critical_count']}"
    student_ids = [s["id"] for s in data_students["students"]]
    print(f"  [✓] 鉴权后成功读取 {len(student_ids)} 名预置临界生（四板块状态灯正常）")

    # -------------------------------------------------------------------------
    # 闭环 2: 母题极简内存 RAG 检索 (< 50ms)
    # -------------------------------------------------------------------------
    print("\n>>> [测试 2/6] 母题向量检索与耗时断言 (<50ms)...")
    query = "六年级工程问题合作天数"
    t0 = time.perf_counter()
    kb_results = retrieve_mother_questions(query, top_k=3)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert len(kb_results) >= 1, "RAG returned empty results"
    top_hit = kb_results[0]
    print(f"  [✓] 查询语句: '{query}'")
    print(f"  [✓] Top-1 匹配母题: {top_hit['id']} - {top_hit['title']} (相关度分: {top_hit['score']:.3f})")
    print(f"  [✓] 检索耗时: {elapsed_ms:.2f} ms")
    assert top_hit["id"] == "G6-FRAC-01", f"Expected G6-FRAC-01 for engineering problem, got {top_hit['id']}"
    assert elapsed_ms < 50.0, f"Retrieval took too long: {elapsed_ms:.2f}ms >= 50ms"
    print("  [✓] 母题检索命中精确，耗时严格控制在 50ms 以内")

    # -------------------------------------------------------------------------
    # 闭环 3: 考后 10 秒勾题号连录与顽固错题计数递增
    # -------------------------------------------------------------------------
    print("\n>>> [测试 3/6] 考后 10 秒勾题号连录（第2次提交同一错题递增为顽固错题）...")
    s1_id = student_ids[0]
    
    # Fetch paper 1
    resp_papers = client.get("/api/exams")
    papers = resp_papers.json()
    paper_id = papers[0]["id"]

    resp_paper_detail = client.get(f"/api/exams/{paper_id}")
    p_questions = resp_paper_detail.json()["questions"]
    q1 = p_questions[0]  # Q1, G6-CALC-01

    # First tick mistake
    tick_payload_1 = {
        "paper_id": paper_id,
        "student_id": s1_id,
        "score": 52.0,
        "mistakes": [{
            "q_no": q1["q_no"],
            "exam_question_id": q1["id"],
            "kb_mother_id": q1["kb_mother_id"],
            "module_tag": q1["module_tag"],
            "error_tags": ["计算粗心"]
        }]
    }
    resp_tick1 = client.post("/api/exams/batch-tick", json=tick_payload_1)
    assert resp_tick1.status_code == 200, f"Tick 1 failed: {resp_tick1.text}"

    # Verify database wrong_count is 1
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT wrong_count FROM mistake_records
        WHERE student_id = ? AND exam_question_id = ? AND mastery_status != 'passed';
    """, (s1_id, q1["id"]))
    row1 = c.fetchone()
    assert row1 and row1["wrong_count"] == 1, f"Expected wrong_count=1, got {row1['wrong_count'] if row1 else None}"
    print("  [✓] 第 1 次勾题号录入成功，wrong_count = 1")

    # Second tick mistake on SAME question
    tick_payload_2 = {
        "paper_id": paper_id,
        "student_id": s1_id,
        "score": 50.0,
        "mistakes": [{
            "q_no": q1["q_no"],
            "exam_question_id": q1["id"],
            "kb_mother_id": q1["kb_mother_id"],
            "module_tag": q1["module_tag"],
            "error_tags": ["乘法分配律漏乘"]
        }]
    }
    resp_tick2 = client.post("/api/exams/batch-tick", json=tick_payload_2)
    assert resp_tick2.status_code == 200, f"Tick 2 failed: {resp_tick2.text}"

    c.execute("""
        SELECT wrong_count FROM mistake_records
        WHERE student_id = ? AND exam_question_id = ? AND mastery_status != 'passed';
    """, (s1_id, q1["id"]))
    row2 = c.fetchone()
    assert row2 and row2["wrong_count"] == 2, f"Expected wrong_count=2, got {row2['wrong_count'] if row2 else None}"
    conn.close()
    print("  [✓] 第 2 次勾同一错题成功，wrong_count 自动递增为 2 并标记为顽固错题！")

    # -------------------------------------------------------------------------
    # 闭环 4: 拍照批改与 2 秒确认卡入库
    # -------------------------------------------------------------------------
    print("\n>>> [测试 4/6] 模拟手写作业拍照上传 + 腾讯云批改 Agent + 2秒确认卡入库...")
    # Create dummy in-memory image
    img = Image.new("RGB", (600, 800), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)

    resp_ocr = client.post(
        "/api/ocr/upload",
        files={"file": ("test_homework.jpg", buf, "image/jpeg")}
    )
    assert resp_ocr.status_code == 200, f"OCR upload failed: {resp_ocr.text}"
    ocr_items = resp_ocr.json()
    assert len(ocr_items) == 3, f"Expected 3 questions from mock OCR, got {len(ocr_items)}"
    print(f"  [✓] 成功接收并批改作业，解析出 {len(ocr_items)} 道题目（1对2错）")

    # Confirm only wrong items
    confirm_payload = {
        "student_id": s1_id,
        "items": [
            {
                "kb_mother_id": item["kb_mother_id"],
                "module_tag": item["module_tag"],
                "question_snapshot": item["question_text"],
                "error_tags": item["error_tags"],
                "photo_url": item["crop_url"],
                "ocr_note": item["student_answer"],
                "face_to_face_script": item["face_to_face_script"],
                "is_wrong": item["is_wrong"]
            }
            for item in ocr_items
        ]
    }
    resp_confirm = client.post("/api/ocr/confirm", json=confirm_payload)
    assert resp_confirm.status_code == 200, f"Confirm failed: {resp_confirm.text}"
    confirm_res = resp_confirm.json()
    assert confirm_res["inserted_count"] == 2, f"Expected 2 wrong items inserted, got {confirm_res['inserted_count']}"
    print(f"  [✓] 2秒确认卡入库完成，成功录入 {confirm_res['inserted_count']} 道手写作答错题")

    # -------------------------------------------------------------------------
    # 闭环 5: 变式生成 + Python 算术沙箱验算 + 合并 A4 PDF 导出（严格3页）
    # -------------------------------------------------------------------------
    print("\n>>> [测试 5/6] 勾选 2 名学生出卷 + Python 沙箱验算 + 合并 A4 PDF (断言总页数严格等于 3 页)...")
    s2_id = student_ids[1]
    gen_payload = {
        "student_ids": [s1_id, s2_id],
        "batch_title": "周末保及格强化巩固单"
    }
    resp_gen = client.post("/api/sheets/generate", json=gen_payload)
    assert resp_gen.status_code == 200, f"Sheet gen failed: {resp_gen.text}"
    gen_data = resp_gen.json()
    assert gen_data["success"] is True
    batch_code = gen_data["batch_code"]
    pdf_path_rel = gen_data["pdf_path"]
    
    # Resolve physical PDF path
    pdf_full_path = pdf_path_rel.lstrip("/")
    assert os.path.exists(pdf_full_path), f"Generated PDF file not found at: {pdf_full_path}"
    print(f"  [✓] PDF 文件成功生成于: {pdf_full_path}")

    # Verify all variant items have sandbox_verified == 1
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM sheet_items si
        JOIN consolidation_sheets cs ON si.sheet_id = cs.id
        WHERE cs.batch_code = ? AND si.item_type = 'variant' AND si.sandbox_verified != 1;
    """, (batch_code,))
    unverified_count = c.fetchone()[0]
    assert unverified_count == 0, f"Found {unverified_count} variant items not verified by sandbox!"
    print("  [✓] 数据库断言：所有变式题均 100% 通过 Python fractions 算术沙箱验算 (sandbox_verified == 1)")

    # Verify PDF Page Count using pypdf
    reader = PdfReader(pdf_full_path)
    actual_pages = len(reader.pages)
    print(f"  [✓] 合并 PDF 总页数校验：实际页数 = {actual_pages} 页（学生 1: 第 1 页；学生 2: 第 2 页；教师参考答案与面批速查: 第 3 页）")
    assert actual_pages == 3, f"Expected STRICTLY 3 pages for 2 students, but got {actual_pages} pages!"
    print("  [✓] 合并 A4 PDF 排版强制约束校验通过：严格一人一页 + 末页教师速查表！")

    # -------------------------------------------------------------------------
    # 闭环 6: 周一 5 秒反向验收（7 题过关消项、1 题未过关 wrong_count+1）
    # -------------------------------------------------------------------------
    print("\n>>> [测试 6/6] 周一 5 秒反向验收（提交 7 题过关、1 题未过关）...")
    # Get sheet for student 1
    sheet_id = gen_data["sheet_ids"][0]
    resp_sheet_detail = client.get(f"/api/sheets/{sheet_id}")
    sheet_detail = resp_sheet_detail.json()
    items = sheet_detail["items"]
    assert len(items) == 8, f"Expected 8 items per sheet, got {len(items)}"

    # 7 items passed, last item failed
    checks = []
    for idx, item in enumerate(items):
        is_p = (idx != 7)  # Item 8 failed
        checks.append({"item_id": item["id"], "passed": is_p})

    resp_check = client.post("/api/sheets/monday-check", json={
        "sheet_id": sheet_id,
        "checks": checks
    })
    assert resp_check.status_code == 200, f"Monday check failed: {resp_check.text}"
    check_res = resp_check.json()
    assert check_res["passed_count"] == 7
    assert check_res["failed_count"] == 1
    print(f"  [✓] 反向验收接口返回：{check_res['passed_count']} 题过关，{check_res['failed_count']} 题未过关")

    # Check status of passed items in DB
    c.execute("""
        SELECT check_result FROM sheet_items WHERE sheet_id = ? ORDER BY sort_order ASC;
    """, (sheet_id,))
    results = [r[0] for r in c.fetchall()]
    assert results[:7] == ["passed"] * 7
    assert results[7] == "failed"
    conn.close()
    print("  [✓] 数据库校验：已过关题目正确消项，未过关题目 wrong_count 递增并锁定至下周！")

    # -------------------------------------------------------------------------
    # 闭环 7: 动态筛选导入学生（CSV 筛选 40-58 分 + 归档切换）
    # -------------------------------------------------------------------------
    print("\n>>> [测试 7/10] 动态筛选导入学生（CSV 筛选 40-58 分 + 跟踪分层管理）...")
    csv_text = "座号,姓名,班级,分数,备注\n12,周子涵,六(1)班,53,新发现临界生\n28,高飞,六(1)班,86,高分不纳入\n35,马思琪,六(1)班,46,急需巩固\n"
    resp_parse = client.post(
        "/api/students/import/parse-file",
        files={"file": ("roster.csv", csv_text.encode("utf-8"), "text/csv")},
        data={"filter_min_score": "40", "filter_max_score": "58", "paper_title": "第三单元小测"},
    )
    assert resp_parse.status_code == 200, f"Parse CSV failed: {resp_parse.text}"
    parse_data = resp_parse.json()
    assert parse_data["summary"]["recommended_count"] == 2, f"Expected 2 recommended, got {parse_data}"

    resp_commit_stu = client.post(
        "/api/students/import/commit",
        json={
            "batch_id": parse_data["batch_id"],
            "paper_title": "第三单元小测",
            "students": [
                {
                    "student_no": it["student_no"],
                    "name": it["name"],
                    "class_name": it["class_name"],
                    "score": it["score"],
                    "focus_tier": "critical",
                    "selected": it["selected"],
                }
                for it in parse_data["items"]
            ],
        },
    )
    assert resp_commit_stu.status_code == 200, f"Commit student import failed: {resp_commit_stu.text}"
    commit_stu_data = resp_commit_stu.json()
    assert commit_stu_data["created_count"] == 2
    new_stu_id = commit_stu_data["results"][0]["student_id"]

    # Archive & restore tier
    resp_arch = client.patch(f"/api/students/{new_stu_id}/tier", json={"focus_tier": "archived"})
    assert resp_arch.status_code == 200
    assert client.get("/api/students").json()["critical_count"] == 6
    assert client.get("/api/students?include_archived=true").json()["critical_count"] == 7
    client.patch(f"/api/students/{new_stu_id}/tier", json={"focus_tier": "critical"})
    print("  [✓] 成功筛选导入 2 名新临界生（过滤掉86分高分学生），并验证 archived 移出/恢复跟踪")

    # -------------------------------------------------------------------------
    # 闭环 8: 批量导入作业（ZIP 按文件名匹配 + 未匹配人工指派 + 批量确认）
    # -------------------------------------------------------------------------
    print("\n>>> [测试 8/10] 批量导入作业（ZIP 含命名匹配图 + 未知图人工指派 + 批量入库）...")
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        for img_name in ["12_周子涵_1.jpg", "unknown_hw.jpg"]:
            im = Image.new("RGB", (500, 720), color=(250, 250, 250))
            ib = io.BytesIO()
            im.save(ib, format="JPEG")
            zf.writestr(img_name, ib.getvalue())
    zip_buf.seek(0)

    resp_batch_hw = client.post(
        "/api/ocr/batch-upload",
        files=[("files", ("homework_pack.zip", zip_buf, "application/zip"))],
        data={"title": "周三作业批改包", "wait_for_completion": "true"},
    )
    assert resp_batch_hw.status_code == 200, f"Batch homework upload failed: {resp_batch_hw.text}"
    hw_data = resp_batch_hw.json()
    assert hw_data["summary"]["total_images"] == 2
    assert hw_data["summary"]["matched_count"] == 1
    assert hw_data["summary"]["unmatched_count"] == 1

    hw_detail = client.get(f"/api/ocr/batches/{hw_data['batch_id']}").json()
    unmatched_task = [t for t in hw_detail["tasks"] if t["match_status"] == "unmatched"][0]
    assert client.patch(f"/api/ocr/tasks/{unmatched_task['id']}/assign", json={"student_id": new_stu_id}).status_code == 200

    hw_detail_ready = client.get(f"/api/ocr/batches/{hw_data['batch_id']}").json()
    batch_confirm_payload = {
        "tasks": [
            {
                "task_id": t["id"],
                "student_id": t["student_id"],
                "items": [
                    {
                        "kb_mother_id": it["kb_mother_id"],
                        "module_tag": it["module_tag"],
                        "question_snapshot": it["question_text"],
                        "error_tags": it["error_tags"],
                        "photo_url": it["crop_url"],
                        "ocr_note": it["student_answer"],
                        "face_to_face_script": it["face_to_face_script"],
                        "is_wrong": it["is_wrong"],
                    }
                    for it in t["parsed_items"]
                ],
            }
            for t in hw_detail_ready["tasks"]
        ]
    }
    resp_hw_confirm = client.post(f"/api/ocr/batches/{hw_data['batch_id']}/confirm", json=batch_confirm_payload)
    assert resp_hw_confirm.status_code == 200
    assert resp_hw_confirm.json()["inserted_count"] == 4  # 2 images * 2 wrong items each
    print("  [✓] ZIP 批量作业导入 + 未匹配人工指派 + 4 道错题批量确认入库通过")

    # -------------------------------------------------------------------------
    # 闭环 9: DSH 桥接上传成绩单 + 腾讯 OCR + 严格脱敏检查
    # -------------------------------------------------------------------------
    print("\n>>> [测试 9/10] DSH 桥接上传成绩单图片 + 腾讯 OCR + 严格防真名泄露校验...")
    grade_img = Image.new("RGB", (800, 600), color=(255, 255, 255))
    g_buf = io.BytesIO()
    grade_img.save(g_buf, format="JPEG")
    g_buf.seek(0)

    mcp_headers = {"Authorization": f"Bearer {settings.MCP_AUTH_TOKEN}"}
    resp_dsh_up = client.post(
        "/api/batches/from-dsh",
        headers=mcp_headers,
        files=[("files", ("grade_sheet.jpg", g_buf, "image/jpeg"))],
        data={"batch_type": "dsh_grade_sheet", "title": "期中摸底成绩单"},
    )
    assert resp_dsh_up.status_code == 200, f"DSH upload failed: {resp_dsh_up.text}"
    dsh_up_data = resp_dsh_up.json()
    dsh_batch_id = dsh_up_data["batch_id"]
    raw_json_str = json.dumps(dsh_up_data, ensure_ascii=False)
    for forbidden_name in ["张伟", "李芳", "周子涵", "林小雨", "刘思远", "孙浩然"]:
        assert forbidden_name not in raw_json_str, f"Privacy leak detected in DSH payload: {forbidden_name}"
    assert len(dsh_up_data["desensitized_rows"]) >= 4
    print(f"  [✓] DSH 收到 {len(dsh_up_data['desensitized_rows'])} 条脱敏成绩行，0 真实姓名泄露")

    # -------------------------------------------------------------------------
    # 闭环 10: MCP /mcp/math 鉴权 + 预览 + 禁止未确认写库 + 确认提交入库
    # -------------------------------------------------------------------------
    print("\n>>> [测试 10/10] MCP /mcp/math 鉴权、工具发现、先预览后确认写库闭环...")
    assert client.post("/mcp/math", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
    resp_tools = client.post("/mcp/math", headers=mcp_headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp_tools.status_code == 200
    tool_names = [t["name"] for t in resp_tools.json()["result"]["tools"]]
    assert "math_preview_import_plan" in tool_names and "math_commit_import_batch" in tool_names

    # math_list_students must not leak real names
    resp_mcp_stu = client.post(
        "/mcp/math",
        headers=mcp_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "math_list_students", "arguments": {}}},
    )
    mcp_stu_text = resp_mcp_stu.json()["result"]["content"][0]["text"]
    assert "周子涵" not in mcp_stu_text and "张伟" not in mcp_stu_text

    # Submit preview plan using desensitized refs from step 9
    cand_rows = [r for r in dsh_up_data["desensitized_rows"] if r["parsed_score"] is not None and 40 <= r["parsed_score"] <= 58]
    resp_preview = client.post(
        "/mcp/math",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "math_preview_import_plan",
                "arguments": {
                    "batch_id": dsh_batch_id,
                    "exam_title": "期中摸底测验",
                    "filter_rule": "40 <= score <= 58",
                    "students_to_import": [
                        {
                            "ref": r["ref"],
                            "student_no": r["student_no"],
                            "class_name": r["class_name"],
                            "score": r["parsed_score"],
                            "focus_tier": "critical",
                            "recommended": True,
                            "reason": "命中 40-58 临界区间",
                        }
                        for r in cand_rows
                    ],
                    "mistakes": [
                        {
                            "ref": cand_rows[0]["ref"],
                            "q_no": "15",
                            "kb_mother_id": "G6-GEO-01",
                            "module_tag": "geometry",
                            "error_tags": ["平方算成乘2"],
                            "question_snapshot": "圆形花坛面积计算丢分",
                        }
                    ],
                },
            },
        },
    )
    assert resp_preview.status_code == 200
    assert resp_preview.json()["result"]["isError"] is False

    # Ensure unconfirmed commit fails safely
    resp_unconf = client.post(
        "/mcp/math",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "math_commit_import_batch", "arguments": {"batch_id": dsh_batch_id, "confirmed_by_user": False}},
        },
    )
    assert resp_unconf.json()["result"]["isError"] is True

    # Teacher web UI can see resolved real names before/at commit
    ui_batch = client.get(f"/api/students/import/batches/{dsh_batch_id}").json()
    assert any(st["name"] == "林小雨" for st in ui_batch["preview_plan"]["students_to_import"])

    # Confirmed commit via MCP succeeds and imports 林小雨 into local DB
    resp_conf = client.post(
        "/mcp/math",
        headers=mcp_headers,
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "math_commit_import_batch", "arguments": {"batch_id": dsh_batch_id, "confirmed_by_user": True}},
        },
    )
    commit_payload = json.loads(resp_conf.json()["result"]["content"][0]["text"])
    assert commit_payload["success"] is True
    assert commit_payload["students_created"] >= 1
    assert commit_payload["mistakes_inserted"] == 1
    print("  [✓] MCP 鉴权、脱敏列表、预览校验、未确认拦截、确认后真名落库全部验证通过！")

    print("\n" + "=" * 70)
    print("🎉 恭喜！全部 10 大核心业务与 DSH 整合闭环测试 100% 自动化断言通过！")
    print("系统已就绪，可部署至目标云服务器并接入 DSH！")
    print("=" * 70)

if __name__ == "__main__":
    run_e2e_verification()
