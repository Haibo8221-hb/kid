#!/usr/bin/env python3
"""P0 修复自检：#1 错题增删接口、#2 child 白名单、#3 密码/Cookie、#4 登出吊销。"""
import json, os, sys, tempfile

tmp = tempfile.mkdtemp(prefix='kidverify_')
os.environ['DATA_DIR'] = tmp
os.environ['APP_PASSWORD'] = 'verify-pass-123'
os.environ['COOKIE_SECURE'] = '1'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_server as A

app = A.app
app.config['TESTING'] = True

# R-2：服务端读缓存 ttl=15s，会跨用例污染「写后立刻读」的断言（CI 偶发失败根因之一）
try:
    A._json_cache.clear()
except Exception:
    pass
FAILS = []

def check(cond, msg):
    if cond:
        print('  PASS  ' + msg)
    else:
        print('  FAIL  ' + msg)
        FAILS.append(msg)

class Authed:
    """带会话 cookie 的客户端（Flask 3 需用 set_cookie 注入）"""
    def __init__(self, tok):
        self.cl = app.test_client()
        self.cl.set_cookie('kid_auth', tok, domain='localhost')
    def get(self, u, **kw):    return self.cl.get(u, **kw)
    def post(self, u, **kw):   return self.cl.post(u, **kw)
    def delete(self, u, **kw): return self.cl.delete(u, **kw)
    def put(self, u, **kw):    return self.cl.put(u, **kw)

c = app.test_client()

print('== #3 密码 / Cookie ==')
r = c.post('/api/auth/login', json={'password': 'wrong'})
check(r.status_code == 401, f'错误密码 401（实际 {r.status_code}）')
r = c.post('/api/auth/login', json={'password': 'verify-pass-123'})
check(r.status_code == 200, f'正确密码 200（实际 {r.status_code}）')
sc = r.headers.get('Set-Cookie', '')
print('  Set-Cookie:', sc)
check('Secure' in sc, 'Cookie 带 Secure')
check('HttpOnly' in sc, 'Cookie 带 HttpOnly')
check('SameSite=Lax' in sc, 'Cookie 带 SameSite=Lax')
check(A.PASSWORD_CONFIGURED, 'APP_PASSWORD 已配置（非内置默认）')

print('== #4 登出吊销 ==')
tok = A._make_token()
cc = Authed(tok)
r = cc.get('/api/auth/status')
check(r.status_code == 200, f'登出前会话有效（实际 {r.status_code}）')
r = cc.post('/api/auth/logout')
check(r.status_code == 200, f'登出接口 200（实际 {r.status_code}）')
r = Authed(tok).get('/api/auth/status')
check(r.status_code == 401, f'登出后旧 token 401（实际 {r.status_code}）')
r = Authed(A._make_token()).get('/api/auth/status')
check(r.status_code == 200, f'登出后新登录 token 有效（实际 {r.status_code}）')

print('== #2 child/user 白名单（路径穿越）==')
cc = Authed(A._make_token())
for ep, payload in [('/api/planner', {'user': '../../evil', 'plan': {'a': 1}}),
                    ('/api/tasks', {'user': '../../evil', 'tasks': {}}),
                    ('/api/review', {'user': '../../evil', 'x': 1}),
                    ('/api/chat/stream', {'user': '../../evil', 'message': 'x'}),
                    ('/api/errors/diya', {'child': '../../evil', 'question': 'Q'})]:
    r = cc.post(ep, json=payload)
    writes = [p for p in os.listdir(tmp) if 'evil' in p]
    # 安全断言的核心：绝不能在 data/ 之外（或以其名）落盘。
    # 状态码不纳入断言：chat/stream 在无 LLM 配置时按设计返回 503，不代表白名单失效。
    check(not writes, f'{ep} 未写出穿越文件（返回 {r.status_code}，落盘 {writes}）')

