#!/usr/bin/env python3
"""L4 深度复盘层（每季度：1/4/7/10 月第一个周六 18:00 CST，systemd kid-quarterly.timer）
专家组式全盘复盘：权重复核、路径选择、费用预算、与上季度决策对账。
输出：data/refresh/reviews/YYYY-Qn.md（复盘报告）+ proposals（全部 pending_review，人工闸门）。
定时器在 1-7 号每天触发，脚本自判"本月第一个周六"；FORCE=1 跳过门控。
"""
import os, sys, json
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (DATA_DIR, REFRESH_DIR, log, now_iso, atomic_write_json,
                    read_json, llm_complete, llm_json, run_logged)

TODAY = date.today()


def first_saturday(y, m):
    d = date(y, m, 1)
    while d.weekday() != 5:  # Sat
        d = d.replace(day=d.day + 1)
    return d


def gate():
    if os.environ.get('FORCE') == '1':
        return True
    if TODAY.month not in (1, 4, 7, 10):
        return False
    return TODAY == first_saturday(TODAY.year, TODAY.month)


def review_once():
    if not gate():
        log(f'quarterly gate: {TODAY} is not first Saturday of quarter month — skip')
        return {'skipped': True}

    facts = read_json(os.path.join(REFRESH_DIR, 'facts.json'), {}) or {}
    rc = read_json(os.path.join(REFRESH_DIR, 'route_compare.json'), {}) or {}
    brief = read_json(os.path.join(REFRESH_DIR, 'live_brief.json'), {}) or {}
    goals = read_json(os.path.join(DATA_DIR, 'goals.json'), {}) or {}
    smc = read_json(os.path.join(DATA_DIR, 'smc_records.json'), []) or []

    ctx = json.dumps({
        'today': TODAY.isoformat(),
        'facts': {c: {k: facts.get(c, {}).get(k) for k in ('profile', 'grades', 'competitions', 'decisions_log', 'open_questions')}
                  for c in ('diya', 'jemma')},
        'weights': rc.get('weights'),
        'diya_routes': rc.get('diya', {}).get('routes'),
        'jemma_routes': rc.get('jemma', {}).get('routes'),
        'goal_summary': [{'child': g.get('child'), 'name': g.get('目标名称'), 'status': g.get('状态'),
                          'due': g.get('截止日期'), 'progress': f"{g.get('已完成量',0)}/{g.get('目标总量','?')}"}
                         for g in goals.get('goals', [])],
        'smc_records': smc,
        'freshness': brief.get('freshness'),
        'changes_last_period': brief.get('changes_this_period', {}).get('items', [])[-20:],
    }, ensure_ascii=False)[:12000]

    report_md = llm_complete([
        {'role': 'system', 'content':
         'You are a quarterly expert-panel reviewer (UK G5 + HK admissions, engineering tracks) for two students: '
         'Diya (Y11, GCSE 2027, Imperial Engineering target, 2029 entry) and Jemma (Y9, exploration phase, 2031 entry). '
         'Write the quarterly review in CHINESE (markdown). Structure: ①本季度要事回顾 ②路径与权重复核（逐项给 维持/调整 建议+理由） '
         '③费用预算复核（数据不足则标 待核实，绝不编造数字） ④竞赛关键路径（BPhO/UKMT/Bebras/ESAT 时间线核对） '
         '⑤风险清单（按影响排序） ⑥下季度三件最重要的事. Every claim must carry 证据+置信度+日期. Rules: only use provided data; '
         'never invent scores, fees, or dates; mark gaps 待核实.'},
        {'role': 'user', 'content': 'Data:\n' + ctx}],
        max_tokens=3500, temperature=0.3)

    qn = f'{TODAY.year}-Q{(TODAY.month - 1) // 3 + 1}'
    rdir = os.path.join(REFRESH_DIR, 'reviews')
    os.makedirs(rdir, exist_ok=True)
    rfile = os.path.join(rdir, f'{qn}.md')
    header = (f'# 季度深度复盘 · {qn}\n\n> 生成: {now_iso()} · L4 review_quarterly.py · '
              f'LLM={"ok" if report_md else "FAILED(仅数据附录)"}\n\n')
    body = report_md or '（LLM 不可用——本季度仅保留数据附录，人工复盘。）'
    body += '\n\n---\n## 数据附录（自动生成）\n```json\n' + ctx[:6000] + '\n```\n'
    open(rfile, 'w').write(header + body)
    try:
        import pwd, grp
        os.chown(rfile, pwd.getpwnam('www').pw_uid, grp.getgrnam('www').gr_gid)
    except Exception:
        pass
    log(f'[review] wrote {rfile}')

    # 复盘衍生的调整建议 → proposals（人工闸门）
    adj = llm_json(
        'You propose quarterly adjustments for the family planning platform. Only use provided data. Reply pure JSON.',
        'Based on this review context, propose weight/route adjustments if warranted (empty arrays if none):\n'
        + ctx[:5000] + '\n\n{"weight_changes":{"<dim>":<new_weight>},"route_score_changes":[{"child":"","route_id":"","dim":"","score":0,"reason":""}],"notes":""}\n'
        'Weights must sum to 1.0. If unsure, return empty arrays — human reviews everything.',
        max_tokens=800)
    pdir = os.path.join(REFRESH_DIR, 'proposals', f'{TODAY.isoformat()}-L4')
    os.makedirs(pdir, exist_ok=True)
    atomic_write_json(os.path.join(pdir, 'proposals.json'), {
        'generated_at': now_iso(), 'status': 'pending_review', 'layer': 'L4',
        'review_file': rfile, 'l4_adjustments': adj or {}, 'challenges': {},
        'expired_goal_actions': [], 'new_sprint_goals': [],
        'note': 'L4 adjustments are NEVER auto-applied; weights/routes change only via human+Hermes session.'})
    return {'review': rfile}


if __name__ == '__main__':
    run_logged('quarterly', review_once)
