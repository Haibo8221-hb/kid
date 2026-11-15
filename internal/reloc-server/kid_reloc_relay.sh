#!/bin/bash
# 迪拜搬迁日报微信推送（每日12:15 CST；只推当天生成的，否则静默）
F=/www/wwwroot/kid/data/reloc/digest_latest.txt
if [ -f "$F" ] && [ "$(date +%Y-%m-%d)" = "$(stat -c %y "$F" | cut -d' ' -f1)" ]; then
  cat "$F"
fi
