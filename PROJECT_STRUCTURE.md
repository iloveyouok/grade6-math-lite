# 小学六年级数学“保及格”教师端工作台 · 工程架构与业务全景文档

> **项目目标**：专攻小学六年级 **40–58 分临界生**，通过“动态筛人 → 错题建档 → 周五出卷 → 周一验收”闭环稳拿基础分、冲刺 **65–70 分**。  
> **运行形态**：纯 Python 单进程 FastAPI 微内核 + SQLite WAL + 免打包 Vue 3 CDN 前端，支持与外部 **DeepSeek Harness (DSH)** 通过 Streamable HTTP MCP 联动批处理。

---

## 1. 整体系统架构图

```mermaid
flowchart TD
    subgraph IN["接入终端与编排入口"]
        A1["教师电脑工作台 (Vue 3 CDN)"]
        A2["手机微信扫码拍照 (/upload.html)"]
        A3["DeepSeek Harness (DSH 对话台)"]
    end

    subgraph NET["网关与反向代理层"]
        B["Nginx (:80 / :443 HTTPS)\n- client_max_body_size 50M\n- gzip 压缩与静态转发"]
    end

    subgraph CORE["FastAPI 单进程微内核 (127.0.0.1:8000)"]
        C1["鉴权中间件 (routers/auth.py)\n- 教师 Session Cookie\n- MCP_AUTH_TOKEN Bearer 鉴权"]
        C2["静态前端与打印模板托管"]

        subgraph ROUTERS["7 大业务与协议路由 (routers/)"]
            R1["students.py: 看板状态灯 / CSV&XLSX筛选导入 / 分层归档"]
            R2["exams.py: 考后10秒勾题连录"]
            R3["ocr_upload.py: 单图/多图/ZIP批量队列 & DSH上传桥接"]
            R4["sheets.py: 周五8题组卷与周一5秒反向验收"]
            R5["kb.py: 母题库查阅"]
            R6["mcp_server.py: Streamable HTTP MCP (/mcp/math)"]
        end

        subgraph SERVICES["纯 Python 核心服务层 (services/)"]
            S1["kb_retriever.py: NumPy 内存余弦检索 (<5ms)"]
            S2["math_sandbox.py: AST 白名单 + python -I 验算沙箱"]
            S3["pdf_renderer.py: Playwright 按需启停导出 A4 PDF"]
            S4["image_crop.py: Pillow 压缩(<=2000px)与切图"]
            S5["tencent_ocr.py: 批改Agent / 表格OCR / 页眉学号识别"]
            S6["desensitize.py: 花名册比对与真实姓名脱敏"]
            S7["import_service.py & batch_worker.py: 预览校验与 Semaphore(2) 队列"]
            S8["llm_service.py: 脱敏归因 / 变式题生成 / 家长周报"]
        end
    end

    subgraph DATA["本地持久化存储 (data/ 与 knowledge_base/)"]
        D1[("SQLite WAL (data/app.db)\n10 张业务与批处理表")]
        D2["母题向量索引 (data/kb_vectors.json)"]
        D3["图片与PDF资产 (uploads/ cropped/ pdfs/ batches/)"]
    end

    subgraph EXT["云端按量 API (MOCK_EXTERNAL_APIS=false 时启用)"]
        E1["腾讯云 OCR (试题批改 Agent + 表格识别)"]
        E2["阿里云百炼 DashScope (Embedding + Rerank)"]
        E3["DeepSeek API (deepseek-chat JSON 输出)"]
    end

    A1 -->|"HTTP/HTTPS"| B
    A2 -->|"HTTP/HTTPS"| B
    A3 -->|"上传桥接 /api/batches/from-dsh"| B
    A3 <-->|"Streamable HTTP /mcp/math"| B
    B --> C1
    C1 --> C2
    C1 --> R1 & R2 & R3 & R4 & R5 & R6
    R1 & R2 & R3 & R4 & R5 & R6 --> S1 & S2 & S3 & S4 & S5 & S6 & S7 & S8
    S1 & S2 & S3 & S4 & S6 & S7 --> D1 & D2 & D3
    S1 -.-> E2
    S5 -.-> E1
    S8 -.-> E3
```

---

## 2. 核心文件职责明细表