print('== #1 错题 POST / DELETE ==')
import shutil
shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, exist_ok=True)   # 清掉前面用例写入的样例数据
cc = Authed(A._make_token())
r = cc.post('/api/errors/diya', json={'subject': 'Maths', 'question': 'Q1', 'solution': 'S1', 'type': 'algebra', 'tags': 't1'})
print('  POST ->', r.status_code, r.get_json())
check(r.status_code == 200, 'POST /api/errors/<child> 200（原 405）')
eid = (r.get_json() or {}).get('id')
check(bool(eid), '返回记录 id')
r = cc.get('/api/errors/diya')
n = len(r.get_json().get('errors', []))
check(n == 1, f'GET 可见新记录（实际 {n}）')
r = cc.delete(f'/api/errors/diya/{eid}')
print('  DELETE ->', r.status_code, r.get_json())
check(r.status_code == 200, 'DELETE /api/errors/<child>/<id> 200（原 405）')
r = cc.get('/api/errors/diya')
check(len(r.get_json().get('errors', [])) == 0, 'DELETE 后记录数归零')
r = cc.delete('/api/errors/diya/not-exist')
check(r.status_code == 404, f'删除不存在记录 404（实际 {r.status_code}）')
r = cc.post('/api/errors/diya', json={'subject': 'Maths', 'question': ''})
check(r.status_code == 400, f'空题目 400（实际 {r.status_code}）')

print('== 存量无 id 自动补齐 ==')
p = os.path.join(tmp, 'errors_diya.json')
open(p, 'w').write(json.dumps([{'subject': 'Phy', 'question': 'legacy', 'solution': '', 'type': 'x'}]))
A._json_cache.pop(p, None)
cc = Authed(A._make_token())
recs = cc.get('/api/errors/diya').get_json().get('errors', [])
check(bool(recs and recs[0].get('id')), '存量记录已自动补 id')
if recs:
    r = cc.delete(f"/api/errors/diya/{recs[0]['id']}")
    check(r.status_code == 200, f'存量记录可删除（实际 {r.status_code}）')


print('== #11 Notes 服务端持久化 ==')
_cc = Authed(A._make_token())
r = _cc.get('/api/notes/diya')
check(r.status_code == 200, f'GET notes 200（实际 {r.status_code}）')
check(r.get_json().get('payload') == {}, 'GET notes 初始 payload 为空对象')
r = _cc.put('/api/notes/diya', json={'payload': {'Maths': [{'id': 'n1', 'content': 'x'}], 'text': 'jemma-style'}, 'updatedAt': '2026-10-15T16:00:00'})
check(r.status_code == 200 and r.get_json().get('ok'), f'PUT notes 成功（实际 {r.status_code}）')
r = _cc.get('/api/notes/diya')
d = r.get_json()
check(d.get('updatedAt') == '2026-10-15T16:00:00', 'PUT 后 updatedAt 回读一致')
check(d['payload'].get('Maths') and d['payload']['Maths'][0]['id'] == 'n1', 'PUT 后 payload 回读一致（自由形态，不规定结构）')
r = _cc.put('/api/notes/diya', json={'payload': 'not-an-object'})
check(r.status_code == 400, f'payload 非对象应 400（实际 {r.status_code}）')
r = _cc.get('/api/notes/../../evil')
check(r.status_code != 200 or 'evil' not in str(r.get_json()), 'notes 接口受 _safe_child 白名单保护')
_np = os.path.join(tmp, 'notes_diya.json')
check(os.path.exists(_np), 'notes 落盘到 data/notes_<child>.json')
# 未登录不得读写（用全新客户端，避免复用前面用例的会话 cookie）
_fresh = app.test_client()
r = _fresh.get('/api/notes/diya')
check(r.status_code == 401, f'未登录 GET notes 应 401（实际 {r.status_code}）')
r = _fresh.put('/api/notes/diya', json={'payload': {}})
check(r.status_code == 401, f'未登录 PUT notes 应 401（实际 {r.status_code}）')

print('== #12 错题分页 ==')
_cc = Authed(A._make_token())
# 造 120 条样本，覆盖 113 条的真实规模
import shutil as _sh
_sh.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, exist_ok=True)
for i in range(120):
    _cc.post('/api/errors/diya', json={'question': f'Q{i}', 'subject': ['Maths','Physics'][i % 2],
                                       'priority': ['high','medium','low'][i % 3],
                                       'date': f'2026-01-{(i % 28) + 1:02d}'})
