#!/usr/bin/env python3
"""来源健康检查（每 2 小时，systemd kid-health.timer）
- 检查各数据流年龄（poller 产物 / goals 打卡 / research / live_brief）与采集连续失败
- 写 data/refresh/source_health.json（live_brief 与周邮件"变化雷达"消费）
- 红色告警每日最多写一次 alerts.jsonl（防轰炸）
"""
import os, sys, json
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (DATA_DIR, REFRESH_DIR, log, now_iso, atomic_write_json,
                    read_json, run_logged, write_alert)

HEALTH_FILE = os.path.join(REFRESH_DIR, 'source_health.json')

# (name, path, green_days, yellow_days, note)
FILE_SOURCES = [
    ('tasks_diya', os.path.join(DATA_DIR, 'tasks_diya.json'), 10, 21, 'Diya homework/task feed (poller-hw)'),
    ('tasks_jemma', os.path.join(DATA_DIR, 'tasks_jemma.json'), 10, 21, 'Jemma homework/task feed (poller-hw)'),
    ('errors_diya', os.path.join(DATA_DIR, 'errors_diya.json'), 21, 45, 'Diya error-book feed (poller-error)'),
    ('errors_jemma', os.path.join(DATA_DIR, 'errors_jemma.json'), 21, 45, 'Jemma error-book feed (poller-error)'),
    ('smc_records', os.path.join(DATA_DIR, 'smc_records.json'), 45, 90, 'SMC score records (event-driven)'),
    ('goals', os.path.join(DATA_DIR, 'goals.json'), 14, 30, 'Goal Desk data'),
    ('live_brief', os.path.join(REFRESH_DIR, 'live_brief.json'), 16, 35, 'L3 biweekly refresh output'),
    ('route_compare', os.path.join(REFRESH_DIR, 'route_compare.json'), 20, 45, 'Route Compare data'),
    ('facts', os.path.join(REFRESH_DIR, 'facts.json'), 20, 45, 'Ground-truth facts'),
]


def age_days(path):
    try:
        return round((datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).total_seconds() / 86400, 1)
    except Exception:
        return None


def check_once():
    out = {'checked_at': now_iso(), 'sources': {}, 'red_count': 0, 'yellow_count': 0}
    state = read_json(os.path.join(REFRESH_DIR, 'collect_state.json'), {}) or {}

    for name, path, green, yellow, note in FILE_SOURCES:
        a = age_days(path)
        if a is None:
            level = 'red'
        elif a <= green:
            level = 'green'
        elif a <= yellow:
            level = 'yellow'
        else:
            level = 'red'
        out['sources'][name] = {'age_days': a, 'level': level, 'note': note}
        if level == 'red':
            out['red_count'] += 1
        elif level == 'yellow':
            out['yellow_count'] += 1

    # research_weekly（内容时间戳，不是 mtime——部署会刷 mtime）
    weekly = read_json(os.path.join(DATA_DIR, 'research_weekly.json'), {}) or {}
    try:
        wa = round((datetime.now() - datetime.fromisoformat(str(weekly.get('generated_at'))[:19])).total_seconds() / 86400, 1)
    except Exception:
        wa = None
    lvl = 'green' if (wa is not None and wa <= 8) else ('yellow' if (wa is not None and wa <= 20) else 'red')
    out['sources']['research_weekly'] = {'age_days': wa, 'level': lvl, 'note': 'weekly digest content stamp'}

    # 采集层运行状态
    coll = state.get('collect', {})
    per_q = state.get('per_query', {})
    fails = {k: v.get('consecutive_fail', 0) for k, v in per_q.items() if v.get('consecutive_fail', 0) >= 2}
    out['sources']['collect_30min'] = {
        'last_run': coll.get('last_run'), 'level': 'red' if fails else 'green',
        'note': f'failing queries: {fails}' if fails else 'all queries returning results'}
    if fails:
        out['red_count'] += 1

    # L3 是否按期跑（live_brief 内容时间戳 >16 天 → 提醒）
    atomic_write_json(HEALTH_FILE, out)

    # 红色告警每日一次
    today = date.today().isoformat()
    dedup = read_json(os.path.join(REFRESH_DIR, 'health_alert_dedup.json'), {}) or {}
    reds = [n for n, s in out['sources'].items() if s.get('level') == 'red']
    if reds and dedup.get('date') != today:
        write_alert('red', 'health', 'red sources: ' + ', '.join(reds))
        dedup = {'date': today, 'reds': reds}
        atomic_write_json(os.path.join(REFRESH_DIR, 'health_alert_dedup.json'), dedup)
    log(f'health: red={out["red_count"]} yellow={out["yellow_count"]} reds={reds}')
    return out


if __name__ == '__main__':
    run_logged('health', check_once)
