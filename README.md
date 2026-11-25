# 小学六年级数学“保及格”教师端工作台 · 纯 Python 轻量全栈工程

专为 **2核 2GiB 内存、40GiB 硬盘的 Ubuntu 22.04 服务器（如阿里云 99元/年款）** 定制开发。专攻小学六年级 **40–58 分临界生**，目标稳拿基础分冲刺 **65–70 分**。

---

## 1. 核心架构与 2GB 内存生存设计

1. **去 Dify / 去 Docker 微内核**：
   - 彻底摒弃大型重量级容器架构，全栈使用 **Python 3.11/3.12 + FastAPI + SQLite (WAL模式) + NumPy 内存向量检索 + CDN 前端 (Vue 3 / Tailwind / KaTeX) + Playwright 按需唤醒**。
   - 生产环境常驻内存严格控制在 **200–300 MB**，绝不 OOM。
2. **Playwright 零常驻机制**：
   - 严禁全局初始化浏览器；仅在点击“导出 A4 PDF”时以异步上下文临时启动 Chromium，单次渲染完成立刻 `close()` 释放全部内存。
3. **开箱即跑（Mock 优先）**：
   - 默认 `.env` 中 `MOCK_EXTERNAL_APIS=true`，无需任何云端 API Key 即可本地 100% 跑通全部端到端测试与真实业务闭环。
   - 填入 DeepSeek、阿里云百炼、腾讯云密钥并将开关设为 `false`，即无缝切换至云端真实生产调用。
4. **隐私与安全绝对红线**：
   - 学生真实姓名仅存在本地 SQLite 数据库中。
   - 向 DeepSeek、DashScope、腾讯云发出的请求中**严禁包含学生真实姓名**，仅传递匿名编码（如 `g6_c01_s01`）。
   - 单教师密码鉴权（Session Cookie），防止未授权访问或 API 额度盗刷。

---

## 2. 快速启动指南

### 2.1 本地开发与验收自检

```bash
# 1. 进入工程目录
cd grade6-math-lite

# 2. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖与 Playwright 内核
pip install -r requirements.txt
playwright install chromium

# 4. 初始化配置文件（默认开启 Mock）
cp .env.example .env

# 5. 构建/预热母题向量库
python scripts/build_kb.py

# 6. 执行端到端自动化验收脚本（验证 6 大核心业务闭环）
python scripts/verify_e2e.py

# 7. 启动本地 Web 服务
python -m backend.main
# 访问地址：http://127.0.0.1:8000
# 默认教师访问密码：math2026
```

---

## 3. 云服务器（Ubuntu 22.04）一键上线

本项目提供幂等安全的部署脚本，**绝不触碰任何云平台实例规格，100% 保留续费资格**：

```bash
# 赋予执行权限并运行
sudo bash deploy/init_ubuntu_2g.sh
```

该脚本将自动完成：
1. 创建 **2GB Swap 虚拟内存**（设置 `vm.swappiness=20`），缓冲周五批量出卷高峰；
2. 安装系统库及 `fonts-noto-cjk` 中文字体，杜绝 PDF 中文方块乱码；
3. 配置 `systemd` 服务守护自启与低内存限制；
4. 配置 `Nginx` 反向代理与 `20MB` 手机高清试卷拍照直传限制。

---

## 4. 目录结构

```text
grade6-math-lite/
├── .env.example                    # 环境变量模板
├── .gitignore
├── README.md                       # 说明指南
├── requirements.txt                # 精简 Python 依赖
├── knowledge_base/                 # 六年级核心母题 Markdown 种子库
│   ├── G6-CALC-01-分数四则混合运算与简便计算.md
│   ├── G6-FRAC-01-找准单位1求部分量与总量.md
│   ├── G6-RATIO-01-比的基本性质与按比分配.md
│   └── G6-GEO-01-圆的周长与面积基础应用.md
├── scripts/
│   ├── build_kb.py                 # 离线建库：Markdown -> data/kb_vectors.json
│   └── verify_e2e.py               # 6 大闭环端到端自动化验收脚本
├── deploy/
│   ├── init_ubuntu_2g.sh           # 2核2G Ubuntu 一键部署与 2G Swap 设置
│   ├── grade6-math.service         # systemd 守护进程
│   └── nginx_site.conf             # Nginx 反向代理配置
├── backend/
│   ├── main.py                     # FastAPI 主入口、生命周期与静态挂载
│   ├── config.py                   # Pydantic Settings
│   ├── db.py                       # SQLite WAL 与演示数据初始化
│   ├── schemas.py                  # 请求与响应模型
│   ├── routers/                    # 路由 (auth, students, exams, ocr, sheets)
│   └── services/                   # 服务 (RAG检索, 算术沙箱, 批改OCR, PDF渲染)
├── frontend/
│   ├── login.html                  # 教师密码登录页
│   ├── index.html                  # 教师端主工作台 (Vue3 CDN)
│   ├── upload.html                 # 微信扫码手机拍照直传页
│   └── static/
│       ├── app.js                  # 前端交互与快捷键逻辑
│       └── print_sheet.html        # A4 巩固单打印模板 (Jinja2+KaTeX)
└── data/                           # 持久化数据 (自动创建)
    ├── app.db
    ├── kb_vectors.json
    ├── uploads/
    ├── cropped/
    └── pdfs/
```