| 层次 / 目录 | 文件路径 | 职责与技术说明 |
| :--- | :--- | :--- |
| **配置** | `backend/config.py` | 管理 `MOCK_EXTERNAL_APIS`、`SEED_DEMO_DATA`、`MCP_AUTH_TOKEN`、密钥与目录路径。 |
| **数据库** | `backend/db.py` | 初始化 SQLite WAL 与 10 张表；为 `(class_name, student_no)` 建唯一索引；按开关控制演示数据注入。 |
| **数据契约** | `backend/schemas.py` | 定义学生导入预览/提交、批量 OCR、DSH 计划校验、组卷与验收全部 Pydantic 模型。 |
| **鉴权** | `backend/routers/auth.py` | 提供教师 Session Cookie 验证与恒定时间比对的 `MCP_AUTH_TOKEN` Bearer 鉴权。 |
| **学生与导入** | `backend/routers/students.py` | 看板状态灯（排除 `archived`）、`.csv`/`.xlsx` 解析筛选预览、提交建档、层级切换、DSH 批次核对。 |
| **考后录错** | `backend/routers/exams.py` | 键盘连录成绩与错题号；加权更新 `current_est_score`，重复出错自动递增 `wrong_count`。 |
| **OCR与批处理** | `backend/routers/ocr_upload.py` | 支持单图/手机带 `student_id` 直传、多图与 `.zip` 批量入队、人工指派学生及 `/api/batches/from-dsh` 桥接。 |
| **巩固单** | `backend/routers/sheets.py` | 固定“3热身 + 3错题重练 + 2变式”8题组卷，调用沙箱验算与 Playwright 合并导出 A4 PDF；支持周一 5 秒验收。 |
| **MCP服务** | `backend/routers/mcp_server.py` | 暴露 `/mcp/math` Streamable HTTP 接口（7 个工具），供 DSH 在脱敏约束下查询、预览与确认写库。 |
| **脱敏服务** | `backend/services/desensitize.py` | 解析 CSV/XLSX、本地匹配学生花名册、将真实姓名替换为 `anonymous_code` 或 `cand_xxx`、文件名匹配学生。 |
| **导入编排** | `backend/services/import_service.py` | 统一承载 CSV 筛选预览、DSH 导入计划 Pydantic 校验、原子写入 `students`/`exam_scores`/`mistake_records`。 |
| **异步队列** | `backend/services/batch_worker.py` | 以 `asyncio.Semaphore(2)` 并发限流执行批量作业 OCR 与页眉学号匹配，防止低配机器 OOM。 |
| **母题检索** | `backend/services/kb_retriever.py` | NumPy 矩阵点积计算余弦相似度 + 领域关键词加权，内存约 0.5 MB，召回耗时约 1–2 ms。 |
| **算术沙箱** | `backend/services/math_sandbox.py` | `ast.parse` 静态白名单封禁危险调用，`python -I` 子进程 2 秒超时执行 `Fraction` 等价性验算。 |
| **PDF渲染** | `backend/services/pdf_renderer.py` | 仅出卷时拉起 headless Chromium 渲染 `print_sheet.html` 为 A4 PDF，生成后立刻 `close()` 释放内存。 |
| **前端工作台** | `frontend/index.html`、`frontend/static/app.js` | 免打包 Vue 3 单页应用，涵盖看板、筛选导入弹窗、DSH 核对、批量作业队列、印卷与周一验收抽屉。 |
| **DSH 接入包** | `deploy/dsh/` | 包含附件上传桥接脚本 `upload_batch_to_math.py` 与 `grade6-math-intake/SKILL.md` 操作约束规范。 |

---

## 3. 数据库 10 张核心表关系模型

