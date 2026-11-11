#!/usr/bin/env python3
"""邮件层：每周日 18:00 CST（10:00 UK）固定一封六板块周报。
板块：①本周一件事 ②变化雷达(红黄绿) ③倒计时 ④孩子专区(英文,鼓励式) ⑤家长决策提醒 ⑥下周预告
输出：weekly_digest.json / .html / .txt（.txt 供 Hermes cronjob 转发微信）
邮件：WEEKLY_MAIL_TO 环境变量（逗号分隔）未设置则跳过发送只落盘；SEND_TO_KIDS=1 追加孩子学校邮箱。
正文≤2屏，细节走链接。LLM 失败时全部板块有模板兜底，绝不编造数据。
"""
import os, sys, json
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (DATA_DIR, REFRESH_DIR, log, now_iso, atomic_write_json,
                    read_json, llm_json, run_logged)

TODAY = date.today()
LEVEL_ICON = {'red': '🔴', 'yellow': '🟡', 'green': '🟢'}


def week_changes():
    since = (datetime.now() - timedelta(days=7)).isoformat()[:19]
    out = []
    try:
        for line in open(os.path.join(REFRESH_DIR, 'changes_log.jsonl')):
            try:
                r = json.loads(line)
                if str(r.get('ts', '')) >= since:
                    out.append(r)
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return out


def goals_pulse():
    g = read_json(os.path.join(DATA_DIR, 'goals.json'), {}) or {}
    pulse = {'diya': [], 'jemma': []}
    today_s = date.today().isoformat()
    for goal in g.get('goals', []):
        c = goal.get('child') or 'diya'
        if c in pulse:
            expired = str(goal.get('截止日期', '')) < today_s and str(goal.get('状态', '')) not in ('stDone', '已完成', '已归档')
            pulse[c].append({'name': goal.get('目标名称'), 'progress': f"{goal.get('已完成量',0)}/{goal.get('目标总量','?')}",
                             'due': goal.get('截止日期'), 'status': ('EXPIRED-' + str(goal.get('状态'))) if expired else goal.get('状态'),
                             'tier': goal.get('tier')})
    return pulse


def build_digest():
    brief = read_json(os.path.join(REFRESH_DIR, 'live_brief.json'), {}) or {}
    facts = read_json(os.path.join(REFRESH_DIR, 'facts.json'), {}) or {}
    health = read_json(os.path.join(REFRESH_DIR, 'source_health.json'), {}) or {}
    events = read_json(os.path.join(REFRESH_DIR, 'events_pending.json'), []) or []
    changes = week_changes()
    pulse = goals_pulse()

    freshness = brief.get('freshness', {})
    countdowns = brief.get('countdowns', [])

    # ② 变化雷达
    radar = {'red': [], 'yellow': [], 'green': 0}
    for name, s in (health.get('sources') or {}).items():
        lv = s.get('level')
        if lv == 'red':
            radar['red'].append(f"{name} ({s.get('age_days')}d): {s.get('note','')}")
        elif lv == 'yellow':
            radar['yellow'].append(f"{name} ({s.get('age_days')}d)")
        else:
            radar['green'] += 1

    # ⑤ 家长决策提醒
    decisions = []
    for e in events:
        if e.get('requires_human') and e.get('status') in ('pending_review', 'review_notified'):
            decisions.append(f"[{e.get('child')}/{e.get('topic')}] {str(e.get('excerpt',''))[:120]}…")
    for c in ('diya', 'jemma'):
        for q in (facts.get(c, {}) or {}).get('open_questions', []):
            decisions.append(f"({c}) 待核实: {q}")
    for eg in brief.get('expired_goals', [])[:6]:
        decisions.append(f"({eg['child']}) 目标过期 {eg['days_overdue']}天: {eg['name']} [{eg['progress']}] → 需裁定 续期/降档/归档")

    # ⑥ 下周预告
    next_week = [c for c in countdowns if 0 <= c.get('days_left', 99) <= 10][:5]

    # ① 本周一件事 + ④ 孩子专区：LLM 一次生成，失败模板兜底
    top_change = max(changes, key=lambda x: x.get('importance', 0)) if changes else None
    one_thing, kids_zone = None, None
    try:
        llm = llm_json(
            'You write a weekly family planning email section. RULES: only use provided data; never invent. '
            'Items with status starting EXPIRED- or a due date in the past must NEVER be recommended as next actions '
            '(they await parent review). Reply pure JSON.',
            json.dumps({
                'task': 'Write (a) one_thing: the single most important thing this week for the parent, in Chinese, <=40 chars; '
                        '(b) kids_zone: for each child an encouraging note IN ENGLISH, age-appropriate, <=35 words, referencing real items only.',
                'top_change': top_change,
                'countdowns': countdowns[:5],
                'diya_pulse': pulse['diya'][:6],
                'jemma_pulse': pulse['jemma'][:6],
                'diya_facts': {'competitions': facts.get('diya', {}).get('competitions', [])},
                'jemma_facts': {'competitions': facts.get('jemma', {}).get('competitions', [])},
            }, ensure_ascii=False)[:4500],
            max_tokens=800)
        if isinstance(llm, dict):
            one_thing = llm.get('one_thing')
            kids_zone = llm.get('kids_zone')
    except Exception as e:
        log(f'[llm] digest polish failed: {e}')

    if not one_thing:
        if top_change:
            one_thing = f"本周最高优先变更：{top_change.get('child')}/{top_change.get('topic')}（重要度{top_change.get('importance')}）"
        elif next_week:
            one_thing = f"最近节点：{next_week[0]['what']}（{next_week[0]['days_left']}天后）"
        else:
            one_thing = "本周无重大变更；各数据流状态见变化雷达。"
    if not isinstance(kids_zone, dict):
        kids_zone = {}
    kz_default = {
        'diya': kids_zone.get('diya') if isinstance(kids_zone.get('diya'), str) else None,
        'jemma': kids_zone.get('jemma') if isinstance(kids_zone.get('jemma'), str) else None,
    }
    if not kz_default['diya']:
        kz_default['diya'] = ("Mocks are the FMaths gate — steady reps beat cramming. "
                              "Chem error-book track is the 6→7 mission. One topic a night, and you move.")
    if not kz_default['jemma']:
        kz_default['jemma'] = ("Bebras window is open until 20 Nov — go enjoy the puzzles. "
                               "Last year Distinction; this year, who knows where the ceiling is.")

    digest = {
        'generated_at': now_iso(), 'week_of': TODAY.isoformat(),
        'sections': {
            'one_thing': one_thing,
            'radar': radar,
            'countdowns': countdowns[:5],
            'kids_zone': kz_default,
            'decisions': decisions[:10],
            'next_week': next_week,
        },
        'stats': {'changes_7d': len(changes), 'events_open': sum(1 for e in events if 'pending' in str(e.get('status')) or 'notified' in str(e.get('status')))},
        'links': {'diya_dashboard': 'https://xgrow.me/app.html', 'jemma_dashboard': 'https://xgrow.me/jemma.html',
                  'full_intel': 'https://xgrow.me/research-weekly.html'},
    }
    return digest


