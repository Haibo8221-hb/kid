#!/usr/bin/env python3
"""pipeline/common.py — 刷新流水线共享工具（2026-11-11）
- 环境加载（/etc/plan-api.env）
- 百炼 MCP 搜索 / LLM 供应商链（复用 research_weekly.py 已验证模式）
- run_logged：统一日志+失败重试（60s/300s 退避，第3次失败写 alert）
- 原子写 JSON + chown www:www（plan-api 以 www 运行）
"""
import os, sys, json, time, hashlib, tempfile, traceback
from datetime import datetime

KID_ROOT = '/www/wwwroot/kid'
DATA_DIR = os.path.join(KID_ROOT, 'data')
REFRESH_DIR = os.path.join(DATA_DIR, 'refresh')
LOG_DIR = '/var/log/kidrefresh'
ALERT_FILE = os.path.join(REFRESH_DIR, 'alerts.jsonl')

os.makedirs(REFRESH_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


def load_env():
    try:
        for line in open('/etc/plan-api.env'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


load_env()


def now_iso():
    return datetime.now().isoformat(timespec='seconds')


def log(msg):
    print(f'[{now_iso()}] {msg}', flush=True)


def atomic_write_json(path, obj, indent=1):
    """原子写 + chown www:www（与 plan-api 运行用户一致，避免权限漂移）。"""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
        try:
            import pwd, grp
            os.chown(path, pwd.getpwnam('www').pw_uid, grp.getgrnam('www').gr_gid)
        except Exception:
            pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')
    try:
        import pwd, grp
        os.chown(path, pwd.getpwnam('www').pw_uid, grp.getgrnam('www').gr_gid)
    except Exception:
        pass


def read_json(path, default=None):
    try:
        return json.load(open(path))
    except Exception:
        return default


def fingerprint(text):
    return hashlib.sha256((text or '').strip().encode('utf-8', 'ignore')).hexdigest()[:16]


def write_alert(level, source, msg):
    append_jsonl(ALERT_FILE, {'ts': now_iso(), 'level': level, 'source': source, 'msg': str(msg)[:300]})


# ── 搜索 & LLM（复用 research_weekly.py 生产验证过的实现） ──
import httpx

_MCP_ENV = 'QWEN' + '_MCP' + '_KEY'
QWEN_MCP_KEY = os.environ.get(_MCP_ENV, '')
MCP_URL = 'https://dashscope.aliyuncs.com/api/v1/mcps/EnhancedSearch/mcp'


def mcp_search(query, timeout=30.0, limit=3500):
    query = (query or '').strip()[:200]
    if not query or not QWEN_MCP_KEY:
        return ''
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(MCP_URL, headers={
                'Authorization': f'Bearer {QWEN_MCP_KEY}',
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
            }, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 'search_pro', 'arguments': {'query': query}}})
        if r.status_code != 200:
            return ''
        txt = r.text
        if 'data:' in txt[:40]:
            for line in txt.split('\n'):
                if line.startswith('data:'):
                    txt = line[5:].strip()
                    break
        d = json.loads(txt)
        blocks = d.get('result', {}).get('content', [])
        text = ' '.join(b.get('text', '') for b in blocks if isinstance(b, dict))
        return text[:limit]
    except Exception as e:
        log(f'[mcp_search] failed: {str(e)[:80]}')
        return ''


def load_llm_providers():
    provs = []
    try:
        cfg = json.load(open(os.path.join(DATA_DIR, 'llm_config.json')))
        if cfg.get('api_key'):
            provs.append({'base_url': cfg.get('endpoint') or cfg.get('base_url'),
                          'api_key': cfg['api_key'], 'model': cfg.get('model'), 'label': 'primary'})
    except Exception:
        pass
    try:
        fb = json.load(open(os.path.join(DATA_DIR, 'llm_fallback.json')))
        for p in (fb if isinstance(fb, list) else fb.get('providers', [])):
            if isinstance(p, dict) and p.get('api_key') and (p.get('base_url') or p.get('endpoint')) and p.get('model'):
                provs.append({'base_url': p.get('base_url') or p.get('endpoint'), 'api_key': p['api_key'],
                              'model': p['model'], 'label': p.get('label', 'fallback')})
    except Exception:
        pass
    return provs


