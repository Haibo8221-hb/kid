#!/usr/bin/env python3
"""P0 修复：goals.json 数据污染（2026-11-11 体检发现）
问题：
  1. 8 条 child='jemma' 的目标实为 Diya 目标的逐字拷贝（record_id/目标ID 均为 diya-*）
     → 违反两孩数据隔离铁律（Jemma 登录会看到 Diya 的全部目标）
  2. Diya 的 diya-g2（错题总清库）只存在于 jemma 拷贝中，Diya 侧反而缺失
  3. Diya 自己的目标全部缺 child 字段（靠 DEFAULT_GOAL_CHILD 兜底）
修复：
  - 备份 → 删除 jemma 拷贝 → diya-g2 转正归 diya → 所有 diya 目标显式 child='diya'
  - 为 Jemma 按其真实情况（Y9 探索期）重建 4 条目标（英文内容，schema 与现有一致）
  - 全部目标补 tier（year/term/sprint）与 owner 字段（Goal Desk 三层化基础）
一次性脚本，跑完即弃；幂等（重复跑不会重复建 jemma 目标）。
"""
import json, shutil, sys
from datetime import datetime, timezone

GOALS = '/www/wwwroot/kid/data/goals.json'
NOW = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')

d = json.load(open(GOALS))
goals = d['goals']

# ── 1. 备份 ──
bak = GOALS + '.bak-contam-' + datetime.now().strftime('%Y%m%d-%H%M%S')
shutil.copy2(GOALS, bak)
print(f'[backup] {bak}')

# ── 2. 拆分：diya 真身（无 child 或 child=diya） vs jemma 污染拷贝 ──
clean, polluted = [], []
for g in goals:
    gid = str(g.get('目标ID', ''))
    child = str(g.get('child') or '').strip().lower()
    if child == 'jemma' and gid.startswith('diya-'):
        polluted.append(g)
    else:
        clean.append(g)
print(f'[split] clean={len(clean)} polluted={len(polluted)}')

# ── 3. diya-g2（错题总清库）在 clean 中缺失 → 从污染拷贝转正 ──
have_ids = {g.get('目标ID') for g in clean}
for g in polluted:
    if g.get('目标ID') == 'diya-error-clear' and 'diya-error-clear' not in have_ids:
        g2 = dict(g)
        g2['child'] = 'diya'
        clean.append(g2)
        print('[restore] diya-error-clear 转正归 diya')

# ── 4. 所有 diya 目标显式 child='diya' ──
for g in clean:
    if not str(g.get('child') or '').strip():
        g['child'] = 'diya'

# ── 5. tier / owner 补齐（幂等：已有则跳过） ──
TIER_BY_ID = {
    'diya-chem-paper2': 'term',   # 11-08 截止（已过期，待 L3 处置队列裁定）
    'diya-physics-rp': 'term',    # 2027-01-31
    'diya-smc-q913': 'term',      # 2026-12-20
    'diya-english-pet': 'year',   # 2027-03-31
    'diya-entrance-day': 'sprint',# 11-06/07 已过期
    'diya-chem-verdict': 'sprint',# 已完成
    'diya-late-app': 'sprint',    # 10-24 已过期，状态待开始 → 需人工核实
    'diya-error-clear': 'year',   # 2027-04-30
}
for g in clean:
    gid = g.get('目标ID')
    g.setdefault('tier', TIER_BY_ID.get(gid, 'term'))
    if gid and str(gid).startswith('diya-'):
        g.setdefault('owner', 'Diya')
        g.setdefault('owner_support', 'Haibo (evidence/scores) · AI (reminders)')

