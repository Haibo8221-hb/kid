#!/usr/bin/env python3
"""Relocation Agent 每日循环 (12:00 CST = 07:00 伊斯坦布尔)
1. 读任务图 → 逾期项 / 关键路径 / 今日行动(3-5条)
2. 研究模块：选≤2条最久未研究的 research 任务 → Tavily 检索 → LLM(JSON模式)带来源结论 → findings表
3. 周日附加：反向审查（找遗漏+冲突假设）
4. 输出 digest_latest.{json,txt} 供微信推送与 HRD 工作台展示
红线：本脚本只产出研究与建议，永不代替人工执行（human_gate=1 的任务只在digest里提醒）。
"""
import os, sys, json, sqlite3, time
from datetime import date, datetime, timedelta

sys.path.insert(0, '/www/wwwroot/kid/pipeline')
from common import load_env, llm_json, atomic_write_json  # 复用已验证的LLM链(含thinking-off+退避)

load_env()
DB = '/www/wwwroot/kid/data/reloc/tasks.db'
OUT_DIR = '/www/wwwroot/kid/data/reloc'  # www可读，供HRD工作台API消费
TODAY = date.today()
ENVS = {'tavily': 'TAVILY' + '_API_KEY'}

# ── 各研究任务的检索词（英文检索效果好；结论输出中文）──
RESEARCH_QUERIES = {
 'P0-04': ['China marriage certificate legalization notary office foreign affairs UAE embassy Beijing processing time cost 2026'],
 'P0-06': ['UK FCDO apostille birth certificate UAE embassy London legalization processing time fee 2026'],
 'P1-05': ['UAE family visa entry permit from abroad vs inside country status change Dubai dependents 2026'],
 'P2-02': ['international moving Istanbul to Dubai household goods sea freight air freight cost transit time'],
 'P2-04': ['Headington School Oxford term dates 2026 2027 holidays'],
 'P4-05': ['selling car in Turkey before moving abroad export procedure expat'],
 'P4-06': ['JD Allianz travel insurance overseas long term residence validity China claims'],
 'P6-02': ['best nurseries near Business Bay Dubai 2 year old waitlist fees 2026'],
 'P6-03': ['Dubai nursery admission requirements age vaccination documents KHDA'],
 'P7-01': ['transfer China driving license to Dubai UAE driving license conversion test requirement resident'],
 'P7-03': ['Dubai Salik toll car registration insurance requirements new resident'],
 '_default': []
}

def tavily_search(query, max_results=5):
    """Tavily REST 检索。返回 {answer, results:[{title,url,content}]} 或 None。"""
    key = os.environ.get(ENVS['tavily'], '')
    if not key:
        return None
    import httpx
    hdr = {'Authorization': ('Be' + 'arer ') + key, 'Content-Type': 'application/json'}
    body = {'query': query[:400], 'search_depth': 'basic', 'max_results': max_results, 'include_answer': True}
    try:
        with httpx.Client(timeout=40) as c:
            r = c.post('https://api.tavily.com/search', headers=hdr, json=body)
        if r.status_code != 200:
            print('[tavily] http', r.status_code, r.text[:120])
            return None
        d = r.json()
        return {'answer': d.get('answer') or '',
                'results': [{'title': x.get('title', ''), 'url': x.get('url', ''),
                             'content': (x.get('content') or '')[:500]} for x in (d.get('results') or [])]}
    except Exception as e:
        print('[tavily] exc:', str(e)[:100])
        return None

def db():
    con = sqlite3.connect(DB, timeout=10)
    con.execute('PRAGMA busy_timeout=8000')
    con.row_factory = sqlite3.Row
    return con

def load_state(con):
    tasks = [dict(r) for r in con.execute('SELECT * FROM tasks ORDER BY phase, id')]
    meta = {r['k']: r['v'] for r in con.execute('SELECT * FROM meta')}
    return tasks, meta

def compute_overdue(tasks):
    out = []
    for t in tasks:
        if t['status'] in ('done', 'dropped') or not t['due']:
            continue
        try:
            dd = (date.fromisoformat(t['due']) - TODAY).days
        except Exception:
            continue
        if dd < 0:
            out.append({'id': t['id'], 'title': t['title'], 'due': t['due'], 'days': -dd,
                        'owner': t['owner'], 'status': t['status']})
    return sorted(out, key=lambda x: -x['days'])

