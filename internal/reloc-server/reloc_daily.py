#!/usr/bin/env python3
"""Relocation Agent 周循环 (每周一 12:00 CST = 07:00 伊斯坦布尔) · 行为规范 v2
1. 读任务图(6条主线:GATE闸门/家属签/4类主事项) → 逾期 / 关键路径 / 倒计时
2. 研究模块：选≤2条最久未研究的 research 任务 → Tavily 检索 → LLM 按6段规范输出 → findings表
3. 周一附加：反向审查（找遗漏+冲突假设）+ 闸门居留签/EID 每周确认请求
4. 输出周报 weekly_digest（≤15行：①已完成自动 ②需确认0-3 ③关键路径倒计时与风险）
   兼容保留 digest_latest.{json,txt} 旧字段供现有 API/页面消费。
红线：本脚本只产出研究与建议；永不代替人工执行。A-E 五类情形才生成【需确认】请求。
"""
import os, sys, json, sqlite3, time
from datetime import date, datetime, timedelta

sys.path.insert(0, '/www/wwwroot/kid/pipeline')
from common import load_env, llm_json, atomic_write_json  # 复用已验证LLM链(thinking-off+退避)

load_env()
DB = '/www/wwwroot/kid/data/reloc/tasks.db'
OUT_DIR = '/www/wwwroot/kid/data/reloc'
TODAY = date.today()
ENVS = {'tavily': 'TAVILY' + '_API_KEY'}

# ── 行为规范 v2：注入所有 LLM 调用，保证 Agent 自循环遵守 Bo 的约定 ──
BRIEF = (
 '【行为规范 v2·必须遵守】'
 '1 跟踪粒度：只跟踪6条主线(GATE-01工作签闸门/FAM-01家属签3状态/HOUSE-01住房/CAR-01用车/'
 'KIDS-01孩子教育/DOCS-01文件签署)。海波本人工作签由公司办理，除"居留签/Emirates ID是否已下发"'
 '一个状态外不拆解；家属签只记材料齐备→递交→获批；4类主事项只保留里程碑，不拆子任务。'
 '2 自动化：信息搜集/比价/筛选/去重/打分排序/材料清单生成并复用已有证件/表格邮件草稿/跟进催办/'
 '排期倒计时/依赖检查/风险预警/每周汇总 全部自动完成，不问海波。'
 '3 仅五类情形请确认：A付款/押金/定金/不可退费用；B签字/合同/法律承诺；C提交身份证件或敏感信息给第三方；'
 'D≥2选项得分差<10%且取舍取决于个人偏好；E信息缺失且无法检索或用已有文件补全。'
 '每次只问一个问题，格式：【需确认】事项｜推荐方案｜理由(一句话)｜截止时间｜回复"1"即同意；'
 '每周确认请求≤3条，同类合并。'
 '4 输出规范(每个调研主题)：①结论≤3行(推荐哪个/为什么/需海波做什么，没有则"无需操作")'
 '②对比表≤5候选、每候选≤6维度 ③评分规则一行(如成本40%/便利30%/孩子30%)'
 '④数据来源与日期(每条标注，查不到标"未核实"，禁止编造) ⑤风险与不确定点≤3条'
 '⑥下一步(自动执行/需确认分开列)。先结论后依据，能表格不段落，一主题一页，细节放附录折叠。'
 '5 节奏：每周1条汇总≤15行=①已完成(自动)②需确认(0-3条)③关键路径倒计时与风险；'
 '无待办只报"今日无需操作"；距搬迁锚点剩余周数变化时自动重排优先级。'
 '6 约束：价格/政策/费用标查询日期，重要结论≥2来源交叉核对；不确定就写"不确定"并说明如何核实，不猜；'
 '房租/学费/押金金额一律保留原币种并附折算。'
)

# ── 各研究任务检索词（英文检索效果好；结论输出中文）──
RESEARCH_QUERIES = {
 'HOUSE-01': ['Dubai Business Bay apartment rent 2027 Ejari annual lease family 2 bedroom commute Bay Gate Tower'],
 'CAR-01': ['UAE driving license conversion Chinese license Dubai 2027 expatriate car lease vs buy insurance Salik'],
 'KIDS-01': ['Dubai nursery admission KHDA fees waitlist 2027 British curriculum school term dates vaccination'],
 'DOCS-01': ['China UK birth certificate marriage certificate UAE embassy legalization 2026 apostille not required'],
 'FAM-01': ['UAE family visa spouse child entry permit status change Dubai dependents 2027 processing'],
 '_default': []
}

