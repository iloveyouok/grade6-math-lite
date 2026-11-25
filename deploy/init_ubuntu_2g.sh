#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# 阿里云 2核2G Ubuntu 22.04 服务器专属一键初始化与加固脚本
# 说明：此脚本完全在操作系统层运行，绝不触碰任何云平台实例规格，100%保留99元续费资格
# ==============================================================================

echo ">>> [1/6] 检查并配置 2GB Swap 虚拟内存（防止 PDF 瞬时峰值 OOM）..."
if [ ! -f /swapfile ]; then
    echo "创建 2GB Swap 交换文件..."
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo "/swapfile none swap sw 0 0" >> /etc/fstab
    sysctl vm.swappiness=20
    echo "vm.swappiness=20" >> /etc/sysctl.conf
    echo "Swap 虚拟内存创建成功！"
else
    echo "Swap 虚拟内存已存在，跳过创建。"
fi

echo ">>> [2/6] 更新软件包索引并安装系统依赖及中文字体库..."
apt-get update -y
apt-get install -y --no-install-recommends \
    python3-venv \
    python3-pip \
    nginx \
    certbot \
    python3-certbot-nginx \
    fonts-noto-cjk \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2

echo ">>> [3/6] 初始化 Python 虚拟环境与依赖..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
export PLAYWRIGHT_BROWSERS_PATH="$PROJECT_ROOT/.playwright"
PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright}" playwright install chromium

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "已从 .env.example 生成 .env，请妥善配置 TEACHER_ACCESS_PASSWORD 及 API 密钥。"
fi

echo ">>> [4/6] 预热知识库向量索引..."
python scripts/build_kb.py

echo ">>> [5/6] 配置 Systemd 守护进程..."
SERVICE_USER="${APP_USER:-ubuntu}"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    SERVICE_USER="root"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_ROOT"
cp deploy/grade6-math.service /etc/systemd/system/grade6-math.service
sed -i "s|/opt/grade6-math-lite|$PROJECT_ROOT|g" /etc/systemd/system/grade6-math.service
sed -i "s|^User=root$|User=$SERVICE_USER|" /etc/systemd/system/grade6-math.service
systemctl daemon-reload
systemctl enable grade6-math.service
systemctl restart grade6-math.service

echo ">>> [6/6] 配置 Nginx 反向代理与大文件上传限制 (50M)..."
rm -f /etc/nginx/sites-enabled/default
cp deploy/nginx_site.conf /etc/nginx/sites-available/grade6-math.conf
ln -sf /etc/nginx/sites-available/grade6-math.conf /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx

echo "=========================================================================="
echo "恭喜！小学六年级数学‘保及格’工作台已在 2核2G 服务器上部署成功！"
echo "服务状态：systemctl status grade6-math"
echo "日志查看：journalctl -u grade6-math -f"
echo "=========================================================================="
