# 生产部署与 DeepSeek Harness (DSH) 集成运维手册

本文档面向 **跨设备协作与后续迭代部署**，记录 `grade6-math-lite` 在腾讯云业务主节点与阿里云 DSH 编排节点上的标准部署结构、升级流程与安全边界。

> **安全说明**：本仓库为公开仓库，文中所有服务器公网 IP、域名、登录密码、API 密钥和 `MCP_AUTH_TOKEN` 均使用占位符表示；真实密钥只保存在服务器本地 `/opt/grade6-math-lite/.env` 与 `/opt/deepseek-harness/` 受限配置文件中。

---

## 1. 双机协同部署拓扑

| 节点角色 | 推荐/当前运行环境 | 核心职责 | 数据与隐私边界 |
| :--- | :--- | :--- | :--- |
| **业务主节点（腾讯云）** | 4核 4GiB / 40GB SSD / Ubuntu 22.04 | 运行 `grade6-math-lite`（FastAPI + SQLite WAL + Nginx + Playwright A4 PDF + 腾讯 OCR 调用） | 保存真实学生花名册、作业原图、切图、A4 PDF 与 `.env` 密钥 |
| **DSH 编排节点（阿里云）** | 运行 `deepseek-harness` (`dsh-local`) + `gateway-proxy` (Nginx) | 作为教师对话入口，挂载 `dsh-mcp-client` 与 `grade6-math-intake` Skill | 仅接触 `anonymous_code` / `cand_xxx` 脱敏后的表格与错题文本 |

### 为什么拆分两台机器？
1. **内存隔离**：`deepseek-harness` 常驻占用约 `320–400 MiB` 内存，若与 Playwright Chromium（PDF 渲染峰值 `250–300 MiB`）同挤 2 GiB 机器容易触发 Swap 抖动或 OOM；拆至 4核 4G 业务主节点后运行更宽裕。
2. **地域与 OCR 延迟**：业务主节点直接调用腾讯云 OCR 与百炼/DeepSeek，图片压缩和脱敏映射均在业务机本地完成。

---

## 2. 业务主节点部署与升级（Ubuntu 22.04）

### 2.1 首次一键部署

```bash
# 1. 拉取代码到 /opt/grade6-math-lite
sudo mkdir -p /opt/grade6-math-lite
sudo chown -R $USER:$USER /opt/grade6-math-lite
git clone https://github.com/iloveyouok/grade6-math-lite.git /opt/grade6-math-lite
cd /opt/grade6-math-lite

# 2. 生成并配置 .env（生产环境务必修改密码与令牌）
cp .env.example .env
chmod 600 .env
# 编辑 .env：
#   TEACHER_ACCESS_PASSWORD=<强密码>
#   MCP_AUTH_TOKEN=<32字节以上随机Token>
#   SEED_DEMO_DATA=false  # 若不想注入5名演示学生
#   MOCK_EXTERNAL_APIS=false  # 填入真实密钥后开启

# 3. 执行幂等部署脚本
sudo bash deploy/init_ubuntu_2g.sh
```

### 2.2 脚本关键设计点（踩坑防雷）
1. **共享 Playwright 浏览器目录**：`init_ubuntu_2g.sh` 会将 Chromium 下载至 `/opt/grade6-math-lite/.playwright`（通过 `PLAYWRIGHT_BROWSERS_PATH`），并通过 `chown` 授权给 `ubuntu` 用户；[grade6-math.service](./deploy/grade6-math.service) 同步设置 `PLAYWRIGHT_BROWSERS_PATH=/opt/grade6-math-lite/.playwright` 与 `User=ubuntu`，避免以 `root` 安装后普通用户启动报错。
2. **Nginx 默认站点与大文件上传**：[init_ubuntu_2g.sh](./deploy/init_ubuntu_2g.sh) 会移除 `/etc/nginx/sites-enabled/default`，并启用 `listen 80 default_server` 与 `client_max_body_size 50M`（支持多张作业图片及 `.zip` 压缩包直传）。
3. **内置 2GB Swap + 中文字体**：自动安装 `fonts-noto-cjk`、`fonts-wqy-zenhei`、`fonts-wqy-microhei`，防止 A4 PDF 中文方块乱码。

### 2.3 后续从 GitHub 拉取升级

当你在其他设备修改并推送代码到 `main` 分支后，在业务主节点只需执行：

