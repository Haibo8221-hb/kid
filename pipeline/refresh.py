#!/usr/bin/env python3
"""L3 刷新层（每两周，systemd kid-refresh.timer → Sat 17:00 CST，脚本内 ISO 周奇偶门控）

产出：
1. data/refresh/live_brief.json —— Dashboard 唯一消费的聚合文件：
   countdowns（未来45天） / freshness（三模块+数据流新鲜度） /
   changes_this_period（本期变化清单：新增/变更/过期） / expired_goals / alerts
2. data/refresh/proposals/YYYY-MM-DD/proposals.json —— 待人工确认的提案（LLM 生成，
   含 challenge / 过期目标处置 / 新双周行动）。LLM 失败时降级为纯数据提案，绝不编造。
3. 重大变更不自动落地 —— proposals 状态 pending_review，由 watchdog 微信通知家长，
   确认后 apply_proposal.py 落地（人工闸门）。
"""
import os, sys, json, glob
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (DATA_DIR, REFRESH_DIR, log, now_iso, atomic_write_json,
                    read_json, llm_json, run_logged)

FACTS_FILE = os.path.join(REFRESH_DIR, 'facts.json')
GOALS_FILE = os.path.join(DATA_DIR, 'goals.json')
CHANGES_FILE = os.path.join(REFRESH_DIR, 'changes_log.jsonl')
BRIEF_FILE = os.path.join(REFRESH_DIR, 'live_brief.json')
HEALTH_FILE = os.path.join(REFRESH_DIR, 'source_health.json')
RSTATE_FILE = os.path.join(REFRESH_DIR, 'refresh_state.json')
PROPOSALS_DIR = os.path.join(REFRESH_DIR, 'proposals')

TODAY = date.today()


def biweekly_gate():
    """ISO 周数偶数周执行（2026-11-14 = W46 → 执行）。FORCE=1 跳过门控。"""
    if os.environ.get('FORCE') == '1':
        return True
    wk = TODAY.isocalendar()[1]
    if wk % 2 != 0:
        log(f'biweekly gate: ISO week {wk} is odd — skip (runs on even weeks)')
        return False
    return True


def days_between(iso_date):
    try:
        return (date.fromisoformat(str(iso_date)[:10]) - TODAY).days
    except Exception:
        return None


def level_for_age(days, green=14, yellow=60):
    if days is None:
        return 'red'
    if days <= green:
        return 'green'
    if days <= yellow:
        return 'yellow'
    return 'red'


def file_age_days(path):
    try:
        return (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).days
    except Exception:
        return None


def iso_age_days(iso_ts):
    try:
        return (datetime.now() - datetime.fromisoformat(str(iso_ts)[:19])).days
    except Exception:
        return None


def build_countdowns(facts):
    out = []
    for child in ('diya', 'jemma'):
        for kd in (facts.get(child, {}).get('key_dates') or []):
            d = days_between(kd.get('date'))
            if d is None or d < -7 or d > 45:
                continue
            out.append({'child': child, 'date': kd.get('date'), 'what': kd.get('what'),
                        'days_left': d, 'confidence': kd.get('confidence', 'medium'),
                        'status': kd.get('status', 'confirmed')})
    out.sort(key=lambda x: x['days_left'])
    return out


