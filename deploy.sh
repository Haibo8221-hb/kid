#!/usr/bin/env bash
# =============================================================================
# deploy.sh —— 本机把 kid-main 通过 rsync 推送到阿里云 ECS（方案 B 手动通道）
# 不依赖 GitHub Actions，适合紧急修复不想走 commit 时直接用。
# 前提：本机 ~/.ssh/config 已配 112.74.40.8 走公司代理（见 ~/.ssh/config）。
# =============================================================================
set -e
SITE_ROOT="${SITE_ROOT:-/www/wwwroot/kid}"
ECS_HOST="${ECS_HOST:-112.74.40.8}"
ECS_PORT="${ECS_PORT:-22}"
ECS_USER="${ECS_USER:-root}"
KEY="${KEY:-$HOME/.ssh/id_ed25519}"
SERVICE="${SERVICE:-plan-api}"

rsync -az --delete \
  --exclude='.git/' --exclude='.github/' --exclude='downloads/' \
  --exclude='resources/' --exclude='backups/' \
  --exclude='research_collector.py' --exclude='deploy.sh' \
  --exclude='.workbuddy/' --exclude='tests/' \
  --exclude='.versions/' --exclude='*/.versions/' \
  --exclude='data/' --exclude='logs/' \
  -e "ssh -p $ECS_PORT -i $KEY -o StrictHostKeyChecking=no" \
  ./ "$ECS_USER@$ECS_HOST:$SITE_ROOT/"

echo "==> rsync 完成。需要重启后端请执行："
echo "   ssh -p $ECS_PORT $ECS_USER@$ECS_HOST 'systemctl restart $SERVICE'"
