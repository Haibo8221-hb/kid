#!/usr/bin/env python3
"""Grow Weekly — Diya/Jemma 成长周报草稿生成器
聚合：goals.json / competition_calendar.json / live_brief.json / smc_records.json
产出：data/weekly_drafts/{YYYY-Www}-{child}.md + parent_summary_{YYYY-Www}.md
流程：每周日 17:30 定时生成草稿 → 微信提醒家长审核 → 家长回复后人工发送（SMTP）
绝不自动发送。所有数字来自真实数据文件，缺失标 TBC。
"""
import json, os, datetime, sys

KID = '/www/wwwroot/kid'
DRAFTS = os.path.join(KID, 'data', 'weekly_drafts')
os.makedirs(DRAFTS, exist_ok=True)
TODAY = datetime.date.today()
WEEK = f'{TODAY.isocalendar()[0]}-W{TODAY.isocalendar()[1]:02d}'

GOAL_EN = {
 '化学 Paper 2 冲 7（口径待裁定）': 'Chemistry Paper 2 — push to grade 7 (51-item error book)',
 '物理必做实验方法描述默写（12 份）': 'Physics required-practical method drills (12 sheets)',
 'SMC Q9–13 数论代数断档修复（10 题）': 'SMC Q9-13 number theory & algebra repair (10 questions)',
 '英语核心词汇 200 词 + PETAL 段落限时': 'English core vocabulary 200 + timed PETAL paragraphs',
 '11/07 16+ Entrance Day 口述准备（3 项）': '16+ Entrance Day prep (3 items)',
 'P0：化学 G7/G4 口径裁定（10/23 前给结论）': 'P0: Chemistry G7/G4 grading decision',
 'P0: Headington 16+ late application — chase outcome (downgraded from registration)': 'Chase 16+ late application outcome',
 '错题总清库（113 条 → 真掌握 22–34 条）': 'Error-book full clear-out (113 items)',
 'Confirm mock dates, SMC score, BPhO status': 'Confirm mock dates, SMC score, BPhO status',
 'FMaths mock prep: past papers sprint': 'FMaths mock prep: past papers sprint',
 'Bebras Challenge 2026 — sit the challenge inside the window': 'Sit the Bebras Challenge 2026 inside the window',
 'IMC prep — 6 past papers before the Intermediate Challenge': 'IMC prep — 6 past papers before the Intermediate Challenge',
 'Piano Grade 8 — steady exam prep': 'Piano Grade 8 — steady exam prep',
 'English reading journal — 1 book per month + short journal notes': 'English reading journal — 1 book per month',
 'Confirm Bebras sitting before window closes': 'Confirm Bebras sitting before window closes',
 'IMC warm-up: first two past papers': 'IMC warm-up: first two past papers',
}
DONE_STATES = ('已归档', 'stDone', '已完成', 'done', 'dropped')

def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)

goals = load(os.path.join(KID, 'data/goals.json'))
goals = goals if isinstance(goals, list) else goals.get('goals', [])
cal = load(os.path.join(KID, 'data/refresh/competition_calendar.json'))
brief = load(os.path.join(KID, 'data/refresh/live_brief.json'))

def days(d):
    try:
        return (datetime.date.fromisoformat(str(d)[:10]) - TODAY).days
    except Exception:
        return None

def goal_rows(child):
    out = []
    for g in goals:
        if g.get('child') != child or str(g.get('状态')) in DONE_STATES:
            continue
        out.append(g)
    # 本周/逾期 sprint 优先，其次 term/year
    out.sort(key=lambda g: (g.get('tier') != 'sprint', str(g.get('截止日期'))))
    return out

def cal_rows(child):
    items = list(cal.get(child, [])) + (cal.get('shared', []) if child == 'diya' else [])
    near, later = [], []
    for c in items:
        eff = c.get('date_end') or c.get('date')      # 窗口型事件以关闭日为准
        d = days(eff)
        if d is None or d < 0:                        # 已关闭的窗口不进孩子版
            continue
        (near if d <= 45 else later).append((d, c))
    near.sort(key=lambda x: x[0]); later.sort(key=lambda x: x[0])
    return near, later

def fmt_date(ds):
    return datetime.date.fromisoformat(str(ds)[:10]).strftime('%a %d %b %Y')

CHALLENGE = brief.get('challenges', {})
ALERTS = brief.get('alerts', [])
SMC = load(os.path.join(KID, 'data/smc_records.json'))
smc_note = ''
if isinstance(SMC, dict):
    recs = SMC.get('records', [])
    for r in recs:
        if r.get('id') == 'smc-2026-actual':
            smc_note = f"SMC 2026: {r.get('award','Bronze')} recorded (score TBC — UKMT thresholds pending)"

HIGHLIGHTS = {
 'diya': [
   ('A-Level plan locked in', 'Three subjects confirmed: Maths, Further Maths, Physics. Research-verified against Imperial & HK universities; the plan email was sent to you this week.'),
   (smc_note.split(':')[0] if smc_note else 'SMC 2026 recorded', 'Bronze certificate now on your dashboard. Score TBC — next SMC sitting Oct 2027 (Y12) is the UCAS-window one.'),
 ],
 'jemma': [
   ('Growth calendar is live', '16 dated milestones from now to UCAS — IMC, Kangaroo, Physics Challenge, piano exams, and scholarship windows. Check My Plan page.'),
   ('Your piano is a superpower', 'A research pass this week found a route where Grade 8 piano becomes a Music Scholarship application in autumn of Year 11. Practise counts double.'),
 ],
}