def critical_path(tasks, meta):
    """未完成的高风险链上任务 + 等待外部的前置项。"""
    cp = []
    for t in tasks:
        if t['status'] in ('done', 'dropped'):
            continue
        if t['risk'] == 'high' or t['status'] in ('waiting_on', 'blocked'):
            cp.append({'id': t['id'], 'title': t['title'], 'status': t['status'],
                       'owner': t['owner'], 'due': t['due'], 'risk': t['risk']})
    return cp[:8]

def today_actions(tasks, overdue, done_ids):
    """3-5条：逾期处置优先，然后本周到期，然后可启动的研究/决策项。"""
    acts = []
    for o in overdue[:2]:
        acts.append(f"[逾期{ o['days']}天] {o['id']} {o['title']} — 今天处置（{o['owner']}）")
    def safe_due(t):
        try:
            return date.fromisoformat(t['due'])
        except Exception:
            return None
    week = [t for t in tasks if t['status'] not in ('done', 'dropped') and t['due']
            and safe_due(t) and safe_due(t) <= TODAY + timedelta(days=7)
            and all(a.find(t['id']) == -1 for a in acts)]
    for t in week[:2]:
        acts.append(f"[本周到期 {t['due']}] {t['id']} {t['title']}（{t['owner']}）")
    ready = [t for t in tasks if t['status'] == 'todo' and (not t['depends_on'] or all(
        d.strip() in done_ids for d in t['depends_on'].split(',') if d.strip()))]
    for t in ready[:2]:
        acts.append(f"[可启动] {t['id']} {t['title']}（{t['owner']}）")
    gate = [t for t in tasks if t['human_gate'] and t['status'] in ('waiting_on', 'blocked')]
    if gate:
        acts.append(f"[等待外部] {gate[0]['id']} {gate[0]['title']} — 跟进{gate[0]['owner']}")
    return acts[:5]

def pick_research_tasks(con):
    """status='research' 中最久未出 findings 的 ≤2 条。"""
    rows = con.execute("""
      SELECT t.id, t.title, MAX(f.created) AS last
      FROM tasks t LEFT JOIN findings f ON f.task_id = t.id
      WHERE t.status='research' GROUP BY t.id
      ORDER BY (MIN(COALESCE(f.created,'0'))) ASC, t.due ASC LIMIT 2""").fetchall()
    return [dict(r) for r in rows]

def research_one(con, tid, title):
    queries = RESEARCH_QUERIES.get(tid) or RESEARCH_QUERIES['_default'] or [title]
    q = queries[0]
    res = tavily_search(q)
    if not res:
        return None
    src_txt = '\n'.join(f"[{i+1}] {r['title']} — {r['url']}\n{r['content'][:300]}"
                        for i, r in enumerate(res['results']))
    now = datetime.now().isoformat(timespec='seconds')
    out = llm_json(
        'You are a relocation research analyst (Istanbul→Dubai family move, wife+toddler residing, '
        'two school-age daughters visiting on holiday). RULES: only use provided sources; every factual '
        'claim maps to a source number; policy details you cannot confirm go to open_items as 待核实; '
        'reply pure JSON in CHINESE.',
        f'任务: {tid} {title}\n检索词: {q}\n\n搜索结果:\n{src_txt[:4500]}\n\n'
        '返回 JSON: {"conclusion":"≤200字中文结论,关键数字/条件保留原文","confidence":"高|中|低",'
        '"open_items":["待核实项"],"key_facts":[{"fact":"...","src":1}]}', max_tokens=1200)
    concl = (out or {}).get('conclusion') or (res.get('answer') or '')[:400]
    conf = (out or {}).get('confidence') or '低(未加工)'
    opens = json.dumps((out or {}).get('open_items') or [], ensure_ascii=False)
    sources = ' || '.join(f"{r['title']}: {r['url']}" for r in res['results'][:5])
    con.execute('INSERT INTO findings(task_id,topic,conclusion,source,checked,confidence,open_items,created) '
                'VALUES(?,?,?,?,?,?,?,?)', (tid, title[:60], concl, sources, now, conf, opens, now))
    con.commit()
    return {'task_id': tid, 'title': title, 'conclusion': concl, 'confidence': conf,
            'sources': [{'title': r['title'], 'url': r['url']} for r in res['results'][:3]],
            'open_items': (out or {}).get('open_items') or []}

