# 小学六年级数学“保及格”教师端工作台 · 纯 Python 轻量全栈工程

专为 **低配置云服务器（2核 2GiB ~ 4核 4GiB Ubuntu 22.04）** 定制开发，同时支持与 **DeepSeek Harness (DSH)** 通过 MCP 联动批处理成绩单与作业。专攻小学六年级 **40–58 分临界生**，目标稳拿基础分冲刺 **65–70 分**。

---

## 1. 核心架构与设计原则

1. **纯 Python 微内核（零 Docker / 零独立向量库）**：
   - 全栈使用 **Python 3.10+ + FastAPI + SQLite (WAL模式) + NumPy 内存向量检索 + CDN 前端 (Vue 3 / Tailwind / KaTeX) + Playwright 按需唤醒**。
   - 生产常驻内存仅约 **70–100 MB**（含 `systemd` `MemoryHigh=450M` / `MemoryMax=600M` 保护）。
2. **Playwright 零常驻机制**：
   - 严禁在服务启动时初始化浏览器；仅在导出“A4 巩固单 PDF”时按需异步拉起 headless Chromium，渲染完立刻 `close()` 释放全部内存。
3. **动态筛选导入学生 + 批量作业异步队列**：
   - 支持上传 `.csv` / `.xlsx` 成绩单，按分数区间（默认 `40–58` 分）或退步幅度筛选建档，支持 `critical` / `watchlist` / `archived` 分层管理；可通过 `SEED_DEMO_DATA=false` 跳过内置演示学生。
   - 支持多图连传、`.zip` 打包上传与手机扫码锁定学生直传；后台以 `asyncio.Semaphore(2)` 限流调用腾讯批改 OCR，避免并发撑爆内存。
4. **DSH × 腾讯 OCR 批处理与 MCP 联动**：
   - 业务服务器提供受 Bearer Token 保护的 `/api/batches/from-dsh` 上传桥接接口与 `/mcp/math` Streamable HTTP MCP 服务；
   - 腾讯 OCR 负责“看清图片与表格”，本地服务负责“花名册匹配与真名脱敏”，DSH 负责“脏表清洗、规则筛选与母题归因”；必须经教师确认后才会落库。
5. **隐私与安全绝对红线**：
   - 学生真实姓名仅存在本地 SQLite 数据库中。
   - 向 DeepSeek、DashScope、腾讯云或 DSH 上下文输出的数据**严禁包含学生真实姓名**，仅传递 `anonymous_code`（如 `g6_c01_s01`）或临时编号（`cand_001`）。

---

## 2. 快速启动指南

### 2.1 本地开发与 10 项闭环自检

```bash
# 1. 进入工程目录
cd grade6-math-lite

# 2. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖与 Playwright 内核
pip install -r requirements.txt
playwright install chromium

# 4. 初始化配置文件（默认开启 Mock，支持零密钥跑通全流程）
cp .env.example .env

# 5. 构建/预热母题向量库
python scripts/build_kb.py

# 6. 执行端到端自动化验收脚本（验证 10 大核心与整合闭环）
python scripts/verify_e2e.py

# 7. 启动本地 Web 服务
python -m backend.main
# 访问地址：http://127.0.0.1:8000
# 默认教师访问密码见 .env 中的 TEACHER_ACCESS_PASSWORD（示例默认 math2026）
```

---

## 3. 生产部署与 DSH 联调文档

- 架构全景与 10 张数据表模型见：[PROJECT_STRUCTURE.md](./PROJECT_STRUCTURE.md)
- 云服务器一键部署、双机协同拓扑及 DSH MCP/Skill 接入指南见：[DEPLOY_AND_DSH.md](./DEPLOY_AND_DSH.md)

一键初始化部署命令（Ubuntu 22.04）：

```bash
sudo bash deploy/init_ubuntu_2g.sh
```

脚本会自动完成：
1. 创建 **2GB Swap 虚拟内存**（`vm.swappiness=20`）；
2. 安装 Nginx、Python 3 venv、`fonts-noto-cjk` 中文字体及 Chromium 运行库；
3. 创建 `.venv`、安装依赖、将 Chromium 安装至共享路径 `.playwright/` 并预热 `data/kb_vectors.json`；
4. 配置非特权用户运行的 `systemd` 服务（`grade6-math.service`）与 `50MB` 上传上限的 Nginx 反向代理。

---

## 4. 目录结构

```text
grade6-math-lite/
├── .env.example                    # 环境变量模板（含 SEED_DEMO_DATA 与 MCP_AUTH_TOKEN）
├── .gitignore
├── README.md                       # 快速启动与核心特性总览
├── PROJECT_STRUCTURE.md            # 系统架构、流程图、10张表ER模型与模块职责
├── DEPLOY_AND_DSH.md               # 腾讯云部署、升级运维与 DSH MCP 接入指南
├── requirements.txt                # 精简 Python 依赖（含 openpyxl 解析 xlsx）
├── knowledge_base/                 # 六年级核心母题 Markdown 种子库
│   ├── G6-CALC-01-分数四则混合运算与简便计算.md
│   ├── G6-FRAC-01-找准单位1求部分量与总量.md
│   ├── G6-RATIO-01-比的基本性质与按比分配.md
│   └── G6-GEO-01-圆的周长与面积基础应用.md
├── scripts/
│   ├── build_kb.py                 # 离线建库：Markdown -> data/kb_vectors.json
│   └── verify_e2e.py               # 10 大闭环端到端自动化验收脚本
├── deploy/
│   ├── init_ubuntu_2g.sh           # Ubuntu 22.04 一键部署与环境初始化脚本
│   ├── grade6-math.service         # systemd 守护进程（默认 ubuntu 用户运行）
│   ├── nginx_site.conf             # Nginx 反向代理配置（50M 上传 + gzip）
│   └── dsh/                        # DeepSeek Harness 接入模板
│       ├── upload_batch_to_math.py # DSH 附件上传与本地脱敏桥接脚本
│       └── grade6-math-intake/     # DSH Skill 定义（SKILL.md）
├── backend/
│   ├── main.py                     # FastAPI 主入口、生命周期与静态托管
│   ├── config.py                   # Pydantic Settings
│   ├── db.py                       # SQLite WAL 与 10 张业务表初始化
│   ├── schemas.py                  # 请求与响应 Pydantic 模型
│   ├── routers/                    # 路由 (auth, students, exams, ocr_upload, sheets, kb, mcp_server)
│   └── services/                   # 服务 (RAG检索, 算术沙箱, PDF渲染, 图像裁剪, OCR, 脱敏, 批导入)
├── frontend/
│   ├── login.html                  # 教师密码登录页
│   ├── index.html                  # 教师端主工作台 (Vue3 CDN)
│   ├── upload.html                 # 微信扫码手机拍照直传页（支持 ?student_id= 绑定）
│   └── static/
│       ├── app.js                  # 前端交互、筛选导入、批量作业与快捷键逻辑
│       └── print_sheet.html        # A4 巩固单打印模板 (Jinja2+KaTeX)
└── data/                           # 运行期持久化数据（不入 Git，含 app.db/uploads/cropped/pdfs/batches）
```
