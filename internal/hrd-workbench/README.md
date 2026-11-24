# HRD 工作台备份（服务器侧资产）

生产位置：`/var/www/plan/hrd/index.html`（xgrow.me/hrd，nginx python_kid.conf root=仓库根，页面本身不在仓库部署链）
本目录 = 服务器侧快照备份（CI 不部署）。

## 快照内容（2026-11-18）
- 今日时间盒视图（Timeboxing）：加盒/硬停点三态决策/估时偏差统计/关联搬迁任务自动推进
- 每日复盘（三段式：经过/原因/改进 + 四色基调 + 行动点回顾区）
- Mood Meter 四象限卡片（SVG）
- 迁移认证：纯 JS sha256Hex（PREFIX='hrd-workbench-sync-v1:'，非 HTTPS 环境无 crypto.subtle 时兜底）

## 恢复
1. 复制 index.html 回服务器 `/var/www/plan/hrd/index.html`（先备份旧文件）
2. chown www:www，权限 644
3. 依赖：api_server.py 的 /api/hrd/timebox + /api/hrd/dayreview 端点（随仓库部署）；
   tasks.db 需有 timeboxes 与 daily_reviews 表（CREATE TABLE 见 reloc_seed 或下方 DDL）

timeboxes(id,tdate,slot,title,est_min,status,actual_min,task_ref,started_at,ended_at,created)
daily_reviews(id,rdate UNIQUE,mood,pass_text,cause_text,action_text,created,updated)