d = _cc.get('/api/errors/diya?page=1&limit=50').get_json()
check(d['total'] == 120, f'分页 total=120（实际 {d["total"]}）')
check(len(d['errors']) == 50, f'第1页 50 条（实际 {len(d["errors"])}）')
check(d['has_more'] is True, '第1页 has_more=True')
d2 = _cc.get('/api/errors/diya?page=3&limit=50').get_json()
check(len(d2['errors']) == 20, f'第3页 20 条（实际 {len(d2["errors"])}）')
check(d2['has_more'] is False, '末页 has_more=False')
# 分页切片顺序必须与不分页一致（服务端已统一排序）
allq = [e['question'] for e in _cc.get('/api/errors/diya').get_json()['errors']]
paged = []
for pg in (1, 2, 3):
    paged += [e['question'] for e in _cc.get(f'/api/errors/diya?page={pg}&limit=50').get_json()['errors']]
check(allq == paged, '分页拼接顺序与全量顺序一致（排序口径未漂移）')
# 科目筛选
d3 = _cc.get('/api/errors/diya?page=1&limit=50&subject=Maths').get_json()
check(d3['total'] == 60, f'subject=Maths 过滤后 60 条（实际 {d3["total"]}）')
check(all(e['subject'] == 'Maths' for e in d3['errors']), '筛选结果全部命中科目')
# 不传 page/limit 时保持旧行为（返回全部）
d4 = _cc.get('/api/errors/diya').get_json()
check(len(d4['errors']) == 120, f'不分页时返回全部 120 条（实际 {len(d4["errors"])}）')
# stats 增强字段
st = _cc.get('/api/errors/diya?stats=1').get_json()
check(st['total'] == 120 and 'mastered_total' in st and 'new_this_week' in st,
      'stats=1 提供 mastered_total / new_this_week（供分页前端计数）')
# 首屏传输量
import json as _json
first = len(_json.dumps(_cc.get('/api/errors/diya?page=1&limit=50').get_json(), ensure_ascii=False).encode())
full = len(_json.dumps(_cc.get('/api/errors/diya').get_json(), ensure_ascii=False).encode())
check(first < full * 0.6, f'首屏传输量显著下降：{first//1024}KB vs 全量 {full//1024}KB')

print('== 登录限流（新增-D）==')
_ip = '203.0.113.7'
for i in range(5):
    c.post('/api/auth/login', json={'password': 'definitely-wrong'}, environ_base={'REMOTE_ADDR': _ip})
r = c.post('/api/auth/login', json={'password': 'definitely-wrong'}, environ_base={'REMOTE_ADDR': _ip})
check(r.status_code == 429, f'连续失败达阈值后锁定（实际 {r.status_code}）')
r = c.post('/api/auth/login', json={'password': 'verify-pass-123'}, environ_base={'REMOTE_ADDR': _ip})
check(r.status_code == 429, f'锁定期内正确密码也被拒（实际 {r.status_code}）')
r = c.post('/api/auth/login', json={'password': 'verify-pass-123'}, environ_base={'REMOTE_ADDR': '198.51.100.9'})
check(r.status_code == 200, f'未失败来源不受影响（实际 {r.status_code}）')
A._login_rate_clear(_ip)
r = c.post('/api/auth/login', json={'password': 'verify-pass-123'}, environ_base={'REMOTE_ADDR': _ip})
check(r.status_code == 200, f'清除计数后可正常登录（实际 {r.status_code}）')


print('== Decide 决定卡持久化接口（2026-10-19：Latin 等自主选择）==')
dc = Authed(A._make_token())
r = app.test_client().get('/api/decide/jemma')
check(r.status_code == 401, f'未登录访问 decide 401（实际 {r.status_code}）')
r = dc.get('/api/decide/jemma')
d = r.get_json()
check(r.status_code == 200 and d['child'] == 'jemma' and d['payload'] == {},
      '初始 decide 快照为空')
r = dc.put('/api/decide/jemma', json={'payload': {'kid-stamp-jemma-latin': '继续学 Latin'},
                                      'updatedAt': '2026-10-18T22:10:00+00:00'})
check(r.status_code == 200 and (r.get_json() or {}).get('ok'), 'PUT decide 快照成功')
r = dc.get('/api/decide/jemma')
d = r.get_json()
check(d['payload'].get('kid-stamp-jemma-latin') == '继续学 Latin', 'GET 读回已存选择（刷新/跨设备真源）')
check(d['updatedAt'] == '2026-10-18T22:10:00+00:00', 'updatedAt 原样返回供前端比较新旧')
r = dc.put('/api/decide/jemma', json={'payload': 'not-a-dict'})
check(r.status_code == 400, f'payload 非对象 400（实际 {r.status_code}）')
check(A._safe_child('../../etc') == 'diya', '非法 child 回退白名单默认值（防路径穿越）')