def tavily_search(query, max_results=5):
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
            print('[tavily] http', r.status_code, r.text[:120]); return None
        d = r.json()
        return {'answer': d.get('answer') or '',
                'results': [{'title': x.get('title', ''), 'url': x.get('url', ''),
                             'content': (x.get('content') or '')[:500]} for x in (d.get('results') or [])]}
    except Exception as e:
        print('[tavily] exc:', str(e)[:100]); return None

def db():
    con = sqlite3.connect(DB, timeout=10)
    con.execute('PRAGMA busy_timeout=8000'); con.row_factory = sqlite3.Row
    return con

def load_state(con):
    tasks = [dict(r) for r in con.execute('SELECT * FROM tasks ORDER BY phase, id')]
    meta = {r['k']: r['v'] for r in con.execute('SELECT * FROM meta')}
    return tasks, meta

def weeks_to_anchor(meta):
    a = str(meta.get('arrival_anchor') or '')
    try:
        d = date.fromisoformat(a[:10]); return max(0, (d - TODAY).days // 7)
    except Exception:
        return None

def compute_overdue(tasks):
    out = []
    for t in tasks:
        if t['status'] in ('done', 'dropped') or not t['due'] or t['due'] == '每周确认':
            continue
        try:
            dd = (date.fromisoformat(t['due']) - TODAY).days
        except Exception:
            continue
        if dd < 0:
            out.append({'id': t['id'], 'title': t['title'], 'due': t['due'], 'days': -dd,
                        'owner': t['owner'], 'status': t['status']})
    return sorted(out, key=lambda x: -x['days'])

def critical_path(tasks):
    cp = []
    for t in tasks:
        if t['status'] in ('done', 'dropped'): continue
        if t['risk'] == 'high' or t['status'] in ('waiting_on', 'blocked'):
            cp.append({'id': t['id'], 'title': t['title'], 'status': t['status'],
                       'owner': t['owner'], 'due': t['due'], 'risk': t['risk']})
    return cp[:8]

def gate_status(tasks):
    for t in tasks:
        if t['id'] == 'GATE-01':
            return {'id': 'GATE-01', 'status': t['status'], 'title': t['title'],
                    'issued': t['status'] == 'done'}
    return {'id': 'GATE-01', 'status': 'waiting_on', 'title': '', 'issued': False}

def family_status(tasks):
    for t in tasks:
        if t['id'] == 'FAM-01':
            return {'status': t['status'], 'stage': {'todo': '材料齐备', 'doing': '已递交',
                    'done': '获批'}.get(t['status'], t['status'])}
    return {'status': 'todo', 'stage': '材料齐备'}

def weekly_confirmations(tasks, meta, gate):
    """每周≤3条【需确认】：闸门每周确认固定1条 + 红线事项(付款/签字)按里程碑补。"""
    out = []
    if not gate['issued']:
        out.append({'id': 'GATE-01',
                    'item': '居留签 / Emirates ID 是否已下发（本周确认）',
                    'recommend': '未下发→答"未下发+预计时间"；已下发→回复"1"',
                    'why': '租房登记、车辆上牌、孩子入学均以其为前置条件。',
                    'deadline': '本周内'})
    for t in tasks:
        if t['human_gate'] and t['status'] not in ('done', 'dropped') and t['phase'] in ('HOUSE', 'CAR', 'KIDS'):
            out.append({'id': t['id'], 'item': str(t['title'])[:50],
                        'recommend': '材料已备好，确认后再执行',
                        'why': '涉及付款/签字/提交证件，属人工确认红线。',
                        'deadline': str(t['due'] or '按里程碑')})
        if len(out) >= 3: break
    return out[:3]

def pick_research_tasks(con):
    rows = con.execute("""
      SELECT t.id, t.title, MAX(f.created) AS last
      FROM tasks t LEFT JOIN findings f ON f.task_id = t.id
      WHERE t.status='research' GROUP BY t.id
      ORDER BY MIN(COALESCE(f.created,'0')) ASC, t.due ASC LIMIT 2""").fetchall()
    return [dict(r) for r in rows]

def research_one(con, tid, title):
    queries = RESEARCH_QUERIES.get(tid) or RESEARCH_QUERIES['_default'] or [title]
    q = queries[0]
    res = tavily_search(q)
    if not res: return None
    src_txt = '\n'.join(f"[{i+1}] {r['title']} — {r['url']}\n{r['content'][:300]}"
                        for i, r in enumerate(res['results']))
    now = datetime.now().isoformat(timespec='seconds')
    out = llm_json(
        BRIEF + '你是搬迁研究分析师(伊斯坦布尔→迪拜 全家搬迁)。严格只依据提供的来源；'
        '每条事实映射到来源编号；无法证实的政策进 risks/open_items 并标"未核实"；'
        '金额保留原币种并附折算；输出纯 JSON(中文)，遵循输出规范6段。',
        f'任务: {tid} {title}\n检索词: {q}\n今天: {TODAY.isoformat()}\n\n来源:\n{src_txt[:4500]}\n\n'
        '返回 JSON: {"conclusion":"≤3行中文结论,推荐哪个/为什么/需海波做什么",'
        '"table":[{"候选":"","维度1":"","维度2":"","综合得分":"","排名":""}],'
        '"score_rule":"一行权重说明",'
        '"sources":[{"title":"","url":"","date":"查询日期"}],'
        '"risks":["≤3条风险与不确定点"],'
        '"next_auto":["自动执行的下一步"],"next_confirm":["需海波确认的下一步"],'
        '"confidence":"高|中|低","open_items":["待核实项"]}', max_tokens=1600)
    out = out or {}
    concl = str(out.get('conclusion') or (res.get('answer') or '')[:400])
    conf = str(out.get('confidence') or '低(未加工)')
    table = out.get('table') or []
    # 6段结构整体存 conclusion(JSON)，source 存来源串，兼容页面旧纯文本渲染
    structured = {
        'conclusion': concl, 'table': table if isinstance(table, list) else [],
        'score_rule': str(out.get('score_rule') or ''),
        'sources': out.get('sources') or [],
        'risks': out.get('risks') or [],
        'next_auto': out.get('next_auto') or [], 'next_confirm': out.get('next_confirm') or [],
    }
    sources = ' || '.join(f"{r['title']}: {r['url']}" for r in res['results'][:5])
    con.execute('INSERT INTO findings(task_id,topic,conclusion,source,checked,confidence,open_items,created) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (tid, title[:60], json.dumps(structured, ensure_ascii=False), sources,
                 now[:10], conf, json.dumps(out.get('open_items') or [], ensure_ascii=False), now))
    con.commit()
    return {'task_id': tid, 'title': title, 'structured': structured, 'confidence': conf,
            'sources': [{'title': r['title'], 'url': r['url']} for r in res['results'][:3]],
            'open_items': out.get('open_items') or []}