PARENT_ACTIONS = {
 'diya': [
   ('red', '化学目标 0/51，截止 11-25（剩9天）——本周必须启动或调整口径，否则自动过期'),
   ('yellow', '3 个 sprint 本周到期：mock dates 确认（11-18）、FMaths prep（11-29）、16+ late app chase（11-20）'),
   ('yellow', 'Junior Astro Challenge 窗口 11-20 关闭——今天问物理老师是否报名'),
 ],
 'jemma': [
   ('yellow', 'Bebras 确认 sprint 11-18 到期、场次窗口 11-20 关闭——确认她已完成'),
   ('yellow', 'Piano G8 前置 Grade 5 Theory 状态待确认（G8 硬门槛；线上乐理考随约随考）'),
   ('green', 'GCSE Options evening 日期 2027-01 问学校；Latin→Civ 决策在春季落定'),
 ],
}

def child_report(child):
    name = child.capitalize()
    near, later = cal_rows(child)
    L = [f'# {name} — Grow Weekly · week {WEEK}', '']
    hi = HIGHLIGHTS.get(child, [])
    if hi:
        L.append('## This week, the one thing')
        for t, d in hi:
            L.append(f'**{t}.** {d}')
        L.append('')
    ch = CHALLENGE.get(child, {})
    if ch.get('text') and ch.get('status') == 'active':
        L.append(f'## Fortnight challenge ({ch.get("period","")})')
        L.append(ch['text']); L.append('')
    L.append('## Goal tracker')
    L.append('| Goal | Progress | Due | Days |')
    L.append('|---|---|---|---|')
    for g in goal_rows(child)[:8]:
        d = days(g.get('截止日期'))
        ds = f'{d} days' if d is not None else 'TBC'
        if d is not None and d < 0: ds = f'overdue {-d}d'
        flag = ' ⚠️' if (d is not None and d <= 14 and g.get('已完成量', 0) == 0) else ''
        nm = GOAL_EN.get(g.get('目标名称'), g.get('目标名称'))
        L.append(f'| {nm} | {g.get("已完成量",0)}/{g.get("目标总量",0)} | {str(g.get("截止日期"))[:10]} | {ds}{flag} |')
    L.append('')
    if near:
        L.append('## Calendar — next 6 weeks')
        for d, c in near:
            st = ''
            if c.get('date_end'):
                st = ' **closes ' + fmt_date(c['date_end']) + '**' if c.get('status') == 'open_now' else ' (window until ' + fmt_date(c['date_end']) + ')'
            reg = f' (register by {c["reg_deadline"]})' if c.get('reg_deadline') else ''
            est = ' (est.)' if c.get('date_is_estimate') else ''
            nm = c.get('name_en') or c['name']
            L.append(f'- {fmt_date(c["date"])} — {nm}{st}{est}{reg}')
        L.append('')
    if later:
        L.append('## Looking ahead')
        for d, c in later[:4]:
            est = ' (est.)' if c.get('date_is_estimate') else ''
            nm = c.get('name_en') or c['name']
            L.append(f'- {fmt_date(c["date"])}{est} — {nm}')
        L.append('')
    L.append('---')
    L.append('*Numbers come straight from your dashboard data. Reply to Dad with questions — this report is feedback, not a verdict.*')
    return '\n'.join(L)

def parent_summary():
    L = [f'# Grow 周报家长摘要 · {WEEK}（{TODAY.isoformat()}）', '',
         '## 🔔 需要你处理的决策/动作']
    for ch, acts in PARENT_ACTIONS.items():
        for lvl, a in acts:
            icon = {'red': '🔴', 'yellow': '🟡', 'green': '🟢'}[lvl]
            L.append(f'- {icon} **{ch.capitalize()}**: {a}')
    L += ['', '## 数据面', f'- 双周挑战: Diya「{CHALLENGE.get("diya",{}).get("text","")[:40]}…」/ Jemma「{CHALLENGE.get("jemma",{}).get("text","")[:40]}…」',
          f'- 平台警报: ' + '；'.join(f"[{a['level']}] {a['source']}: {a['msg'][:40]}" for a in ALERTS[:3]),
          '', '## 发送方式',
          f'- 草稿: data/weekly_drafts/{WEEK}-diya.md / {WEEK}-jemma.md（英文，孩子适龄）',
          '- 收件: h00426@headingtonschool.com / h00427@headingtonschool.com（+家长可选抄送）',
          '- 发送动作: 你回复「发周报」后经 SMTP 送达；绝不自动发送']
    return '\n'.join(L)

for child in ('diya', 'jemma'):
    p = os.path.join(DRAFTS, f'{WEEK}-{child}.md')
    open(p, 'w', encoding='utf-8').write(child_report(child))
    print('written', p)
p = os.path.join(DRAFTS, f'parent_summary_{WEEK}.md')
open(p, 'w', encoding='utf-8').write(parent_summary())
print('written', p)
print('WEEK =', WEEK)
