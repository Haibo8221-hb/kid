# Relocation Agent（迪拜搬迁）服务器侧资产备份

2026-11-16 入库，2026-11-15 升级到行为规范 v2。这些文件运行时位于 ECS 服务器（不在 web 部署路径），此处为版本化备份；
`internal/` 目录被 CI 拦截策略排除部署（fc9b240），仅作源码存档。**改动这些文件不会随 GitHub Actions 自动上线，须按下方步骤手动同步到服务器。**

## 行为规范 v2（2026-11-15 Bo 确认）

- **跟踪粒度**：只跟踪 6 条主线——1 个工作签闸门 `GATE-01`（居留签/Emirates ID 是否下发，每周确认或家长主动告知时更新，下游租房登记/车辆上牌/孩子入学以其为前置条件，不展开）+ 1 个家属签 `FAM-01`（只记 3 状态：材料齐备→递交→获批，用 status 表达 todo/doing/done）+ 4 类主事项（住房 HOUSE / 用车 CAR / 孩子教育 KIDS / 文件签署 DOCS），每类只保留关键路径里程碑，不拆子任务。海波本人工作签由公司办理，不单独拆解。
- **自动化默认开**：信息搜集/比价/筛选/去重/打分/材料清单生成并复用已有证件/表格邮件草稿/跟进催办/排期倒计时/依赖检查/风险预警/每周汇总——全部自动，不问家长。
- **仅 5 类需确认**：A 付款/押金/定金/不可退费用；B 签字/合同/法律承诺；C 提交身份证件或敏感信息给第三方；D ≥2 选项得分差<10% 且取决于个人偏好；E 信息缺失且无法检索/补全。格式：【需确认】事项｜推荐方案｜理由（一句话）｜截止时间｜回复"1"即同意。每周≤3 条，同类合并。
- **调研输出 6 段**：①结论≤3 行 ②对比表≤5 候选、每候选≤6 维度 ③评分规则一行 ④数据来源与日期（查不到标"未核实"）⑤风险与不确定点≤3 条 ⑥下一步（自动/需确认分开）。先结论后依据，表格优先，一主题一页，附录折叠。
- **节奏**：由每日改为**每周 1 条汇总**（≤15 行：①已完成自动 ②需确认 ③关键路径倒计时与风险）；无待办只报"无需操作"；距锚点剩余周数跨档（12/8/4）自动重排优先级。
- **约束**：时效数据标查询日期、重要结论≥2 来源交叉核对；不确定写"不确定"并说明如何核实；房租/学费/押金金额保留原币种并附折算。

## 文件 → 服务器部署位置（v2）

| 本仓库文件 | 服务器位置 | 作用（v2 变更）|
|---|---|---|
| `reloc_seed.py` | `/root/reloc/reloc_seed.py` | 任务图种子 v2：6 条主线（原 48 条），幂等可重跑；**升级模式保留 findings/daily_log/其他 meta，只重建 tasks，先备份旧库 .bak-v2-\<date\>** |
| `reloc_daily.py` | `/root/reloc/reloc_daily.py` | 周循环：周报三段 + 6 段调研 + 闸门每周确认 + 优先级重排；digest 兼容旧字段 |
| `reloc-daily.service` | `/etc/systemd/system/reloc-daily.service` | oneshot，EnvironmentFile=/etc/plan-api.env，日志 append /root/reloc/daily.log（描述改 weekly）|
| `reloc-daily.timer` | `/etc/systemd/system/reloc-daily.timer` | **周一 12:00 CST = 07:00 伊斯坦布尔**（原每日），Persistent，RandomizedDelaySec=120 |
| `kid_reloc_relay.sh` | `/root/.hermes/scripts/kid_reloc_relay.sh` | Hermes cronjob `reloc-daily-wechat`（12:15）推送**周报**：只推本周（周一后）生成的 digest，否则静默 |

## 升级步骤（现有服务器，v1→v2）

