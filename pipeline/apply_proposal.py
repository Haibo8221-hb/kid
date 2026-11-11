#!/usr/bin/env python3
"""人工闸门落地器：把已确认的提案写入生产数据。
用法：
  apply_proposal.py --list                     # 列出最新一期提案
  apply_proposal.py --apply <id> --yes         # 落地单条（--yes 是硬要求 = 人工确认凭证）
  apply_proposal.py --apply-all --yes          # 全量落地（谨慎）
可落地类型：
  ch-<child>       challenge → facts.json[child].challenge
  exp-<goal_id>    过期目标处置 renew/downgrade/archive → goals.json
  ng-<i>           新双周目标 → goals.json（tier=sprint）
重大路径调整（route/weights/facts 结构性变更）不经此工具自动落地——始终由人+Hermes 会话内完成。
"""
import os, sys, json, glob, argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA_DIR, REFRESH_DIR, log, now_iso, atomic_write_json, read_json, append_jsonl

GOALS_FILE = os.path.join(DATA_DIR, 'goals.json')
FACTS_FILE = os.path.join(REFRESH_DIR, 'facts.json')
CHANGES_FILE = os.path.join(REFRESH_DIR, 'changes_log.jsonl')
NOW_Z = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def latest_proposals():
    dirs = sorted(glob.glob(os.path.join(REFRESH_DIR, 'proposals', '*')))
    for d in reversed(dirs):
        pf = os.path.join(d, 'proposals.json')
        if os.path.exists(pf):
            return pf, read_json(pf, {}) or {}
    return None, {}


def enumerate_items(p):
    items = []
    for child, ch in (p.get('challenges') or {}).items():
        if isinstance(ch, dict) and ch.get('text') and not ch.get('applied'):
            items.append(('ch-' + child, f"challenge[{child}]: {ch['text']} ({ch.get('period','')})"))
    for a in p.get('expired_goal_actions') or []:
        if not a.get('applied'):
            items.append((a.get('id') or 'exp-' + str(a.get('goal_id')),
                          f"expired[{a.get('child')}]: {a.get('goal_id')} → {a.get('action')} "
                          f"new_due={a.get('new_due')} ({a.get('reason','')})"))
    for i, g in enumerate(p.get('new_sprint_goals') or []):
        if not g.get('applied'):
            items.append((f'ng-{i}', f"new_goal[{g.get('child')}]: {g.get('name')} due={g.get('due')} metric={g.get('metric','')}"))
    return items


def apply_item(pid, p, pf):
    goals = read_json(GOALS_FILE, {}) or {}
    facts = read_json(FACTS_FILE, {}) or {}
    applied = None

    if pid.startswith('ch-'):
        child = pid[3:]
        ch = (p.get('challenges') or {}).get(child)
        if not ch:
            return f'challenge {child} not found'
        facts.setdefault(child, {})['challenge'] = {'text': ch.get('text'), 'period': ch.get('period'),
                                                    'status': 'active', 'as_of': now_iso(), 'source': 'L3 proposal (human confirmed)'}
        ch['applied'] = True
        atomic_write_json(FACTS_FILE, facts)
        applied = f'challenge[{child}] -> facts.json'

    elif pid.startswith('exp-'):
        gid = pid[4:]
        act = next((a for a in (p.get('expired_goal_actions') or []) if (a.get('id') or 'exp-' + str(a.get('goal_id'))) == pid), None)
        if not act:
            return f'expired action {gid} not found'
        action = act.get('action', 'review')
        found = False
        for g in goals.get('goals', []):
            if g.get('目标ID') == gid:
                found = True
                if action == 'renew':
                    if act.get('new_due'):
                        g['截止日期'] = act['new_due']
                    g['状态'] = '进行中'
                    g['最近更新'] = NOW_Z
                elif action == 'downgrade':
                    try:
                        g['目标总量'] = max(1, int(g.get('目标总量', 2)) // 2)
                    except Exception:
                        pass
                    if act.get('new_due'):
                        g['截止日期'] = act['new_due']
                    g['节奏提示'] = (g.get('节奏提示') or '') + ' [downgraded by L3 proposal]'
                    g['最近更新'] = NOW_Z
                elif action == 'archive':
                    g['状态'] = '已归档'
                    g['最近更新'] = NOW_Z
                else:
                    return f'unknown action {action} for {gid}'
                break
        if not found:
            return f'goal {gid} not found in goals.json'
        act['applied'] = True
        atomic_write_json(GOALS_FILE, goals)
        applied = f'goal[{gid}] action={action} -> goals.json'

    elif pid.startswith('ng-'):
        i = int(pid[3:])
        gs = p.get('new_sprint_goals') or []
        if i >= len(gs):
            return f'new goal index {i} not found'
        g = gs[i]
        child = g.get('child', 'diya')
        rec = {
            'record_id': f'{child}-ng-{int(datetime.now().timestamp())}', 'child': child,
            '目标ID': f'{child}-ng-{int(datetime.now().timestamp())}',
            '目标名称': g.get('name'), '模板': '练习', '领域': 'L3提案', '单位': '项',
            '目标总量': 1, '已完成量': 0, '开始日期': datetime.now().strftime('%Y-%m-%d'),
            '截止日期': g.get('due'), '状态': '待开始', '休息日': [], '休息额度': 0,
            '拆解计划': g.get('metric', ''), '障碍预案': '', '颜色': '#6366f1',
            '今日建议量': 0, '连续天数': 0, '投入分钟': 0, '节奏提示': g.get('why', ''),
            '收口方式': 'self check-in', '每日上限': 1, 'tier': 'sprint',
            'owner': child.capitalize(), 'owner_support': 'Haibo · AI reminders',
            '创建时间': NOW_Z, '最近更新': NOW_Z,
        }
        goals.setdefault('goals', []).append(rec)
        g['applied'] = True
        atomic_write_json(GOALS_FILE, goals)
        applied = f'new sprint goal[{child}]: {g.get("name")} -> goals.json'

    else:
        return f'unknown proposal id: {pid}'

    atomic_write_json(pf, p)
    append_jsonl(CHANGES_FILE, {'ts': now_iso(), 'type': 'proposal_applied', 'id': pid, 'detail': applied})
    return applied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--apply')
    ap.add_argument('--apply-all', action='store_true')
    ap.add_argument('--yes', action='store_true', help='人工确认凭证；缺省拒绝落地')
    args = ap.parse_args()

    pf, p = latest_proposals()
    if not pf:
        print('no proposals found')
        return
    print(f'proposals file: {pf} (status={p.get("status")}, llm={p.get("llm_generated")})')
    items = enumerate_items(p)
    if args.list or not (args.apply or args.apply_all):
        for pid, desc in items:
            print(f'  [{pid}] {desc}')
        if not items:
            print('  (nothing pending)')
        return
    if not args.yes:
        print('REFUSED: human confirmation gate — re-run with --yes after review')
        sys.exit(2)
    targets = [args.apply] if args.apply else [pid for pid, _ in items]
    for pid in targets:
        r = apply_item(pid, p, pf)
        log(f'[apply] {pid}: {r}')
        print(f'  {pid}: {r}')


if __name__ == '__main__':
    main()
