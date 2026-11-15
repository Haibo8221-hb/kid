#!/usr/bin/env python3
"""Relocation Agent — 任务图种子（伊斯坦布尔 → 迪拜 2027-01/02）
行为规范 v2（2026-11-15 Bo 确认）：跟踪粒度大幅收敛——
  · 海波本人工作签/入职由公司办理，不拆解；只保留 1 个「居留签/Emirates ID 是否已下发」闸门(GATE-01)，
    每周确认一次或家长主动告知时更新；下游事项把它当前置条件，不展开为任务。
  · 家属签证(配偶/孩子)不属公司办理；只跟踪 3 状态：材料齐备 → 递交 → 获批(FAM-01 用 status 表达)。
  · 主跟踪事项只有 4 类：住房 HOUSE / 用车 CAR / 孩子教育 KIDS / 文件签署 DOCS；
    每类只保留关键路径里程碑，不做子任务拆分。
  · 其余(比价/清单/草稿/排期/催办/依赖检查/预警/汇总)全部自动执行，不问家长；
    只在 A付款/B签字合同/C提交证件给第三方/D得分差<10%的偏好取舍/E信息无法补全 五类情形请确认。
幂等可重跑。DB: /www/wwwroot/kid/data/reloc/tasks.db （服务器运行时数据，不入公开git）
"""
import sqlite3, os, sys, shutil
from datetime import date

DB = '/www/wwwroot/kid/data/reloc/tasks.db'
os.makedirs(os.path.dirname(DB), exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, phase TEXT, title TEXT,
  status TEXT DEFAULT 'todo', owner TEXT,
  due TEXT, depends_on TEXT, risk TEXT DEFAULT 'med',
  human_gate INTEGER DEFAULT 0, docs TEXT,
  source TEXT DEFAULT '', checked TEXT DEFAULT '', notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, topic TEXT,
  conclusion TEXT, source TEXT, checked TEXT, confidence TEXT,
  open_items TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS daily_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_date TEXT, kind TEXT,
  overdue_n INTEGER, actions_n INTEGER, researched_n INTEGER, notes TEXT
);
"""

META = {
 'project': '伊斯坦布尔 → 迪拜 全家搬迁',
 'arrival_anchor': '2027-01-15（暂定锚点，待海波确认）',
 'work_location': 'Bay Gate Tower, Business Bay, Dubai（通勤半径=选房/托儿所核心约束）',
 'housing': 'HR住房补贴≈房租 → 决策：直接签年约(Ejari)，放弃短租过渡（家属签需要Ejari年约）',
 'daughters_passport': '中国普通护照 → 免签90天/180窗（2025-04使馆新规），假期探访无需陪同签',
 'search_backend': 'tavily',
 'residence_rule': '家属居留签离境>180天自动作废（u.ae）；幼儿与爱人走家属签链，女儿走免签探访',
 'rules_version': '2',
 'rules_effective': '2026-11-15',
 'track_scope': '闸门1(居留签/EID)+家属签3状态+4类主事项(住房/用车/孩子教育/文件签署)，里程碑级不拆子任务',
 'confirm_policy': '仅A付款/B签字合同/C提交证件给第三方/D取舍差<10%/E信息无法补全 五类需确认；每次一问+推荐选项；每日≤3条同类合并',
 'automation': '信息搜集/比价/筛选/去重/打分/材料清单复用/表格邮件草稿/跟进催办/排期倒计时/依赖检查/风险预警/每周汇总 全部自动',
 'cadence': '每周1条汇总(≤15行)，无待办只报“无需操作”；剩余周数变化自动重排优先级',
 'currency_policy': '金额保留原币种并附折算；时效数据标查询日期，重要结论≥2来源交叉核对',
 'created': '2026-11-15',
}

# (id, phase, title, status, owner, due, depends_on, risk, human_gate, docs, notes)
# phase: GATE闸门 / FAM家属签 / HOUSE住房 / CAR用车 / KIDS孩子教育 / DOCS文件签署
T = [
 # ── 闸门：海波工作签由公司办理，只跟踪一个下发状态，每周确认 ──
 ('GATE-01','GATE','居留签 / Emirates ID 是否已下发（每周确认或家长主动告知更新）','waiting_on','海波(公司主办)',
  '每周确认','','high',0,'—','唯一的工作签闸门；租房登记/车辆上牌/孩子入学以其为前置条件，不展开为子任务'),
 # ── 家属签：独立事项，只用 status 表达三状态 材料齐备→递交→获批 ──
 ('FAM-01','FAM','家属签证（配偶+幼儿）：材料齐备 → 递交 → 获批','doing','Agent备料/海波递交',
  '2027-02-15','GATE-01','high',1,'认证结婚证/出生证/Ejari年约',
  '状态口径：todo=材料齐备 / doing=已递交 / done=获批；公司不办理，属自主范围；Ejari年约在先'),
 # ── 主跟踪 1：住房（里程碑级，不拆子任务）──
 ('HOUSE-01','HOUSE','住房：定通勤半径候选表 → 看房 → 签约 Ejari 年约','research','Agent+海波',
  '2027-01-20','GATE-01','high',1,'护照/EID/GATE-01','自动比价筛选打分；签约押金=付款红线需确认'),
 # ── 主跟踪 2：用车 ──
 ('CAR-01','CAR','用车：驾照转换资格核查 → 租车/买车定案 → 上牌保险','research','Agent+海波',
  '2027-02-10','GATE-01','med',1,'驾照/EID/GATE-01','自动比价；购车/签约付款属红线需确认；上牌依赖GATE-01'),
 # ── 主跟踪 3：孩子教育（女儿免签探访 + 幼儿托育）──
 ('KIDS-01','KIDS','孩子教育：女儿探访窗口 + 幼儿托育选定与入园','research','Agent+海波/爱人',
  '2027-02-10','FAM-01','med',1,'校历/疫苗本/FAM-01','女儿中国护照免签探访；幼儿入园依赖家属签获批；学费押金需确认'),
 # ── 主跟踪 4：文件签署 ──
 ('DOCS-01','DOCS','文件签署：材料清单(复用现有证件) → 认证链 → 签署递交','doing','Agent备料/海波签署',
  '2026-12-20','','high',1,'结婚证/出生证/5本护照/学历',
  '自动复用已有文件+生成清单；认证链(中/土/英)并行；签字/提交证件给第三方需确认'),
]

def main():
    # 升级模式：保留 findings / daily_log / 其它 meta，只重建 tasks（不整体删库）
    existed = os.path.exists(DB)
    if existed:
        stamp = date.today().isoformat()
        shutil.copy2(DB, DB + '.bak-v2-' + stamp)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    con.execute('DELETE FROM tasks')
    old_meta = {r[0] for r in con.execute('SELECT k FROM meta')}
    for k, v in META.items():
        con.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', (k, v))
    # 标记 schema/rules 版本
    con.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', ('schema_version', '2'))
    for t in T:
        con.execute('INSERT INTO tasks(id,phase,title,status,owner,due,depends_on,risk,human_gate,docs,notes) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?)', t)
    con.commit()
    n = con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    phases = con.execute('SELECT phase, COUNT(*) FROM tasks GROUP BY phase ORDER BY phase').fetchall()
    print(f'seeded {n} tasks (v2):', phases)
    if existed:
        print('previous DB backed up to tasks.db.bak-v2-' + date.today().isoformat())
    con.close()

if __name__ == '__main__':
    main()