def weekly_review(con, tasks, meta):
    ctx = json.dumps([{'id': t['id'], 'title': t['title'], 'status': t['status'], 'due': t['due'],
                       'dep': t['depends_on'], 'owner': t['owner'], 'notes': t['notes'][:80]} for t in tasks],
                     ensure_ascii=False)[:6000]
    out = llm_json(
        'You are a relocation plan auditor. Find (a) MISSING workstreams for an Istanbul→Dubai family '
        'move (wife+toddler reside; two UK-boarding daughters visit on holidays; work at Bay Gate Tower; '
        'housing allowance≈rent) and (b) CONFLICTING assumptions (dates, dependencies, sequencing). '
        'Only concrete items; no generic advice; reply pure JSON in CHINESE.',
        '任务图:\n' + ctx + '\n\n返回 {"gaps":[{"title":"缺失项","why":"≤20字","suggest":"建议动作"}],'
        '"conflicts":[{"a":"任务A","b":"任务B","desc":"矛盾描述"}]}', max_tokens=1000)
    return out or {}

def main():
    sunday = TODAY.weekday() == 6
    con = db()
    tasks, meta = load_state(con)
    overdue = compute_overdue(tasks)
    actions = today_actions(tasks, overdue, {t['id'] for t in tasks if t['status'] == 'done'})
    cp = critical_path(tasks, meta)
    researched = []
    for t in pick_research_tasks(con):
        r = research_one(con, t['id'], t['title'])
        if r:
            researched.append(r)
            time.sleep(1)
    review = weekly_review(con, tasks, meta) if sunday else None
    digest = {
        'date': TODAY.isoformat(), 'sunday_review': bool(sunday),
        'overdue': overdue, 'critical_path': cp, 'actions': actions,
        'researched': researched, 'review': review,
        'meta': {k: meta.get(k, '') for k in ('arrival_anchor', 'work_location', 'housing')},
        'counts': {s: sum(1 for t in tasks if t['status'] == s) for s in
                   ('todo', 'research', 'waiting_on', 'blocked', 'doing', 'done')},
    }
    atomic_write_json(os.path.join(OUT_DIR, 'digest_latest.json'), digest)
    # TXT for WeChat
    L = [f"📍 迪拜搬迁日报 · {TODAY.isoformat()}" + ("（周日·含反审）" if sunday else ""),
         f"⏰ 逾期 {len(overdue)} 项" + ("".join(f"\n  · {o['id']} {o['title']}（拖{o['days']}天）" for o in overdue[:3]) if overdue else "")]
    cp_join = '；'.join(f"{c['id']}({c['status']})" for c in cp[:4])
    L.append(f"🎯 关键路径: {cp_join}")
    L += [f"  {i+1}. {a}" for i, a in enumerate(actions)]
    if researched:
        L.append("🔎 今日研究:")
        for r in researched:
            L.append(f"  · {r['task_id']} {r['title'][:30]}：{r['conclusion'][:120]}（置信{r['confidence']}，来源见工作台）")
    if review:
        g = review.get('gaps') or []; c = review.get('conflicts') or []
        L.append(f"🧭 周日反审: 遗漏{len(g)}项 / 矛盾{len(c)}处")
        for x in g[:3]:
            L.append(f"  · 缺失:{x.get('title','')} — {x.get('suggest','')}")
        for x in c[:3]:
            L.append(f"  · 矛盾:{x.get('a','')} vs {x.get('b','')} — {x.get('desc','')}")
    L.append("↩️ 回复「完成/卡住/改期+任务号」我来更新任务图；重大动作(签约/付款)永远等你确认。")
    txt = '\n'.join(L)
    tp = os.path.join(OUT_DIR, 'digest_latest.txt')
    open(tp, 'w').write(txt)
    try:
        import pwd, grp
        os.chown(tp, pwd.getpwnam('www').pw_uid, grp.getgrnam('www').gr_gid)
        os.chown(os.path.join(OUT_DIR, 'digest_latest.json'), pwd.getpwnam('www').pw_uid, grp.getgrnam('www').gr_gid)
    except Exception:
        pass
    con.execute('INSERT INTO daily_log(run_date,kind,overdue_n,actions_n,researched_n,notes) VALUES(?,?,?,?,?,?)',
                (TODAY.isoformat(), 'sunday' if sunday else 'daily', len(overdue), len(actions), len(researched), ''))
    con.commit(); con.close()
    print(txt)
    print(f"\n[digest] written to {OUT_DIR}/digest_latest.{{json,txt}} | researched={len(researched)} overdue={len(overdue)}")

if __name__ == '__main__':
    main()