def _thinking_off_payload(base_url):
    """按供应商关闭思考（GLM5.x/qwen3.x 思考会烧光 max_tokens 导致正文空——2026-11-03 教训）。"""
    b = (base_url or '').lower()
    if 'bigmodel' in b or 'zhipu' in b or 'z.ai' in b:
        return {'thinking': {'type': 'disabled'}}
    if 'qianwenaiapi' in b or 'dashscope' in b or 'qwen' in b:
        return {'enable_thinking': False}
    return {}


def llm_complete(messages, timeout=150.0, max_tokens=4000, temperature=0.4, disable_thinking=True):
    """供应商链非流式补全。思考关闭失败(400)时自动去掉参数重试一次。返回文本或 None。"""
    for p in load_llm_providers():
        payload = {'model': p['model'], 'messages': messages,
                   'max_tokens': max_tokens, 'temperature': temperature}
        extra = _thinking_off_payload(p['base_url']) if disable_thinking else {}
        for attempt in (1, 2):
            body = dict(payload, **extra) if attempt == 1 else dict(payload)
            try:
                with httpx.Client(timeout=timeout) as c:
                    r = c.post(f"{p['base_url'].rstrip('/')}/chat/completions",
                               headers={'Authorization': f"Bearer {p['api_key']}", 'Content-Type': 'application/json'},
                               json=body)
                if r.status_code == 400 and attempt == 1 and extra:
                    continue  # 不支持 thinking 参数 → 去掉重试
                if r.status_code == 200:
                    content = (r.json().get('choices') or [{}])[0].get('message', {}).get('content', '') or ''
                    if content.strip():
                        return content
                break
            except Exception:
                break
    return None


def llm_json(system, user, **kw):
    """LLM JSON 模式：要求纯 JSON 输出并做容错解析。失败返回 None（调用方必须有非 LLM 兜底）。"""
    raw = llm_complete([{'role': 'system', 'content': system},
                        {'role': 'user', 'content': user}], **kw)
    if not raw:
        return None
    txt = raw.strip()
    # 剥 markdown 围栏 / thinking 残留
    if txt.startswith('```'):
        txt = txt.split('```')[1] if '```' in txt[3:] else txt
        if txt.startswith('json'):
            txt = txt[4:]
        txt = txt.strip()
    s, e = txt.find('{'), txt.rfind('}')
    if s == -1:
        s, e = txt.find('['), txt.rfind(']')
    if s == -1:
        return None
    try:
        return json.loads(txt[s:e + 1])
    except Exception:
        return None


# ── 统一运行包装：日志 + 重试 + 告警 ──
def run_logged(name, fn, retries=2, backoff=(60, 300)):
    """失败重试 retries 次（退避），最终失败写 alerts.jsonl 并以非零码退出。"""
    attempt = 0
    while True:
        try:
            log(f'[{name}] start (attempt {attempt + 1})')
            result = fn()
            log(f'[{name}] done')
            state = read_json(os.path.join(REFRESH_DIR, 'collect_state.json'), {}) or {}
            state[name] = {**(state.get(name) or {}), 'last_success': now_iso(), 'consecutive_fail': 0}
            atomic_write_json(os.path.join(REFRESH_DIR, 'collect_state.json'), state)
            return result
        except Exception as e:
            attempt += 1
            log(f'[{name}] attempt {attempt} FAILED: {e}')
            traceback.print_exc()
            state = read_json(os.path.join(REFRESH_DIR, 'collect_state.json'), {}) or {}
            cf = (state.get(name) or {}).get('consecutive_fail', 0) + 1
            state[name] = {**(state.get(name) or {}), 'last_fail': now_iso(),
                           'last_error': str(e)[:200], 'consecutive_fail': cf}
            atomic_write_json(os.path.join(REFRESH_DIR, 'collect_state.json'), state)
            if attempt > retries:
                write_alert('red', name, f'failed {attempt}x consecutively: {str(e)[:150]}')
                log(f'[{name}] giving up, alert written (consecutive_fail={cf})')
                sys.exit(1)
            delay = backoff[min(attempt - 1, len(backoff) - 1)]
            log(f'[{name}] retry in {delay}s')
            time.sleep(delay)