def weekly_review(con, tasks):
    ctx = json.dumps([{'id': t['id'], 'title': t['title'], 'status': t['status'], 'due': t['due'],
                       'dep': t['depends_on'], 'owner': t['owner']} for t in tasks], ensure_ascii=False)[:6000]
    out = llm_json(
        BRIEF + '你是搬迁计划审计员。只找具体的(a)遗漏的关键里程碑 (b)相互矛盾的假设/日期/依赖；'
        '按行为规范跟踪粒度判断，不要把公司办理的工作签拆成任务；不要泛泛建议；纯 JSON 中文。',
        '任务图:\n' + ctx + '\n\n返回 {"gaps":[{"title":"缺失项","why":"≤20字","suggest":"建议动作"}],'
        '"conflicts":[{"a":"A","b":"B","desc":"矛盾描述"}]}', max_tokens=1000)
    return out or {}

def recheck_priority(con, meta):
    """剩余周数跨档(12/8/4)时记录并重排：写 meta priority_week，供前端高亮。"""
    w = weeks_to_anchor(meta)
    if w is None: return None
    tier = 12 if w > 12 else 8 if w > 8 else 4 if w > 4 else 0
    prev = meta.get('priority_tier')
    if str(tier) != prev:
        con.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', ('priority_tier', str(tier)))
        con.execute('INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)', ('priority_week', str(w)))
        con.commit()
        return {'weeks_left': w, 'tier': tier, 'changed': True, 'prev': prev}
    return {'weeks_left': w, 'tier': tier, 'changed': False}