```bash
cd /opt/grade6-math-lite
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_kb.py
sudo systemctl restart grade6-math
sudo journalctl -u grade6-math -n 30 --no-pager
```

---

## 3. HTTPS 与域名接入方案（国内服务器注意事项）

1. **仅使用 HTTP IP 试用**：云服务器防火墙放行 `TCP 22`、`TCP 80` 后，即可直接通过 `http://<TENCENT_SERVER_IP>/` 访问。
2. **正式开启 HTTPS（微信扫码拍照与远程 MCP 推荐）**：
   - 若直接绑定域名（例如 `math.yourdomain.com`）解析到腾讯云服务器，国内节点需要在腾讯云侧完成域名**新增接入备案**（主域名在原服务商的备案不会被注销，属于多接入商共存），并在防火墙放行 **`TCP 443`** 后使用 `certbot --nginx` 签发证书。
   - **重要**：前端使用根路径 `/api/...` 与 `/static/...`，因此跨服务器反代或绑定域名时应使用**独立二级域名**或**独立端口**，不要挂载到 `/math/` 子路径下。
   - **在完成 HTTPS 之前**：不建议让两台服务器之间通过公网明文 HTTP 传递 `MCP_AUTH_TOKEN`；可先在两台机器间建立加密 SSH 隧道（利用已放行的 22 端口转发到本地回环），或等待 443 HTTPS 就绪后再直连。

---

## 4. DeepSeek Harness (DSH) 接入配置

DSH 侧完全通过现有插件配置与 Skill 机制接入，**不需要修改 DSH 镜像源码**：

### 4.1 注册 MCP 客户端（`cordis.patch.yml`）
在 DSH profile 配置目录（如 `/opt/deepseek-harness/data/dsh/profiles/web/cordis.patch.yml`）添加：

```yaml
- id: mcp-grade6-math
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: math
    transport: streamable-http
    url: https://<MATH_SERVER_ENDPOINT>/mcp/math
    headers:
      Authorization: !!js '`Bearer ${process.env.MATH_MCP_TOKEN}`'
```

> 并在 DSH 容器环境中注入 `MATH_MCP_TOKEN` 与 `MATH_API_BASE`（例如通过 `/opt/deepseek-harness/compose.yaml` 或 env 文件，切勿硬编码进仓库）。

### 4.2 安装上传桥接脚本与 Skill
1. 将仓库中的 [deploy/dsh/upload_batch_to_math.py](./deploy/dsh/upload_batch_to_math.py) 复制到 DSH 工作区可执行路径（例如 `/opt/deepseek-harness/workspace/bin/upload_batch_to_math.py` 并 `chmod +x`）。
2. 将 [deploy/dsh/grade6-math-intake/SKILL.md](./deploy/dsh/grade6-math-intake/SKILL.md) 复制到 DSH Skills 目录（`/opt/deepseek-harness/data/dsh/skills/grade6-math-intake/SKILL.md`）。
3. 重启 `deepseek-harness` 容器使其加载新 MCP 工具（`mcp__math__*`）与 Skill。

### 4.3 DSH + 腾讯 OCR 联动规范
- **识图由服务端跑腾讯 OCR**：DSH 收到成绩单图片、Excel 或作业包后，调用 `upload_batch_to_math.py` 上传至业务节点的 `/api/batches/from-dsh`。
- **本地脱敏后再返回给 DSH**：业务节点跑完腾讯 OCR 并本地匹配花名册，仅返回 `anonymous_code` / `cand_001` 与座号、分数、错题文本；**真实姓名留在业务节点 SQLite 中**。
- **先预览、确认后再写库**：DSH 清洗筛选后调用 `math_preview_import_plan`，必须向教师展示预览表（或提示教师在工作台真名核对页面确认），得到明确指令后才允许调用 `math_commit_import_batch(confirmed_by_user=true)`。

---

## 5. 多设备开发与验收检查清单

每次在任一设备拉取本仓库继续开发后，提交推送前请确保：
1. `.venv` 下执行 `python scripts/verify_e2e.py` **10 项全量闭环测试通过**（包含隐私脱敏断言与未确认拦截断言）。
2. 检查 `git status` 确认未把 `.env`、`data/*.db*`、`data/uploads/*`、`data/pdfs/*`、`data/batches/*` 提交进 Git。
3. 本次如果改动了接口、表结构或部署脚本，同步更新 `PROJECT_STRUCTURE.md` 与 `DEPLOY_AND_DSH.md`，保证跨设备 AI 上下文一致。