```bash
# 1. 同步脚本（internal/ 不随 CI，手动 scp 或粘贴）
scp internal/reloc-server/reloc_seed.py      root@112.74.40.8:/root/reloc/reloc_seed.py
scp internal/reloc-server/reloc_daily.py     root@112.74.40.8:/root/reloc/reloc_daily.py
scp internal/reloc-server/reloc-daily.service root@112.74.40.8:/etc/systemd/system/reloc-daily.service
scp internal/reloc-server/reloc-daily.timer   root@112.74.40.8:/etc/systemd/system/reloc-daily.timer
scp internal/reloc-server/kid_reloc_relay.sh  root@112.74.40.8:/root/.hermes/scripts/kid_reloc_relay.sh
ssh root@112.74.40.8 'chmod +x /root/.hermes/scripts/kid_reloc_relay.sh'

# 2. 重建任务图（自动备份旧库为 tasks.db.bak-v2-<date>，findings 保留）
ssh root@112.74.40.8 'cd /root/reloc && /opt/plan-api/bin/python reloc_seed.py'
#    预期输出：seeded 6 tasks (v2): [('CAR',1),('DOCS',1),('FAM',1),('GATE',1),('HOUSE',1),('KIDS',1)]

# 3. 切换定时器 每日→每周一
ssh root@112.74.40.8 'systemctl daemon-reload && systemctl restart reloc-daily.timer && systemctl status reloc-daily.timer --no-pager'
#    预期：OnCalendar=Mon *-*-* 12:00:00

# 4. 手动试跑一次（验证周报生成，不依赖定时）
ssh root@112.74.40.8 '/opt/plan-api/bin/python /root/reloc/reloc_daily.py'
#    预期：[digest] written | rules=v2 | confirmed=... researched=... overdue=...

# 5. 验证 API 返回 v2 结构（浏览器登录态或 X-HRD-Token）
curl -s -H 'X-HRD-Token: <token>' http://112.74.40.8/api/hrd/reloc/summary | python3 -c \
  'import sys,json;j=json.load(sys.stdin);d=j["digest"];print("tasks",len(j["tasks"]),"| rules",d.get("rules_version"),"| weekly",bool(d.get("weekly")),"| confirms",len(d.get("confirmations") or []))'
#    预期：tasks 6 | rules 2 | weekly True | confirms 1-3
```

## 回滚（如 v2 异常）

```bash
ssh root@112.74.40.8 'cd /root/reloc && cp data 2>/dev/null; cp /www/wwwroot/kid/data/reloc/tasks.db.bak-v2-<date> /www/wwwroot/kid/data/reloc/tasks.db && systemctl restart reloc-daily.timer'
```
（脚本回滚：用 git 上一版本覆盖 4 个文件后 `systemctl daemon-reload && systemctl restart reloc-daily.timer`。）

## 恢复步骤（新服务器）

1. 按上表复制文件到对应位置；`chmod +x kid_reloc_relay.sh`
2. `/etc/plan-api.env` 需含 `TAVILY_API_KEY=tvly-…`（密钥永不入库）
3. 任务图 DB：`/opt/plan-api/bin/python /root/reloc/reloc_seed.py`（建库+6 条主线于 `/www/wwwroot/kid/data/reloc/tasks.db`，www:www 664）
4. `systemctl daemon-reload && systemctl enable --now reloc-daily.timer`
5. Hermes cronjob `reloc-daily-wechat`：15 12 * * *，script=kid_reloc_relay.sh，no_agent，attach_to_session
6. 运行时数据（digest_latest.{json,txt}）由周循环自动重建，不入库

## 随 CI 自动部署的部分（无需手动）

- `api_server.py`：`/api/hrd/reloc/{summary,findings,task,ingest,auto}` + `/api/reloc/bootstrap`；**ingest prompt 已注入行为规范 v2**（周报三段、6 段调研、5 类确认、闸门/家属签上下文、降级兜底），automatically via GitHub Actions。
- `reloc/index.html`（仓库根，nginx 直服）：**页面 v2 周报展示**（本周汇总三段、【需确认】卡片+一键复制、调研 6 段附录折叠、主线任务图 6 类分组），随 CI 自动上线。

## 关联

- HRD 工作台集成（`/var/www/plan/hrd/index.html`，服务器 only 不入库）：iframe 嵌 `/reloc/index.html?embed=1`，无需改动。
- 运维手册：Hermes skill `dubai-relocation`。
- ⚠️ 注意：行为规范 v2 的部分规则（调研 6 段、确认格式、自动边界）通过 LLM prompt 注入，效果依赖 LLM；若模型偶有偏差，以 digest/页面结构为准，prompt 在 `reloc_daily.py::BRIEF` 与 `api_server.py::RELOC_BRIEF_V2` 两处。