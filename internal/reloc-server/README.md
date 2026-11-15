# Relocation Agent（迪拜搬迁）服务器侧资产备份

2026-11-16 入库。这些文件运行时位于 ECS 服务器（不在 web 部署路径），此处为版本化备份；
`internal/` 目录被 CI 拦截策略排除部署（fc9b240），仅作源码存档。

## 文件 → 服务器部署位置

| 本仓库文件 | 服务器位置 | 作用 |
|---|---|---|
| `reloc_seed.py` | `/root/reloc/reloc_seed.py` | 任务图种子（45任务+meta），幂等可重跑 |
| `reloc_daily.py` | `/root/reloc/reloc_daily.py` | 每日循环：逾期/关键路径/今日行动 + Tavily研究 + 周日反审 |
| `reloc-daily.service` | `/etc/systemd/system/reloc-daily.service` | oneshot，EnvironmentFile=/etc/plan-api.env，日志 append /root/reloc/daily.log |
| `reloc-daily.timer` | `/etc/systemd/system/reloc-daily.timer` | 每天 12:00 CST = 07:00 伊斯坦布尔，Persistent |
| `kid_reloc_relay.sh` | `/root/.hermes/scripts/kid_reloc_relay.sh` | Hermes cronjob `reloc-daily-wechat`(12:15) 推送当天 digest，无 digest 则静默 |

## 恢复步骤（新服务器）
1. 按上表复制文件到对应位置；`chmod +x kid_reloc_relay.sh`
2. `/etc/plan-api.env` 需含 `TAVILY_API_KEY=tvly-…`（密钥永不入库）
3. 任务图 DB：`python3 reloc_seed.py`（建库+种子于 `/www/wwwroot/kid/data/reloc/tasks.db`，www:www 664）
4. `systemctl daemon-reload && systemctl enable --now reloc-daily.timer`
5. Hermes cronjob `reloc-daily-wechat`：15 12 * * *，script=kid_reloc_relay.sh，no_agent，attach_to_session
6. 运行时数据（digest_latest.{json,txt}）由每日循环自动重建，不入库

## HRD 工作台集成（/var/www/plan/hrd/index.html，服务器only不入库）
备份：`/var/www/plan/hrd/index.html.bak-reloc-20261115`。三处 patch：
1. MODS 数组 claims 之后追加：`{key:'reloc', title:'迪拜搬迁台', en:'Dubai Relocation', group:'生活服务', icon:'backup', special:1, external:1},`
2. 视图分发 learn 行后追加：`if(state.page==='reloc'){ c.innerHTML=viewReloc(); bindReloc(); return; }`
3. `viewLearn()` 前插入 `relocUrl()/viewReloc()/bindReloc()` 三函数（iframe 嵌 `/reloc/index.html?embed=1`）

## 关联 API（已随主代码入库）
`api_server.py`：`/api/hrd/reloc/{summary,findings,task}`（X-HRD-Token 或 ?t= 门控）；
页面 `reloc/index.html`（仓库根，nginx 直服）。
运维手册：Hermes skill `dubai-relocation`。
