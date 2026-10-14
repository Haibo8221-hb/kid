#!/usr/bin/env bash
# =============================================================================
# deploy.sh —— 在阿里云 ECS 上拉取 GitHub 最新并重启后端。
# 可被 GitHub Actions 调用，也可在 ECS 终端手动执行（排障/立即生效时用）。
# 与 .github/workflows/deploy.yml 内的命令一致。
# =============================================================================
set -e
SITE_ROOT="${SITE_ROOT:-/www/wwwroot/kid-site}"
SERVICE="${SERVICE:-plan-api}"

cd "$SITE_ROOT"
git config --global --add safe.directory "$SITE_ROOT"
git fetch origin
git reset --hard origin/main
chown -R www:www "$SITE_ROOT"
supervisorctl restart "$SERVICE" || true
echo "==> deploy done"