# ── 6. Jemma 真实目标重建（Y9 探索期，低压力；英文内容） ──
JEMMA_GOALS = [
    {
        'record_id': 'jemma-g1', 'child': 'jemma', '目标ID': 'jemma-bebras-2026',
        '目标名称': 'Bebras Challenge 2026 — sit the challenge inside the window',
        '模板': '练习', '领域': 'Computing', '单位': 'attempt',
        '目标总量': 1, '已完成量': 0, '开始日期': '2026-11-09', '截止日期': '2026-11-20',
        '状态': '进行中', '休息日': [], '休息额度': 0,
        '拆解计划': 'Window 9–20 Nov 2026 (bebras.uk). School usually runs it in class — confirm sitting date with Computing teacher this week.',
        '障碍预案': 'If school has not scheduled it by 17 Nov, parent emails Computing dept to book a slot before window closes.',
        '颜色': '#7c3aed', '今日建议量': 0, '连续天数': 0, '投入分钟': 0,
        '节奏提示': 'One sitting, ~40 min. Last year: Distinction.',
        '收口方式': 'Screenshot/confirmation from school → parent logs done.',
        '每日上限': 1, 'tier': 'sprint', 'owner': 'Jemma',
        'owner_support': 'School Computing dept (scheduling) · Haibo (confirm)',
        '创建时间': NOW, '最近更新': NOW,
    },
    {
        'record_id': 'jemma-g2', 'child': 'jemma', '目标ID': 'jemma-imc-prep',
        '目标名称': 'IMC prep — 6 past papers before the Intermediate Challenge',
        '模板': '练习', '领域': 'Maths', '单位': 'paper',
        '目标总量': 6, '已完成量': 0, '开始日期': '2026-11-15', '截止日期': '2027-01-20',
        '状态': '待开始', '休息日': [], '休息额度': 0,
        '拆解计划': 'IMC date: 2027-01-27 (UKMT). 1 paper per fortnight from mid-Nov; review wrong questions in error book each time; last paper by 20 Jan as dress rehearsal.',
        '障碍预案': 'School holiday periods: papers optional, keep 1 per month minimum.',
        '颜色': '#0ea5e9', '今日建议量': 0, '连续天数': 0, '投入分钟': 0,
        '节奏提示': 'Low-pressure phase: accuracy over speed. July exam was 88% (+13 vs median).',
        '收口方式': 'Score recorded per paper; target trend ≥ Silver-award line.',
        '每日上限': 1, 'tier': 'term', 'owner': 'Jemma',
        'owner_support': 'Haibo (past papers from downloads/) · AI (schedule reminders)',
        '创建时间': NOW, '最近更新': NOW,
    },
    {
        'record_id': 'jemma-g3', 'child': 'jemma', '目标ID': 'jemma-piano-g8',
        '目标名称': 'Piano Grade 8 — steady exam prep',
        '模板': '练习', '领域': 'Music', '单位': 'practice week',
        '目标总量': 20, '已完成量': 0, '开始日期': '2026-11-10', '截止日期': '2027-03-31',
        '状态': '进行中', '休息日': [], '休息额度': 2,
        '拆解计划': 'Count one "practice week" when ≥4 practice sessions done. Exam date to be confirmed (待核实 — not in system yet).',
        '障碍预案': 'Exam-heavy school weeks: minimum 2 sessions, still counts as half week.',
        '颜色': '#f59e0b', '今日建议量': 0, '连续天数': 0, '投入分钟': 0,
        '节奏提示': 'Grade 8 is a marathon — consistency beats cramming.',
        '收口方式': 'Weekly self-check-in; parent confirms exam booking when made.',
        '每日上限': 1, 'tier': 'year', 'owner': 'Jemma',
        'owner_support': 'Haibo (exam booking · date 待核实)',
        '创建时间': NOW, '最近更新': NOW,
    },
    {
        'record_id': 'jemma-g4', 'child': 'jemma', '目标ID': 'jemma-reading-journal',
        '目标名称': 'English reading journal — 1 book per month + short journal notes',
        '模板': '练习', '领域': 'English', '单位': 'book',
        '目标总量': 7, '已完成量': 0, '开始日期': '2026-11-10', '截止日期': '2027-06-30',
        '状态': '进行中', '休息日': [], '休息额度': 1,
        '拆解计划': 'Free-choice novels (English). 3–5 journal lines per book: favourite line, one question, one opinion. Feeds Y10 English Lit habit, zero pressure.',
        '障碍预案': 'Busy month → audiobook counts.',
        '颜色': '#10b981', '今日建议量': 0, '连续天数': 0, '投入分钟': 0,
        '节奏提示': 'Oct26 report: English effort Above. Keep the slow burn.',
        '收口方式': 'One-line journal entry in Notes tab when finished.',
        '每日上限': 1, 'tier': 'year', 'owner': 'Jemma',
        'owner_support': 'Haibo (book supply) · AI (monthly nudge)',
        '创建时间': NOW, '最近更新': NOW,
    },
]
existing_jemma = {g.get('目标ID') for g in clean if g.get('child') == 'jemma'}
added = 0
for jg in JEMMA_GOALS:
    if jg['目标ID'] not in existing_jemma:
        clean.append(jg)
        added += 1
print(f'[jemma] rebuilt {added} goals')

# ── 7. checks/stages 同样显式归属（历史无 child 的归 diya，与后端 DEFAULT 一致） ──
for rec in d.get('checks', []) + d.get('stages', []):
    if not str(rec.get('child') or '').strip():
        gid = str(rec.get('目标ID', ''))
        rec['child'] = 'jemma' if gid.startswith('jemma-') else 'diya'

d['goals'] = clean
meta = d.setdefault('_meta', {})
meta['contamination_fix'] = ('2026-11-11: removed 8 jemma-tagged copies of diya goals; restored '
                             'diya-error-clear to diya; rebuilt 4 real jemma goals; added tier/owner fields. '
                             f'Backup: {bak.split("/")[-1]}')
meta['last_maintenance'] = NOW

json.dump(d, open(GOALS, 'w'), ensure_ascii=False, indent=1)

# ── 8. 验证 ──
v = json.load(open(GOALS))
diya = [g for g in v['goals'] if g.get('child') == 'diya']
jemma = [g for g in v['goals'] if g.get('child') == 'jemma']
assert all(str(g.get('目标ID', '')).startswith('diya-') for g in diya), 'diya 侧仍有非 diya 目标'
assert all(str(g.get('目标ID', '')).startswith('jemma-') for g in jemma), 'jemma 侧仍有非 jemma 目标'
print(f'[verify] total={len(v["goals"])} diya={len(diya)} jemma={len(jemma)} — 隔离干净 ✅')
for g in jemma:
    print('  jemma:', g['目标ID'], '| tier', g.get('tier'), '|', g['目标名称'][:48])