print('== TWP 三周计划接口（R5：2026-10-19 数据外置）==')
tc = Authed(A._make_token())
r = app.test_client().get('/api/twp/diya')
check(r.status_code == 401, f'未登录访问 twp 401（实际 {r.status_code}）')
r = tc.get('/api/twp/diya')
d = r.get_json()
check(r.status_code == 200 and d['child'] == 'diya', 'GET twp 返回孩子标识')
check(isinstance(d.get('weeks'), list) and len(d['weeks']) >= 1,
      f'GET twp 有周数据（weeks={len(d.get("weeks") or [])}；种子或运行时）')
if d.get('weeks'):
    w1 = d['weeks'][0]
    check(all(k in w1 for k in ('label', 'start', 'end')),
          'weeks[0] 含 label/start/end 必填字段')
# PUT 全量覆盖 + 读回
_weeks = [{'id': 'tw1', 'label': 'Test Week', 'start': '2026-11-05', 'end': '2026-11-11',
           'goal': 'g', 'focus': 'f', 'actions': [{'id': 'a1', 'label': 'x', 'sub': 'y'}]}]
r = tc.put('/api/twp/diya', json={'weeks': _weeks, 'nextCycleEnd': '2026-11-11'})
check(r.status_code == 200 and (r.get_json() or {}).get('ok') and r.get_json()['version'] >= 1,
      'PUT twp 全量覆盖成功且版本号递增')
r = tc.get('/api/twp/diya')
d = r.get_json()
check(d['weeks'][0]['id'] == 'tw1' and d['nextCycleEnd'] == '2026-11-11',
      'GET 读回更新后的计划（改计划无需发版）')
r = tc.put('/api/twp/diya', json={'weeks': [{'label': '缺字段'}]})
check(r.status_code == 400, f'weeks 结构非法 400（实际 {r.status_code}）')
r = tc.put('/api/twp/diya', json={'weeks': []})
check(r.status_code == 400, f'空 weeks 400（实际 {r.status_code}）')
# 恢复种子内容（避免污染后续断言环境）
_seed = _json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     'data-seed', 'twp_diya.json'), encoding='utf-8'))
tc.put('/api/twp/diya', json=_seed)


A._json_cache.clear()
print('== R7 错因字段（cause PUT 读写回环 + stats 聚合）==')
ac = Authed(A._make_token())
r = ac.post('/api/errors/diya', json={'subject': 'Maths', 'question': 'R7 cause test', 'date': '2026-10-19'})
check(r.status_code == 200 and (r.get_json() or {}).get('ok'), 'POST 错题（R7 测试用）')
_errs = ac.get('/api/errors/diya').get_json()
_errs = _errs if isinstance(_errs, list) else (_errs.get('errors') or [])
_rid = next((e['id'] for e in _errs if e.get('question') == 'R7 cause test'), None)
check(_rid is not None, '新错题有 id（R7 前置）')
r = ac.put(f'/api/errors/diya/{_rid}', json={'cause': 'concept', 'mastered': False})
check(r.status_code == 200 and (r.get_json() or {}).get('ok'), 'PUT cause=concept 成功（R7 自测回写）')
def _err_list():
    d = ac.get('/api/errors/diya').get_json()
    return d if isinstance(d, list) else (d.get('errors') or d.get('entries') or [])
_hit = next((e for e in _err_list() if isinstance(e, dict) and e.get('id') == _rid), {})
check(_hit.get('cause') == 'concept', 'GET 读回 cause 字段')
r = ac.get('/api/errors/diya?stats=1')
d = r.get_json()
check(d.get('by_cause', {}).get('concept') == 1, f"stats by_cause 聚合正确（{d.get('by_cause')}）")
r = ac.put(f'/api/errors/diya/{_rid}', json={'cause': ''})
_hit = next((e for e in _err_list() if isinstance(e, dict) and e.get('id') == _rid), {})
check(_hit.get('cause', '') == '', 'PUT 空串清除错因（自评会了时复用）')
r = ac.delete(f'/api/errors/diya/{_rid}')
check(r.status_code == 200, '清理 R7 测试错题')