def main():
    con = db()
    tasks, meta = load_state(con)
    overdue = compute_overdue(tasks)
    cp = critical_path(tasks)
    gate = gate_status(tasks)
    fam = family_status(tasks)
    wk = recheck_priority(con, meta)
    confirms = weekly_confirmations(tasks, meta, gate)
    researched = []
    for t in pick_research_tasks(con):
        r = research_one(con, t['id'], t['title'])
        if r:
            researched.append(r); time.sleep(1)
    review = weekly_review(con, tasks)
    done_auto = [f"研究 {len(researched)} 项 · 反审 {len(review.get('gaps') or [])} 缺口" if researched else '自动检查完成']
    cp_join = '；'.join(f"{c['id']}({c['status']})" for c in cp[:4])
    countdown = [f"距锚点约 {wk['weeks_left']} 周（{'优先级已重排' if wk['changed'] else '优先级稳定'}）"] if wk else []
    countdown.append(f"工作签闸门 GATE-01：{'已下发' if gate['issued'] else '待确认（本周需回复）'}")
    countdown.append(f"家属签 FAM-01：{fam['stage']}")
    if overdue: countdown.append(f"逾期 {len(overdue)} 项，最久 {overdue[0]['days']} 天")
    digest = {
        'date': TODAY.isoformat(), 'week_range': (TODAY - timedelta(days=TODAY.weekday())).isoformat(),
        'overdue': overdue, 'critical_path': cp,
        'actions': done_auto,            # 兼容旧字段：本周已完成(自动)
        'weekly': {'done_auto': done_auto, 'confirmations': confirms, 'countdown': countdown},
        'confirmations': confirms, 'gate': gate, 'family': fam, 'weeks_left': wk,
        'researched': researched, 'review': review,
        'meta': {k: meta.get(k, '') for k in ('arrival_anchor', 'work_location', 'housing')},
        'counts': {s: sum(1 for t in tasks if t['status'] == s) for s in
                   ('todo', 'research', 'waiting_on', 'blocked', 'doing', 'done')},
        'by': 'reloc-weekly-loop', 'rules_version': '2',
    }
    atomic_write_json(os.path.join(OUT_DIR, 'digest_latest.json'), digest)
    # TXT 周报 ≤15 行：①已完成 ②需确认 ③关键路径倒计时与风险
    L = [f"迪拜搬迁周报 · {TODAY.isoformat()}（截至 {digest['week_range']} 当周）",
         "① 已完成（自动）", f"  · {done_auto[0]}"]
    L.append("② 需确认（%d/3）" % len(confirms))
    if confirms:
        for c in confirms:
            L.append(f"  · 【需确认】{c['item']}｜推荐：{c['recommend']}｜{c['why']}｜截止{c['deadline']}｜回复\"1\"即同意")
    else:
        L.append("  · 本周无需确认")
    L.append("③ 关键路径倒计时与风险")
    for x in countdown: L.append("  · " + x)
    if cp_join: L.append("  · 关键路径: " + cp_join)
    if review.get('gaps'):
        g = review['gaps'][0]; L.append("  · 缺口: " + str(g.get('title','')) + " — " + str(g.get('suggest','')))
    txt = '\n'.join(L[:15])
    tp = os.path.join(OUT_DIR, 'digest_latest.txt')
    open(tp, 'w').write(txt)
    try:
        import pwd, grp
        uid, gid = pwd.getpwnam('www').pw_uid, grp.getgrnam('www').gr_gid
        os.chown(tp, uid, gid)
        os.chown(os.path.join(OUT_DIR, 'digest_latest.json'), uid, gid)
    except Exception: pass
    con.execute('INSERT INTO daily_log(run_date,kind,overdue_n,actions_n,researched_n,notes) VALUES(?,?,?,?,?,?)',
                (TODAY.isoformat(), 'weekly', len(overdue), len(done_auto), len(researched), ''))
    con.commit(); con.close()
    print(txt)
    print(f"\n[digest] written | rules=v2 | confirmed={len(confirms)} researched={len(researched)} overdue={len(overdue)}")

if __name__ == '__main__':
    main()