def build_freshness(facts):
    """三模块 + 关键数据流的新鲜度。level: green<=14d / yellow<=60d / red>60d或未知。"""
    f = {}
    # 模块级
    rc = read_json(os.path.join(REFRESH_DIR, 'route_compare.json'), {}) or {}
    ages = []
    for c in ('diya', 'jemma'):
        u = rc.get(c, {}).get('updated')
        a = iso_age_days(u)
        if a is not None:
            ages.append(a)
    f['route_compare'] = {'age_days': min(ages) if ages else None,
                          'level': level_for_age(min(ages) if ages else None),
                          'note': 'route_compare.json updated stamp'}
    goals = read_json(GOALS_FILE, {}) or {}
    last_touch = None
    for g in goals.get('goals', []):
        for k in ('最近更新', '创建时间'):
            a = iso_age_days(g.get(k))
            if a is not None and (last_touch is None or a < last_touch):
                last_touch = a
    for c in goals.get('checks', []):
        a = iso_age_days(c.get('打卡日期'))
        if a is not None and (last_touch is None or a < last_touch):
            last_touch = a
    f['goal_desk'] = {'age_days': last_touch, 'level': level_for_age(last_touch),
                      'note': 'last goal edit or check-in'}
    fa = iso_age_days(facts.get('_meta', {}).get('created')) or 0
    f['facts'] = {'age_days': file_age_days(FACTS_FILE), 'level': level_for_age(file_age_days(FACTS_FILE)),
                  'note': 'facts.json'}
    # 数据流级
    weekly = read_json(os.path.join(DATA_DIR, 'research_weekly.json'), {}) or {}
    f['research'] = {'age_days': iso_age_days(weekly.get('generated_at')),
                     'level': level_for_age(iso_age_days(weekly.get('generated_at')), green=8),
                     'note': 'weekly digest regenerated each Monday + incremental merges'}
    for name, path, green in [
        ('errors_diya', os.path.join(DATA_DIR, 'errors_diya.json'), 21),
        ('errors_jemma', os.path.join(DATA_DIR, 'errors_jemma.json'), 21),
        ('tasks_diya', os.path.join(DATA_DIR, 'tasks_diya.json'), 10),
        ('tasks_jemma', os.path.join(DATA_DIR, 'tasks_jemma.json'), 10),
        ('smc_records', os.path.join(DATA_DIR, 'smc_records.json'), 45),
    ]:
        a = file_age_days(path)
        f[name] = {'age_days': a, 'level': level_for_age(a, green=green), 'note': 'file mtime'}
    return f


def read_changes_since(ts_iso):
    out = []
    try:
        for line in open(CHANGES_FILE):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not ts_iso or str(rec.get('ts', '')) >= str(ts_iso)[:19]:
                out.append(rec)
    except FileNotFoundError:
        pass
    return out


def expired_goals(goals):
    out = []
    for g in goals.get('goals', []):
        due = g.get('截止日期')
        st = str(g.get('状态', ''))
        d = days_between(due)
        if d is not None and d < 0 and st not in ('stDone', '已完成', 'done'):
            out.append({'child': g.get('child') or 'diya', 'goal_id': g.get('目标ID'),
                        'name': g.get('目标名称'), 'due': due, 'days_overdue': -d,
                        'status': st, 'progress': f"{g.get('已完成量', 0)}/{g.get('目标总量', '?')}{g.get('单位', '')}"})
    return out


def gen_proposals(facts, expired, changes):
    """LLM 生成提案（challenge/过期处置/新双周行动）。失败 → 纯数据降级提案。"""
    today_s = TODAY.isoformat()
    pdir = os.path.join(PROPOSALS_DIR, today_s)
    os.makedirs(pdir, exist_ok=True)
    pfile = os.path.join(pdir, 'proposals.json')

    base = {'generated_at': now_iso(), 'status': 'pending_review',
            'note': 'Human gate: nothing here is live until confirmed (apply_proposal.py --apply <id> --yes)',
            'challenges': {}, 'expired_goal_actions': [], 'new_sprint_goals': []}

    # 数据降级提案：过期目标默认进"需裁定"队列
    for e in expired:
        base['expired_goal_actions'].append({
            'id': f"exp-{e['goal_id']}", 'goal_id': e['goal_id'], 'child': e['child'],
            'action': 'review', 'reason': f"overdue {e['days_overdue']}d at {e['progress']}",
            'options': ['renew(+14d)', 'downgrade(target reduced)', 'archive'],
            'source': 'rule-based (LLM unavailable or skipped)'})

    llm_out = None
    try:
        ctx = {
            'today': today_s,
            'facts_summary': {c: {'grades': facts.get(c, {}).get('grades', [])[-4:],
                                  'competitions': facts.get(c, {}).get('competitions', []),
                                  'key_dates': facts.get(c, {}).get('key_dates', [])[:5],
                                  'open_questions': facts.get(c, {}).get('open_questions', [])}
                              for c in ('diya', 'jemma')},
            'expired_goals': expired,
            'recent_changes': [{'ts': c.get('ts'), 'child': c.get('child'), 'topic': c.get('topic'),
                                'importance': c.get('importance')} for c in changes[-25:]],
        }
        llm_out = llm_json(
            'You are the planning copilot for two UK-school students (Diya Y11 GCSE year, STEM/Imperial Engineering track; '
            'Jemma Y9 low-pressure exploration). Propose the next fortnight. RULES: only use data provided; never invent '
            'facts, dates or scores; where data is missing write 待核实. Challenges must be forward-looking actions that are '
            'still possible today — never propose preparing for an event whose date has already passed. Reply with pure JSON.',
            'Context:\n' + json.dumps(ctx, ensure_ascii=False)[:6000] + '\n\nReturn JSON:\n'
            '{"challenges":{"diya":{"text":"<=25 words, encouraging, concrete, in English","period":"YYYY-MM-DD..YYYY-MM-DD"},'
            '"jemma":{...}},\n'
            '"expired_goal_actions":[{"id":"exp-<goal_id>","goal_id":"...","child":"...","action":"renew|downgrade|archive",'
            '"new_due":"YYYY-MM-DD or null","reason":"<=20 words"}],\n'
            '"new_sprint_goals":[{"child":"diya|jemma","name":"English, <=12 words","due":"YYYY-MM-DD","metric":"quantifiable",'
            '"why":"<=20 words"}]}\n'
            'Max 2 new sprint goals per child. Challenges must reference real current items (e.g. Bebras window, Chem error-book, mocks).',
            max_tokens=1500)
    except Exception as e:
        log(f'[proposals] LLM failed: {e}')

    if isinstance(llm_out, dict):
        if isinstance(llm_out.get('challenges'), dict):
            base['challenges'] = llm_out['challenges']
        if isinstance(llm_out.get('expired_goal_actions'), list) and llm_out['expired_goal_actions']:
            base['expired_goal_actions'] = llm_out['expired_goal_actions']
        if isinstance(llm_out.get('new_sprint_goals'), list):
            base['new_sprint_goals'] = llm_out['new_sprint_goals'][:4]
        base['llm_generated'] = True
    else:
        base['llm_generated'] = False
        log('[proposals] LLM unavailable — rule-based fallback only')

    atomic_write_json(pfile, base)
    log(f'[proposals] wrote {pfile} (llm={base["llm_generated"]})')
    return pfile


