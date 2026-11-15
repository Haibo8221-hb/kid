#!/bin/bash
# 迪拜搬迁周报微信推送（每周一 12:15 CST；只推本周生成的，否则静默）
F=/www/wwwroot/kid/data/reloc/digest_latest.txt
if [ -f "$F" ]; then
  # 文件修改时间落在本周（周一 00:00 之后）才推送，避免重复播报上周周报
  START=$(date -d 'last monday' +%s 2>/dev/null || date +%s)
  MT=$(stat -c %Y "$F")
  if [ "$MT" -ge "$START" ]; then
    cat "$F"
  fi
fi