def render_text(d):
    s = d['sections']
    L = []
    L.append(f"📮 Kid Platform Weekly · {d['week_of']}")
    L.append(f"\n① 本周一件事\n{s['one_thing']}")
    rad = s['radar']
    L.append(f"\n② 变化雷达  🔴{len(rad['red'])} 🟡{len(rad['yellow'])} 🟢{rad['green']}")
    for r in rad['red'][:4]:
        L.append(f"  🔴 {r}")
    for y in rad['yellow'][:4]:
        L.append(f"  🟡 {y}")
    L.append("\n③ 倒计时")
    for c in s['countdowns']:
        L.append(f"  · {c['what']} — {c['days_left']}天 ({c['date']}, {c.get('confidence','')})")
    L.append("\n④ 孩子专区 (English)")
    for k, v in s['kids_zone'].items():
        L.append(f"  {k.capitalize()}: {v}")
    L.append("\n⑤ 家长决策提醒")
    for x in s['decisions'][:6]:
        L.append(f"  → {x}")
    if not s['decisions']:
        L.append("  （无待决事项）")
    L.append("\n⑥ 下周预告")
    for c in s['next_week']:
        L.append(f"  · {c['what']} ({c['days_left']}天后)")
    L.append(f"\n🔗 Diya: {d['links']['diya_dashboard']} · Jemma: {d['links']['jemma_dashboard']} · 完整情报: {d['links']['full_intel']}")
    return '\n'.join(L)


def render_html(d, txt):
    import html as H
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
            '<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:680px;margin:24px auto;padding:0 16px;color:#212529">'
            f'<pre style="white-space:pre-wrap;font-family:inherit;font-size:14px;line-height:1.7">{H.escape(txt)}</pre>'
            '</body></html>')


def send_email(digest, txt):
    to_env = os.environ.get('WEEKLY_MAIL_TO', '').strip()
    kids = os.environ.get('SEND_TO_KIDS', '0') == '1'
    recipients = [x.strip() for x in to_env.split(',') if x.strip()]
    if kids:
        recipients += ['h00426@headingtonschool.com', 'h00427@headingtonschool.com']
    if not recipients:
        log('[email] WEEKLY_MAIL_TO not set and SEND_TO_KIDS!=1 — skip sending (digest files written)')
        return
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    SMTP_USER = os.environ.get('SMTP_USER', 'xgrow@qq.com')
    SMTP_PASS = os.environ.get('SMTP_PASS', '')
    if not SMTP_PASS:
        log('[email] SMTP_PASS missing — skip')
        return
    msg = MIMEMultipart('alternative')
    msg['From'] = SMTP_USER
    msg['Subject'] = f"📮 Kid Platform Weekly · {digest['week_of']}"
    msg.attach(MIMEText(render_html(digest, txt), 'html'))
    try:
        server = smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=30)
        server.login(SMTP_USER, SMTP_PASS)
        for r in recipients:
            msg['To'] = r
            server.send_message(msg)
            del msg['To']
            log(f'[email] sent to {r}')
        server.quit()
    except Exception as e:
        log(f'[email] FAILED: {e}')
        raise


def weekly_once():
    d = build_digest()
    txt = render_text(d)
    atomic_write_json(os.path.join(REFRESH_DIR, 'weekly_digest.json'), d)
    for ext, content in (('txt', txt), ('html', render_html(d, txt))):
        p = os.path.join(REFRESH_DIR, f'weekly_digest.{ext}')
        open(p, 'w').write(content)
        try:
            import pwd, grp
            os.chown(p, pwd.getpwnam('www').pw_uid, grp.getgrnam('www').gr_gid)
        except Exception:
            pass
    log(f'[digest] wrote weekly_digest.json/.txt/.html')
    send_email(d, txt)
    print('\n' + txt)
    return d


if __name__ == '__main__':
    run_logged('weekly', weekly_once)
