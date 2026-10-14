# -*- coding: utf-8 -*-
"""上传功能全链路测试（OCR/抽取 mock 掉网络调用，验证存储/去重/状态机/API 契约）"""
import base64, glob, io, json, os, shutil, sys, time

TEST_DIR = '/tmp/uploadtest'
shutil.rmtree(TEST_DIR, ignore_errors=True)
os.makedirs(TEST_DIR, exist_ok=True)
os.environ['DATA_DIR'] = TEST_DIR
os.environ['APP_PASSWORD'] = 't'

# 最小 1x1 PNG
PNG_1PX = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
FAKE_PDF = b'%PDF-1.4\n%%EOF'   # 魔数测试用（无页内容，管线会走 render 分支）

sys.path.insert(0, '.')
import api_server as A

# ── mock 网络调用：OCR 与抽取不真打外部 API ──
A._call_vision_ocr = lambda b64, prompt: {'ok': True, 'text': 'Q1 (a) What is an isotope?\n[2 marks]\nSame protons different neutrons', 'model': 'mock-vl', 'provider': 'main'}
A._extract_upload_fields = lambda text, child: {
    'subject': 'Chemistry', 'doc_type': 'homework', 'title': 'Mock chemistry HW',
    'date': '2026-10-14', 'score': {'got': 7, 'total': 9},
    'topics': ['isotopes'], 'mistakes': [
        {'question_no': '1a', 'wrong_answer': 'same neutrons', 'correct_answer': 'same protons', 'reason': 'confused p/n'}],
    'weakness_summary': 'atomic structure weak', 'model': 'mock'}

c = A.app.test_client()
oks, fails = [], []
def chk(name, cond, extra=''):
    (oks if cond else fails).append(f'{name} {extra}'.strip())

# 登录
c.post('/api/auth/login', json={'password': 't'})

# 1) 非法 child
r = c.post('/api/upload/evil', data={'file': (io.BytesIO(PNG_1PX), 'a.png')})
chk('非法 child 400', r.status_code == 400, f'got {r.status_code}')

# 2) 非法格式（文本文件伪装 .png）
r = c.post('/api/upload/diya', data={'file': (io.BytesIO(b'hello world not an image'), 'a.png')})
chk('伪装图片 400', r.status_code == 400, f'got {r.status_code} {(r.get_json() or {}).get("error","")[:40]}')

# 3) 正常图片上传
r = c.post('/api/upload/diya', data={'file': (io.BytesIO(PNG_1PX), 'test_hw.png')})
chk('图片上传 202', r.status_code == 202, f'got {r.status_code}')
d = r.get_json() or {}
uid = d.get('id', '')
chk('返回 upload_id', bool(uid))

# 4) 等待管线完成（mock OCR 秒回）
for _ in range(40):
    m = (c.get(f'/api/uploads/diya/{uid}').get_json() or {})
    if m.get('status') in ('done', 'failed'):
        break
    time.sleep(0.25)
chk('管线到达 done', m.get('status') == 'done', f'status={m.get("status")} err={m.get("error")}')
chk('页文本已识别', bool((m.get('pages') or [{}])[0].get('clean_text')))
chk('抽取字段存在', (m.get('extract') or {}).get('subject') == 'Chemistry')
chk('full_text 拼接', '--- page 1 ---' in (m.get('full_text') or ''))

# 5) 目录与文件落盘
mdir = A._upload_dir('diya', uid)
chk('目录创建', os.path.isdir(mdir))
chk('原文件存在', os.path.isfile(os.path.join(mdir, 'original.png')))
chk('meta.json 存在', os.path.isfile(os.path.join(mdir, 'meta.json')))

# 6) SHA-256 硬去重
r = c.post('/api/upload/diya', data={'file': (io.BytesIO(PNG_1PX), 'dup.png')})
chk('重复上传 409', r.status_code == 409, f'got {r.status_code}')
chk('409 带 duplicate_of', bool((r.get_json() or {}).get('duplicate_of')))

# 7) jemma 上传同内容（不同 child 不算重复）
r = c.post('/api/upload/jemma', data={'file': (io.BytesIO(PNG_1PX), 'jemma_hw.png')})
chk('跨 child 同内容可上传', r.status_code == 202, f'got {r.status_code}')

