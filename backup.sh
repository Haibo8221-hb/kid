#!/bin/bash
# Daily backup of key files for Diya/Jemma planning system
# Runs via cron, keeps last 30 backups

BACKUP_DIR="/var/www/plan/backups"
DATE=$(date +%Y%m%d_%H%M)
DIR="${BACKUP_DIR}/${DATE}"
mkdir -p "$DIR"

# HTML panels
cp /var/www/plan/app.html "$DIR/app.html"
cp /var/www/plan/jemma.html "$DIR/jemma.html"

# Backend
cp /var/www/plan/api_server.py "$DIR/api_server.py"

# Core data files
cp /var/www/plan/data/errors_diya.json "$DIR/errors_diya.json"
cp /var/www/plan/data/errors_jemma.json "$DIR/errors_jemma.json"
cp /var/www/plan/data/tasks_diya.json "$DIR/tasks_diya.json"
cp /var/www/plan/data/tasks_jemma.json "$DIR/tasks_jemma.json"
cp /var/www/plan/data/planner_diya.json "$DIR/planner_diya.json"
cp /var/www/plan/data/planner_jemma.json "$DIR/planner_jemma.json"
cp /var/www/plan/data/chat_diya.json "$DIR/chat_diya.json"
cp /var/www/plan/data/chat_jemma.json "$DIR/chat_jemma.json"
cp /var/www/plan/data/wiki_diya.json "$DIR/wiki_diya.json"

# Nginx config
cp /etc/nginx/sites-enabled/plan-ssl.conf "$DIR/plan-ssl.conf"

# Rotate: keep last 30
BACKUP_COUNT=$(ls -d ${BACKUP_DIR}/20* 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt 30 ]; then
    ls -dt ${BACKUP_DIR}/20* | tail -n +31 | xargs rm -rf
fi

echo "$(date) Backup completed: ${DIR} ($BACKUP_COUNT backups kept)"