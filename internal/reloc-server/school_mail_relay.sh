#!/bin/bash
# 学校邮件快报 relay：有新学校邮件才推微信
OUT=$(/opt/plan-api/bin/python /root/reloc/school_mail_check.py 2>&1)
if [[ "$OUT" == "NO_NEW"* ]]; then
  exit 0
fi
echo "📧 学校邮件快报 $(date '+%m-%d %H:%M')"
echo ""
echo "$OUT"