A._json_cache.clear()
print('== Dashboard twp 聚合（R2：三周计划勾选与日常任务分开统计）==')
ac = Authed(A._make_token())
r = ac.post('/api/tasks', json={'user': 'diya', 'tasks': {
    'general-maths-drill': True,
    'english-petal': {'done': False, 'name': 'PETAL'},
    'twp-diya-w1-d1': True,
    'twp-diya-w1-d2': False,
}})
check(r.status_code == 200 and (r.get_json() or {}).get('ok'), 'POST /api/tasks 写入含 twp 键的任务集')
r = ac.get('/api/dashboard/diya')
d = r.get_json()
check(d['tasks_total'] == 2 and d['tasks_done'] == 1,
      f"日常任务统计不含 twp 键（total={d['tasks_total']}, done={d['tasks_done']}）")
check(d.get('twp_total') == 2 and d.get('twp_done') == 1,
      f"twp 完成度单独聚合（done={d.get('twp_done')}/{d.get('twp_total')}）")
# 清理：恢复空任务集，避免影响其他用例
ac.post('/api/tasks', json={'user': 'diya', 'tasks': {}})


A._json_cache.clear()
print('== 目标台 API（seed / ai 端点）==')
ac2 = Authed(A._make_token())
r = ac2.get('/api/goals')
check(r.status_code == 200 and 'goals' in (r.get_json() or {}), 'GET /api/goals 返回三段结构')
r = ac2.post('/api/goals/seed', json={})
_d = r.get_json() or {}
check(r.status_code == 200 and _d.get('ok') is True and _d.get('goals', 0) >= 8,
      f"POST /api/goals/seed 注入示例目标（{_d.get('goals')} 个）")
r = ac2.post('/api/goals/seed', json={})
_d2 = r.get_json() or {}
check(_d2.get('ok') is False and _d2.get('reason') == 'already_has_goals',
      'seed 幂等保护：已有目标时拒绝覆盖')
_goals = (ac2.get('/api/goals').get_json() or {}).get('goals') or []
check(len(_goals) >= 8 and _goals[0].get('目标名称'), f"seed 后目标可读（{len(_goals)} 个）")
_g0 = _goals[0]
check(all(k in _g0 for k in ('目标ID', '目标名称', '单位', '目标总量', '截止日期', '状态', '休息日')),
      'seed 目标含页面渲染所需字段')


def _make_client(content):
    """构造只返回固定模型输出的假 httpx.Client（避免测试走真实网络）。"""
    class _R:
        status_code = 200
        def json(self):
            return {'choices': [{'message': {'content': content}}]}
    class _C:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, *a, **k):
            return _R()
    return _C