def refresh_once():
    if not biweekly_gate():
        return {'skipped': 'odd week'}

    facts = read_json(FACTS_FILE, {}) or {}
    goals = read_json(GOALS_FILE, {}) or {}
    rstate = read_json(RSTATE_FILE, {}) or {}
    last_refresh = rstate.get('last_refresh')

    changes = read_changes_since(last_refresh)
    expired = expired_goals(goals)
    countdowns = build_countdowns(facts)
    freshness = build_freshness(facts)
    health = read_json(HEALTH_FILE, {}) or {}

    alerts = []
    for src, h in (health.get('sources') or {}).items():
        if h.get('level') == 'red':
            alerts.append({'level': 'red', 'source': src, 'msg': h.get('note', '')})
    # SMC 分数缺位这类"待输入"缺口
    for comp in (facts.get('diya', {}).get('competitions') or []):
        if 'pending' in str(comp.get('status', '')):
            alerts.append({'level': 'yellow', 'source': 'facts',
                           'msg': f"{comp.get('name')}: {comp.get('status')} — awaiting input"})

    change_summary = {
        'period_start': (last_refresh or 'never')[:10],
        'period_end': TODAY.isoformat(),
        'added_events': sum(1 for c in changes if c.get('importance', 0) >= 7),
        'changed_topics': len({c.get('topic') for c in changes}),
        'total_changes': len(changes),
        'expired_goals': len(expired),
        'items': [{'ts': c.get('ts'), 'child': c.get('child'), 'topic': c.get('topic'),
                   'importance': c.get('importance')} for c in changes[-30:]],
    }

    brief = {
        'generated_at': now_iso(),
        'generated_by': 'refresh.py (L3, biweekly)',
        'countdowns': countdowns,
        'freshness': freshness,
        'changes_this_period': change_summary,
        'expired_goals': expired,
        'alerts': alerts,
        'challenges': {c: (facts.get(c, {}) or {}).get('challenge', {}) for c in ('diya', 'jemma')},
    }
    atomic_write_json(BRIEF_FILE, brief)
    log(f'[live_brief] wrote {BRIEF_FILE}: {len(countdowns)} countdowns, '
        f'{change_summary["total_changes"]} changes, {len(expired)} expired goals, {len(alerts)} alerts')

    pfile = gen_proposals(facts, expired, changes)

    rstate['last_refresh'] = now_iso()
    rstate['last_proposals'] = pfile
    atomic_write_json(RSTATE_FILE, rstate)
    return {'brief': BRIEF_FILE, 'proposals': pfile, 'changes': change_summary}


if __name__ == '__main__':
    run_logged('refresh', refresh_once)
