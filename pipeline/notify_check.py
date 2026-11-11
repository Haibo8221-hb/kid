#!/usr/bin/env python3
"""watchdog 消费口（Hermes cronjob 每 2 小时调用）
输出"需要海波知道的新东西"纯文本；没有则输出 QUIET（cronjob 保持静默）。
覆盖：高分事件(events_pending) / 新提案(proposals) / 红色告警(alerts 水位线后)。
输出后即标记 notified（水位线/状态写回），保证不重复推送。
"""
import os, sys, json, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import REFRESH_DIR, read_json, atomic_write_json, now_iso

EVENTS_FILE = os.path.join(REFRESH_DIR, 'events_pending.json')
ALERTS_FILE = os.path.join(REFRESH_DIR, 'alerts.jsonl')
WM_FILE = os.path.join(REFRESH_DIR, 'notify_watermark.json')
PROPOSALS_DIR = os.path.join(REFRESH_DIR, 'proposals')


def main():
    wm = read_json(WM_FILE, {}) or {}
    lines = []

    # 1. 高分事件
    events = read_json(EVENTS_FILE, []) or []
    dirty = False
    for e in events:
        if e.get('status') in ('pending_notify', 'pending_review') and not e.get('notified_at'):
            tag = 'DECISION NEEDED' if e.get('requires_human') else 'FYI'
            lines.append(f"[{tag}] ({e.get('child','?')}/{e.get('topic','?')}) imp={e.get('importance')} "
                         f"{e.get('ts','')[:16]}\n  {str(e.get('excerpt',''))[:280]}")
            e['notified_at'] = now_iso()
            e['status'] = 'review_notified' if e.get('requires_human') else 'notified'
            dirty = True
    if dirty:
        atomic_write_json(EVENTS_FILE, events)

    # 2. 新提案（未通知过的 proposals 目录）
    notified_props = set(wm.get('proposals_notified', []))
    for pfile in sorted(glob.glob(os.path.join(PROPOSALS_DIR, '*', 'proposals.json'))):
        if pfile in notified_props:
            continue
        p = read_json(pfile, {}) or {}
        n_ch = len(p.get('challenges', {}))
        n_exp = len(p.get('expired_goal_actions', []))
        n_new = len(p.get('new_sprint_goals', []))
        lines.append(f"[PROPOSALS READY] {os.path.basename(os.path.dirname(pfile))}: "
                     f"{n_ch} challenges, {n_exp} expired-goal actions, {n_new} new sprint goals. "
                     f"Review then: apply_proposal.py --list / --apply <id> --yes")
        notified_props.add(pfile)
    wm['proposals_notified'] = sorted(notified_props)[-20:]

    # 3. 红色告警（水位线之后的）
    last_alert_ts = wm.get('alerts_ts', '')
    newest = last_alert_ts
    try:
        for line in open(ALERTS_FILE):
            line = line.strip()
            if not line:
                continue
            try:
                a = json.loads(line)
            except Exception:
                continue
            if str(a.get('ts', '')) > last_alert_ts:
                if a.get('level') == 'red':
                    lines.append(f"[RED ALERT] ({a.get('source')}) {a.get('msg')}")
                newest = max(newest, str(a.get('ts', '')))
    except FileNotFoundError:
        pass
    wm['alerts_ts'] = newest
    atomic_write_json(WM_FILE, wm)

    if not lines:
        return  # 静默 = 无输出（no_agent watchdog 模式：空 stdout 不投递）
    print(f"=== KID PLATFORM WATCHDOG ({now_iso()}) ===")
    print('\n\n'.join(lines))
    print(f"\n({len(lines)} item(s))")


if __name__ == '__main__':
    main()