_cfg_backup = A._load_config
_httpx_backup = A.httpx.Client
_name0 = _g0.get('目标名称')
try:
    A._load_config = lambda: {'api_key': 'test-key', 'base_url': 'https://example.invalid/v1',
                               'model': 'test-model', 'temperature': 0.2}
    # ① 正常路径：模型返回 Markdown 包裹的 JSON，含 update_goal + create_goal（必填字段齐全）
    _deadline = (A.datetime.now() + A.timedelta(days=45)).strftime('%Y-%m-%d')
    A.httpx.Client = _make_client(
        'Regrouped by subject and added a daily review:\n```json\n'
        '{"reply":"Regrouped by subject and added a daily review goal.","operations":['
        '{"op":"update_goal","target":"' + _name0 + '","patch":{"领域":"fldMa","每日上限":15}},'
        '{"op":"create_goal","goal":{"目标名称":"每日复习 20 个单词","单位":"个","目标总量":600,'
        '"截止日期":"' + _deadline + '","每日上限":20,"领域":"fldEn","状态":"stDoing"}}]}'
        '\n```')
    _before = len(_goals)
    r = ac2.post('/api/goals/ai', json={'message': '把学习目标改成按学科分类，再加一个每日复习任务'})
    d = r.get_json() or {}
    check(r.status_code == 200 and d.get('ok') is True, 'AI 端点成功解析模型指令')
    check('Regrouped by subject' in str(d.get('reply') or ''), 'AI 回显模型 reply')
    _applied = d.get('applied') or []
    check(any(a.get('op') == 'update_goal' for a in _applied), 'AI 产出 update_goal 操作')
    check(any(a.get('op') == 'create_goal' for a in _applied), 'AI 产出 create_goal 操作')
    _after = (ac2.get('/api/goals').get_json() or {}).get('goals') or []
    check(len(_after) == _before + 1, f'AI 新建目标已落库（{_before} → {len(_after)}）')
    _hit = [g for g in _after if g.get('目标名称') == '每日复习 20 个单词']
    check(bool(_hit) and _hit[0].get('领域') == 'fldEn', 'AI 新建目标使用稳定选项 id（fldEn）')
    check(bool(_hit) and str(_hit[0].get('每日上限')) in ('20', '20.0'), 'AI 新建目标落库每日上限=20')
    check(bool(_hit) and _hit[0].get('状态') == 'stDoing', 'AI 新建目标状态用稳定 id（stDoing）')
    _t = [g for g in _after if g.get('目标名称') == _name0]
    check(bool(_t) and str(_t[0].get('每日上限')) in ('15', '15.0'), 'AI 修改字段已落库（每日上限=15）')
    check(bool(_t) and _t[0].get('领域') == 'fldMa', 'AI 按学科分类已落库（领域=fldMa）')
    # ② 白名单：非法字段被忽略，合法字段照常生效
    A.httpx.Client = _make_client(
        '{"reply":"x","operations":[{"op":"update_goal","target":"' + _name0 +
        '","patch":{"未知字段":"v","领域":"fldSci"}}]}')
    ac2.post('/api/goals/ai', json={'message': '改个字段'})
    _t2 = [g for g in (ac2.get('/api/goals').get_json() or {}).get('goals') or []
           if g.get('目标名称') == _name0]
    check(bool(_t2) and '未知字段' not in _t2[0], 'AI 白名单：非法字段被忽略')
    check(bool(_t2) and _t2[0].get('领域') == 'fldSci', 'AI 白名单内的字段正常生效')
    # ②b 严格策略（红队 P0-2）：create_goal 必填字段缺失 → 不创建 + reply 如实说明
    A.httpx.Client = _make_client(
        '{"reply":"Added a review goal.","operations":['
        '{"op":"create_goal","goal":{"目标名称":"缺字段目标","单位":"个","目标总量":100}}]}')
    _cnt2 = len((ac2.get('/api/goals').get_json() or {}).get('goals') or [])
    r = ac2.post('/api/goals/ai', json={'message': '加个目标'})
    d = r.get_json() or {}
    check(r.status_code == 200 and d.get('ok') is True, 'AI 缺字段请求仍正常返回')
    check('create_goal' not in [a.get('op') for a in (d.get('applied') or [])],
          'AI 缺必填字段时拒绝创建（无 create_goal 落库）')
    check('NOT created' in str(d.get('reply') or ''), 'AI 缺字段时 reply 如实说明未创建')
    check(len((ac2.get('/api/goals').get_json() or {}).get('goals') or []) == _cnt2, '缺字段时目标数不变')
    # ②d delete_goal → 归档（数据保留），不得硬删除
    A.httpx.Client = _make_client(
        '{"reply":"Archived it.","operations":['
        '{"op":"delete_goal","target":"' + _name0 + '"}]}')
    r = ac2.post('/api/goals/ai', json={'message': '删掉这个目标'})
    d = r.get_json() or {}
    check('archive_goal' in [a.get('op') for a in (d.get('applied') or [])],
          'AI 删除请求转为归档（archive_goal）')
    _after2 = (ac2.get('/api/goals').get_json() or {}).get('goals') or []
    check(any(g.get('目标名称') == _name0 for g in _after2), '归档后目标数据仍保留（未硬删除）')
    _t3 = [g for g in _after2 if g.get('目标名称') == _name0]
    check(bool(_t3) and _t3[0].get('状态') == 'stGive', '归档目标状态=stGive')
    # ③ 模型返回非 JSON → 不改数据
    A.httpx.Client = _make_client('我看了一下，建议你先休息')
    _cnt = len((ac2.get('/api/goals').get_json() or {}).get('goals') or [])
    r = ac2.post('/api/goals/ai', json={'message': '随便聊聊'})
    d3 = r.get_json() or {}
    check(d3.get('ok') is False and d3.get('error') == 'bad_llm_output', 'AI 遇到非 JSON 回复时不误改数据')
    check(len((ac2.get('/api/goals').get_json() or {}).get('goals') or []) == _cnt, '非 JSON 回复后目标数不变')