```mermaid
erDiagram
    students ||--o{ exam_scores : "产生分数"
    students ||--o{ mistake_records : "记录错题"
    students ||--o{ consolidation_sheets : "每周生成"
    students ||--o{ ocr_tasks : "归属作业图"

    exam_papers ||--o{ exam_questions : "包含题目"
    exam_papers ||--o{ exam_scores : "记录成绩"
    exam_papers ||--o{ import_batches : "关联导入批次"
    exam_questions ||--o{ mistake_records : "关联考点"

    consolidation_sheets ||--o{ sheet_items : "包含8道题"
    mistake_records ||--o{ sheet_items : "重练来源"
    variant_pool ||--o{ sheet_items : "变式题抽取"

    import_batches ||--o{ ocr_tasks : "拆分识图任务"

    students {
        int id PK
        string student_no "座号"
        string name "真实姓名(仅存本地)"
        string anonymous_code "脱敏编码(传外部API)"
        string class_name "与student_no组成联合唯一键"
        string focus_tier "critical | watchlist | archived"
        float baseline_score "基线分"
        float current_est_score "当前估分"
        string l3_summary_override "学情总结"
    }

    mistake_records {
        int id PK
        int student_id FK
        string source_type "exam_tick | photo_ocr | dsh_batch"
        string kb_mother_id
        string module_tag
        string question_snapshot "题干快照"
        string error_tags "错因JSON"
        string photo_url "错题切图URL"
        string face_to_face_script "3分钟面批话术"
        string mastery_status "unmastered | passed"
        int wrong_count "错误次数(>=2为顽固)"
    }

    import_batches {
        string id PK
        string batch_type "student_csv | homework_batch | dsh_grade_sheet"
        string source "workbench | dsh | mobile"
        string status "pending | processing | ready | previewed | committed"
        string title
        int paper_id FK
        string raw_ocr_json "脱敏后的OCR结果"
        string preview_plan_json "待确认计划"
        string summary_json "本地ref_map等摘要(不外泄)"
    }

    ocr_tasks {
        int id PK
        string batch_id FK
        string filename
        string file_path
        int student_id FK
        string match_status "matched | unmatched | skipped"
        string ocr_status "pending | processing | ready | confirmed | failed"
        string parsed_items_json
    }
```

---

## 4. 核心业务闭环流程

```mermaid
flowchart TD
    subgraph P1["1. 动态筛选导入学生 & 批改入口"]
        CSV["上传 .csv / .xlsx 成绩单"] --> FILT["按分数区间(40-58)或退步幅度筛选预览"]
        FILT --> STU_IN["确认建档入库 students (生成 anonymous_code)"]
        DSH["DSH 对话台传成绩单/截图"] -->|"upload_batch_to_math.py"| OCR_T["腾讯表格OCR + 本地花名册脱敏"]
        OCR_T -->|"仅返脱敏JSON"| DSH
        DSH -->|"MCP math_preview_import_plan"| PREVIEW["生成预览批次 (status=previewed)"]
        PREVIEW -->|"教师在DSH或网页端确认"| COMMIT["math_commit_import_batch 原子落库"]
    end

    subgraph P2["2. 多路径错题采集"]
        TICK["考后10秒勾题号 (/api/exams/batch-tick)"] --> MR[("mistake_records")]
        BATCH_HW["多图/ZIP批量传作业 (/api/ocr/batch-upload)\n或手机扫码 ?student_id= 直传"] --> Q["Semaphore(2) 后台腾讯批改OCR + 母题RAG"]
        Q -->|"未匹配人工指派 + 确认入库"| MR
        COMMIT --> MR
    end

    subgraph P3["3. 周五一键组卷 & 周一 5 秒验收"]
        MR --> GEN["POST /api/sheets/generate\n3热身 + 3错题重练 + 2变式"]
        GEN --> SB["math_sandbox AST+Fraction 验算"]
        SB --> PDF["Playwright 按需生成 A4 PDF 后立刻释放"]
        PDF --> MON["周一验收默认全过 · 反选1-2道未过题"]
        MON -->|"passed消项 / failed wrong_count+1"| MR
    end
```

---

## 5. 多设备开发与跨服务器迁移约定

1. **多设备共享源码**：所有架构与部署规范收敛在 `README.md`、`PROJECT_STRUCTURE.md`、`DEPLOY_AND_DSH.md`，本地修改后务必运行 `python scripts/verify_e2e.py` 通过再推送 GitHub；禁止将 `.env` 与 `data/` 下运行数据提交至仓库。
2. **业务数据 10 秒迁移**：全部业务状态集中在 `data/app.db` 及 `data/uploads/`、`data/cropped/`、`data/pdfs/`、`data/batches/`，打包 `data/` 即可无缝迁移至新服务器。