# 8) 列表过滤
lst = c.get('/api/uploads/diya').get_json() or []
chk('diya 列表含 1 条', len([e for e in lst if e.get('id') == uid]) == 1, f'len={len(lst)}')
chk('列表含抽取摘要字段', all(k in lst[0] for k in ('subject', 'title', 'status')) if lst else False)

# 9) 文件预览白名单（图片上传后页图即 original.png；PDF 才有 page-NN.png）
r = c.get(f'/api/uploads/diya/{uid}/file/original.png')
chk('原图可预览', r.status_code == 200)
r = c.get(f'/api/uploads/diya/{uid}/file/..%2F..%2F..%2Fetc%2Fpasswd')   # URL 编码穿越
chk('编码路径穿越被挡（404=框架规范化拦截）', r.status_code in (400, 404), f'got {r.status_code}')
r = c.get(f'/api/uploads/diya/{uid}/file/meta.json')
chk('meta.json 不在白名单', r.status_code == 400)

# 10) 非法 uid（单段但不匹配格式）
r = c.get('/api/uploads/diya/not-a-valid-uid')
chk('非法 uid 400', r.status_code == 400, f'got {r.status_code}')

# 11) to-errors 错题联动
before = len(A.safe_load(os.path.join(TEST_DIR, 'errors_diya.json'), default=[]))
r = c.post(f'/api/uploads/diya/{uid}/to-errors')
added = (r.get_json() or {}).get('added', 0)
chk('错题导入 added=1', r.status_code == 200 and added == 1, f'added={added}')
after = json.load(open(os.path.join(TEST_DIR, 'errors_diya.json'), encoding='utf-8'))
chk('errors 文件 +1', len(after) == before + 1)
chk('错题字段结构', all(k in after[-1] for k in ('id', 'subject', 'question', 'diya_answer', 'correct_solution')))
# 幂等：重复导入不重复
r = c.post(f'/api/uploads/diya/{uid}/to-errors')
chk('重复导入幂等', (r.get_json() or {}).get('added') == 0)

# 12) PUT 手工修正
r = c.put(f'/api/uploads/diya/{uid}', json={'subject': 'Physics', 'title': 'corrected'})
chk('手工修正 200', r.status_code == 200)
m2 = c.get(f'/api/uploads/diya/{uid}').get_json() or {}
chk('修正生效', (m2.get('extract') or {}).get('subject') == 'Physics' and m2.get('extract_status') == 'manual')

# 13) 删除
r = c.delete(f'/api/uploads/diya/{uid}')
chk('删除 200', r.status_code == 200)
chk('目录已删', not os.path.isdir(mdir))
chk('索引已删', not [e for e in (c.get('/api/uploads/diya').get_json() or []) if e.get('id') == uid])

# 14) PDF 魔数接受（管线会因无页/渲染失败标 failed，但上传本身 202）
r = c.post('/api/upload/diya', data={'file': (io.BytesIO(FAKE_PDF), 'fake.pdf')})
chk('PDF 魔数上传 202', r.status_code == 202, f'got {r.status_code} {(r.get_json() or {}).get("error","")[:50]}')

# 15) 鉴权拦截（干净客户端）
c2 = A.app.test_client()
r = c2.post('/api/upload/diya', data={'file': (io.BytesIO(PNG_1PX), 'x.png')})
chk('未登录上传被拦', r.status_code == 401, f'got {r.status_code}')
r = c2.get('/api/uploads/diya')
chk('未登录列表被拦', r.status_code == 401)

# 16) 线程池关闭（避免测试进程挂起）
A._upload_pool.shutdown(wait=False)

print('\n===== 上传功能全链路测试 =====')
for o in oks: print('  ✅', o)
for f in fails: print('  ❌', f)
print(f'\n通过 {len(oks)} / 失败 {len(fails)}')
print('RESULT:', 'ALL PASS ✅' if not fails else 'FAILED ❌')
shutil.rmtree(TEST_DIR, ignore_errors=True)
sys.exit(0 if not fails else 1)
