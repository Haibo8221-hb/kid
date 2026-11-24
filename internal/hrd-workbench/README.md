# HRD 工作台备份（服务器侧资产）

生产位置：`/var/www/plan/hrd/index.html`（xgrow.me/hrd，nginx python_kid.conf root=仓库根，页面本身不在仓库部署链）
本目录 = 服务器侧快照备份（CI 不部署）。

## 快照内容（2026-11-18）
- 今日时间盒视图（Timeboxing）：加盒/硬停点三态决策/估时偏差统计/关联搬迁任务自动推进
- 每日复盘（三段式：经过/原因/改进 + 四色基调 + 行动点回顾区）
- Mood Meter 四象限卡片（SVG）
- 迁移认证：纯 JS sha256Hex（PREFIX='hrd-workbench-sync-v1:'，非 HTTPS 环境无 crypto.subtle 时兜底）

## 2026-11-24 修复（提交 4f8d8b5，本快照已含）
- P0：补齐 `tbFetch`/`tbState` 定义（fb6482f 快照中调用 8 处但无定义，时间盒页一开即 ReferenceError）
  ⚠️ 若服务器实机 `/var/www/plan/hrd/index.html` 是 fb6482f 快照原样，时间盒页在线上同样是坏的——
  请核对实机是否已含 tbFetch/tbState；没有则用本快照（已修复版）覆盖恢复。
- P1：innerHTML 动态文本全部 esc() 转义（title/slot/task_ref/action_text）；
  doing 盒已进行分钟改用服务端 elapsed_min（时区安全）；轮询改 30s 重拉取。
- P2：切页清理定时器；加盒失败反馈；planned 盒可改（update）、doing 盒可回退（reset）。

## 恢复
1. 复制 index.html 回服务器 `/var/www/plan/hrd/index.html`（先备份旧文件）
2. chown www:www，权限 644
3. 依赖：api_server.py 的 /api/hrd/timebox + /api/hrd/dayreview 端点（随仓库部署，2026-11-24 起
   启动时自动 ensure-schema 幂等建表，无需手工建表）；tasks.db 若此前手工建过
   daily_reviews 且缺 rdate 唯一约束，api_server 启动时会自动补唯一索引。

DDL（与 api_server.py _ensure_tb_schema 一致）：
timeboxes(id INTEGER PK, tdate, slot, title, est_min, status DEFAULT 'planned',
          task_ref, created, started_at, actual_min, ended_at) + idx_timeboxes_tdate
daily_reviews(rdate TEXT PRIMARY KEY, mood, pass_text, cause_text, action_text,
              created, updated) + idx_daily_reviews_rdate 唯一索引