finally:
    A._load_config = _cfg_backup
    A.httpx.Client = _httpx_backup

r = ac2.post('/api/goals/ai', json={'message': '  '})
check(r.status_code == 400, 'AI 端点拒绝空消息')
check(A._parse_ai_json('```json\n{"reply":"ok","operations":[]}\n```') is not None,
      'AI 解析器容忍 ```json 代码块')
check(A._parse_ai_json('好的，已处理：{"reply":"x","operations":[]}') is not None,
      'AI 解析器从夹带文字中抽取 JSON')
check(A._parse_ai_json('完全不是 JSON') is None, 'AI 解析器对不可解析内容返回 None（不误改数据）')
r = app.test_client().post('/api/goals/ai', json={'message': 'x'})
check(r.status_code == 401, '目标台 API 复用平台鉴权（未登录 401）')


A._json_cache.clear()
print('== 目标台 child 隔离（Diya / Jemma 各自数据）==')
_ac = Authed(A._make_token())
# jemma 建一个、diya 建一个
r = _ac.post('/api/goals/goal', json={'child': 'jemma', 'goal': {'目标ID': 'jj1', '目标名称': 'ZZJMARKZZ'}})
check(r.status_code == 200, 'jemma 建目标成功')
r = _ac.post('/api/goals/goal', json={'child': 'diya', 'goal': {'目标ID': 'dd1', '目标名称': 'ZZDMARKZZ'}})
check(r.status_code == 200, 'diya 建目标成功')
_j = (_ac.get('/api/goals?child=jemma').get_json() or {})
_d = (_ac.get('/api/goals?child=diya').get_json() or {})
_jn = [g.get('目标名称') for g in (_j.get('goals') or [])]
_dn = [g.get('目标名称') for g in (_d.get('goals') or [])]
check('ZZJMARKZZ' in _jn and 'ZZDMARKZZ' not in _jn, f'jemma 只看到自己的目标（{_jn}）')
check('ZZDMARKZZ' in _dn and 'ZZJMARKZZ' not in _dn, f'diya 只看到自己的目标（{_dn}）')
# 不传 child 时默认 diya（向后兼容）
_anon = [g.get('目标名称') for g in (_ac.get('/api/goals').get_json() or {}).get('goals') or []]
check('ZZDMARKZZ' in _anon and 'ZZJMARKZZ' not in _anon, f'不传 child 等同 diya（{_anon}）')
# 非法 child 回落 diya（防越权读取）
_bad = [g.get('目标名称') for g in (_ac.get('/api/goals?child=hacker').get_json() or {}).get('goals') or []]
check('ZZDMARKZZ' in _bad and 'ZZJMARKZZ' not in _bad, '非法 child 回落 diya，不泄露 jemma 数据')
# checkin 隔离
_ac.post('/api/goals/checkin', json={'child': 'jemma', 'check': {'记录ID': 'ck-j1', '目标ID': 'jj1',
                                                          '打卡日期': '2026-10-21', '本次完成量': 5},
                                     'goal_patch': {'已完成量': 5}})
_jc = len((_ac.get('/api/goals?child=jemma').get_json() or {}).get('checks') or [])
_dc = len((_ac.get('/api/goals?child=diya').get_json() or {}).get('checks') or [])
check(_jc == 1 and _dc == 0, f'打卡按 child 隔离（jemma={_jc} diya={_dc}）')
# PUT 隔离：jemma 不能改 diya 的目标
r = _ac.put('/api/goals/goal/dd1', json={'child': 'jemma', '目标总量': 999})
check(r.status_code == 404, f'jemma 不能修改 diya 的目标（返回 {r.status_code}）')
_dd = [g for g in ((_ac.get('/api/goals?child=diya').get_json() or {}).get('goals') or [])
       if g.get('目标ID') == 'dd1']
check(_dd and _dd[0].get('目标总量') != 999, 'diya 目标未被越权修改')
# seed 隔离：给 jemma 播种不影响 diya
_before_d = len((_ac.get('/api/goals?child=diya').get_json() or {}).get('goals') or [])
_ac.post('/api/goals/seed', json={'child': 'jemma', 'keep': True})
_after_d = len((_ac.get('/api/goals?child=diya').get_json() or {}).get('goals') or [])
_j2 = len((_ac.get('/api/goals?child=jemma').get_json() or {}).get('goals') or [])
check(_after_d == _before_d, f'给 jemma 播种不影响 diya 数据（{_before_d} → {_after_d}）')
check(_j2 >= 8, f'jemma 播种成功（{_j2} 个目标）')
# AI 端点隔离
_seen = {}


def _spy_client(content):
    class _R:
        status_code = 200

        def json(self):
            _seen['prompt_goals'] = content
            return {'choices': [{'message': {'content': content}}]}

    class _C:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, *a, **k):
            # 记录真实发给模型的 messages（隔离验证要看的就是 prompt 里有哪些目标）
            body = k.get('json')
            if body is None and a:
                body = a[0]
            if isinstance(body, dict) and body.get('messages'):
                _seen['messages'] = str(body['messages'])
            elif isinstance(body, str):
                _seen['messages'] = body
            return _R()
    return _C


_cfg2 = A._load_config
_hx2 = A.httpx.Client
try:
    A._load_config = lambda: {'api_key': 'k', 'base_url': 'https://example.invalid/v1',
                               'model': 'm', 'temperature': 0.2}
    # 前面的 seed(keep=True) 会整体替换该孩子的目标，所以这里先重建两个标记目标
    _ac.post('/api/goals/goal', json={'child': 'jemma', 'goal': {'目标ID': 'jj1', '目标名称': 'ZZJMARKZZ'}})
    _ac.post('/api/goals/goal', json={'child': 'diya', 'goal': {'目标ID': 'dd1', '目标名称': 'ZZDMARKZZ'}})
    A.httpx.Client = _spy_client('{"reply":"ok","operations":[]}')
    _ac.post('/api/goals/ai', json={'child': 'jemma', 'message': 'check my goals'})
    _pj = str(_seen.get('messages', ''))
    check('ZZJMARKZZ' in _pj and 'ZZDMARKZZ' not in _pj,
          'AI 端点只看到该孩子的目标（不把对方的发给模型）')
finally:
    A._load_config = _cfg2
    A.httpx.Client = _hx2


print('== AI 代打卡上限与归档（置于隔离计数断言之后，避免污染计数）==')
_cap_goal = '每日复习 20 个单词'
try:
    A._load_config = lambda: {'api_key': 'k', 'base_url': 'https://example.invalid/v1',
                               'model': 'm', 'temperature': 0.2}
    A.httpx.Client = _make_client(
        '{"reply":"Logged 50.","operations":['
        '{"op":"create_checkin","target":"' + _cap_goal + '","check":{"本次完成量":50}}]}')
    r = ac2.post('/api/goals/ai', json={'message': '今天做了 50'})
    d = r.get_json() or {}
    _ck = [a for a in (d.get('applied') or []) if a.get('op') == 'create_checkin']
    check(bool(_ck) and str(_ck[0].get('amount')) in ('20', '20.0'), 'AI 代打卡被每日上限截断（50→20）')
    check(bool(_ck) and _ck[0].get('by_ai') is True, 'AI 代打卡带 by_ai 标记（前端可见）')
    check('capped' in str(d.get('reply') or ''), 'AI 代打卡截断时 reply 如实说明')
    _ckrows = (ac2.get('/api/goals').get_json() or {}).get('checks') or []
    check(any(c.get('备注') == 'AI helper logged' for c in _ckrows), 'AI 代打卡备注为英文可见标记')
    check(any(c.get('打卡类型') == 'ckNormal' and c.get('当时状态') == 'stSmooth' for c in _ckrows),
          'AI 代打卡选项字段用稳定 id（ckNormal/stSmooth）')
finally:
    A._load_config = _cfg2
    A.httpx.Client = _hx2


print('\n结果:', 'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED -> {FAILS}')
if FAILS:
    import sys as _sys
    print('FAILED_ITEMS=' + ' | '.join(FAILS), file=_sys.stderr)
    _sys.exit(1)
sys.exit(1 if FAILS else 0)
