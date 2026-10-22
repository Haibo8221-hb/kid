#!/usr/bin/env python3
"""Flask API for planner data storage + AI chat + wiki logs"""
import base64, hashlib, hmac, json, logging, os, re, sys, time, uuid
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response, stream_with_context
import httpx

log = logging.getLogger('kid-api')

app = Flask(__name__)
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(__file__), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
# P1-4: 聊天上下文窗口上限，避免历史无限增长导致 token 超限
CHAT_MAX_HISTORY = int(os.environ.get('CHAT_MAX_HISTORY', '20'))

# ── 应用层鉴权（P0：登录态从 nginx basic auth 上移到应用层）──────────────
# 设计：
#   - 无状态 HMAC token（exp + sig），gunicorn 多 worker / 重启均有效
#   - 密码来自环境变量 APP_PASSWORD（未设置时用默认值，生产务必修改）
#   - token 存于 httpOnly cookie（SameSite=Lax），前端 JS 不可读，防 XSS 窃取
#   - 修改 APP_PASSWORD 会使所有已发 token 立即失效（secret 派生自密码）
AUTH_COOKIE = 'kid_auth'
AUTH_TTL = 30 * 24 * 3600            # token 有效期 30 天
AUTH_RENEW = 15 * 24 * 3600          # 剩余不足 15 天时滚动续期

# ── 密码来源（P0-3）───────────────────────────────────────────────────
# 优先级：APP_PASSWORD 环境变量 → APP_PASSWORD_FILE 文件 → 内置默认（仅兼容存量）
# APP_STRICT_PASSWORD=1：未显式配置密码时拒绝所有登录（生产环境必须开启）
# 未开严格模式时仍回退默认值，但启动即打 CRITICAL 告警，便于灰度切换、避免锁死部署
_DEFAULT_PASSWORD = 'haibo2026'
APP_STRICT_PASSWORD = os.environ.get('APP_STRICT_PASSWORD', '0') == '1'

def _resolve_password():
    p = os.environ.get('APP_PASSWORD', '').strip()
    if p:
        return p
    f = os.environ.get('APP_PASSWORD_FILE', '').strip()
    if f and os.path.exists(f):
        try:
            with open(f) as fh:
                return fh.read().strip()
        except Exception as e:
            log.error('APP_PASSWORD_FILE 读取失败: %s', e)
    return ''

APP_PASSWORD = _resolve_password() or _DEFAULT_PASSWORD
PASSWORD_CONFIGURED = bool(_resolve_password())
if not PASSWORD_CONFIGURED:
    log.critical('SECURITY: APP_PASSWORD 未配置，当前回退到内置默认密码（必须尽快修改）。'
                 '严格模式=%s；生产请设置 APP_PASSWORD 并置 APP_STRICT_PASSWORD=1。',
                 APP_STRICT_PASSWORD)

# ── Cookie 安全属性 ───────────────────────────────────────────────────
# 默认 Secure（站点经 https 提供）；本地 http 调试时设 COOKIE_SECURE=0
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', '1') != '0'

def _cookie_kwargs(extra=None):
    kw = dict(httponly=True, samesite='Lax', path='/', secure=COOKIE_SECURE)
    if extra:
        kw.update(extra)
    return kw

def _set_auth_cookie(resp, tok, max_age=AUTH_TTL):
    resp.set_cookie(AUTH_COOKIE, tok, max_age=max_age, **_cookie_kwargs())

def _auth_secret():
    """签名密钥：显式 APP_SECRET 优先；否则从密码派生（改密码=吊销全部会话）"""
    s = os.environ.get('APP_SECRET', '')
    if s:
        return s.encode()
    return hmac.new(b'kid-main-auth-v1', APP_PASSWORD.encode(), hashlib.sha256).digest()

AUTH_STATE_FILE = os.path.join(DATA_DIR, 'auth_state.json')

def _logout_epoch():
    """全局登出水位线：登出时刻之前签发的所有 token 一律失效。"""
    try:
        with open(AUTH_STATE_FILE) as f:
            return float(json.load(f).get('logout_at', 0) or 0)
    except Exception:
        return 0.0

def _mark_logout(now=None):
    now = float(now or time.time())
    try:
        atomic_write(AUTH_STATE_FILE, {'logout_at': now}, ensure_ascii=False)
    except Exception as e:
        log.error('写入登出水位线失败: %s', e)
    return now

def _make_token(now=None):
    """token = exp.iat.sig —— iat 为毫秒级浮点签发时间，用于登出后精确吊销。"""
    now = float(now or time.time())
    exp = int(now + AUTH_TTL)
    iat_s = str(int(now * 1000))      # 毫秒整型，避免 token 中出现额外小数点
    sig = hmac.new(_auth_secret(), f'{exp}.{iat_s}'.encode(), hashlib.sha256).hexdigest()[:32]
    return f'{exp}.{iat_s}.{sig}'

def _verify_token(tok):
    """校验 token；过期/签名不符/早于登出水位线均拒绝（恒定时间比较）。"""
    if not tok or tok.count('.') < 2:
        return False
    try:
        exp_s, iat_s, sig = tok.split('.', 2)
        exp, iat = int(exp_s), int(iat_s) / 1000.0
    except ValueError:
        return False
    good = hmac.new(_auth_secret(), f'{exp}.{iat_s}'.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, good):
        return False
    if iat < _logout_epoch():     # 登出时刻之前签发的 token 全部失效
        return False
    return exp > time.time()

def _auth_token_from_request():
    # 优先 cookie；兼容 Authorization: Bearer <token>（非浏览器/测试场景）
    tok = request.cookies.get(AUTH_COOKIE)
    if tok:
        return tok
    h = request.headers.get('Authorization', '')
    if h.startswith('Bearer '):
        return h[7:].strip()
    return None

def _is_public_api_path():
    """无需登录即可访问的 API：登录/登出/状态检查 + 全部 CORS 预检"""
    p = request.path
    if request.method == 'OPTIONS':
        return True
    return p in ('/api/auth/login', '/api/auth/logout', '/api/auth/status')

@app.before_request
def _auth_gate():
    """统一入口拦截：所有 /api/* 需要有效会话（静态页面由前端 401 跳转兜底）"""
    if not request.path.startswith('/api/'):
        return None
    if _is_public_api_path():
        return None
    if _verify_token(_auth_token_from_request()):
        return None
    resp = jsonify({'error': 'unauthorized', 'hint': '请先登录'})
    _set_cors(resp)
    return resp, 401

# ── 登录限流（按来源 IP 计数，进程内内存；重启即清零，够用且无外部依赖）──
LOGIN_FAIL_LIMIT = 5            # 连续失败达到此次数后锁定
LOGIN_LOCKOUT_SEC = 300         # 锁定时长（秒）
_login_fail_state = {}          # ip -> [失败次数, 首次失败时间, 锁定截止时间]


def _login_rate_check(ip):
    """返回 (是否允许继续尝试, 还需等待秒数)。"""
    st = _login_fail_state.get(ip)
    now = time.time()
    if st and st[2] and now < st[2]:
        return False, int(st[2] - now) + 1
    if st and st[2] and now >= st[2]:
        _login_fail_state.pop(ip, None)     # 锁定到期，重置
    return True, 0


def _login_rate_note_failure(ip):
    now = time.time()
    st = _login_fail_state.get(ip) or [0, now, 0]
    st[0] += 1
    if now - st[1] > 600:                   # 距离首次失败超过 10 分钟，重新开始计数
        st = [1, now, 0]
    if st[0] >= LOGIN_FAIL_LIMIT:
        st[2] = now + LOGIN_LOCKOUT_SEC
    _login_fail_state[ip] = st


def _login_rate_clear(ip):
    _login_fail_state.pop(ip, None)


@app.route('/api/auth/login', methods=['POST', 'OPTIONS'])
def auth_login():
    """校验密码，签发会话 cookie。错误响应不区分「密码错」与「用户不存在」（单用户场景只有前者）。"""
    if request.method == 'OPTIONS':
        return _cors()
    data = request.get_json(force=True, silent=True) or {}
    pwd = str(data.get('password') or '')
    # 严格模式下未配置密码 → 一律拒绝登录（避免内置默认密码被利用）
    if APP_STRICT_PASSWORD and not PASSWORD_CONFIGURED:
        resp = jsonify({'ok': False, 'error': '服务端未配置 APP_PASSWORD，请联系管理员'})
        _set_cors(resp)
        return resp, 503
    # 限流：同一来源连续失败达到阈值后锁定一段时间（防止在线爆破）
    ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
          or request.headers.get('X-Real-IP', '').strip()
          or (request.remote_addr or 'unknown'))
    allowed, wait = _login_rate_check(ip)
    if not allowed:
        resp = jsonify({'ok': False, 'error': f'尝试过于频繁，请 {wait} 秒后重试',
                        'retry_after': wait})
        _set_cors(resp)
        return resp, 429
    if not hmac.compare_digest(pwd.encode(), APP_PASSWORD.encode()):
        time.sleep(0.4)  # 恒定延迟，抬高暴力破解成本
        _login_rate_note_failure(ip)
        resp = jsonify({'ok': False, 'error': '密码错误'})
        _set_cors(resp)
        return resp, 401
    _login_rate_clear(ip)
    tok = _make_token()
    resp = jsonify({'ok': True, 'expires_in': AUTH_TTL})
    _set_auth_cookie(resp, tok)
    _set_cors(resp)
    return resp

@app.route('/api/auth/logout', methods=['POST', 'OPTIONS'])
def auth_logout():
    """清除会话 cookie + 抬高登出水位线，使此前签发的所有 token 立即失效。"""
    if request.method == 'OPTIONS':
        return _cors()
    _mark_logout()          # 服务端吊销：旧 cookie 即使被复制也立即失效
    resp = jsonify({'ok': True})
    resp.set_cookie(AUTH_COOKIE, '', max_age=0, **_cookie_kwargs())
    _set_cors(resp)
    return resp

@app.route('/api/auth/status', methods=['GET', 'OPTIONS'])
def auth_status():
    """前端页面加载时探活；会话剩余不足 15 天时滚动续期。"""
    if request.method == 'OPTIONS':
        return _cors()
    tok = _auth_token_from_request()
    if not _verify_token(tok):
        resp = jsonify({'authenticated': False})
        _set_cors(resp)
        return resp, 401
    resp = jsonify({'authenticated': True})
    # 滑动续期：登录态活跃用户可长期免密
    try:
        exp = int(tok.split('.', 1)[0])
        if exp - time.time() < AUTH_RENEW:
            _set_auth_cookie(resp, _make_token())
    except (ValueError, AttributeError):
        pass
    _set_cors(resp)
    return resp


# ── 安全 / 健壮性 helper（P0 改造：CORS 收紧、原子写、防静默清空） ──
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', 'https://grow-consultant.com').split(',') if o.strip()]

def _set_cors(resp):
    origin = request.headers.get('Origin', '')
    resp.headers['Access-Control-Allow-Origin'] = origin if origin in ALLOWED_ORIGINS else (ALLOWED_ORIGINS[0] if ALLOWED_ORIGINS else '*')
    resp.headers['Access-Control-Allow-Credentials'] = 'true'

def _cors():
    """OPTIONS 预检响应（作业 PUT/DELETE 等调用，原 _cors() 未定义导致 500）。"""
    resp = jsonify({})
    _set_cors(resp)
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return resp

def atomic_write(path, obj, **kw):
    kw.setdefault('ensure_ascii', False)
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, **kw)
    # Windows 下目标文件可能被杀毒/索引短暂占用导致 os.replace 报 WinError 5；
    # 短退避重试 5 次（总计约 1.5s），Linux 语义不变（replace 原子且不失败）。
    last_err = None
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    raise last_err

def _sse(payload):
    """序列化为 SSE data 行，对内容做 JSON 转义（P1-2 修复未转义导致前端解析失败）。"""
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'

def safe_load(path, default=None):
    if default is None:
        default = []
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        corrupt = path + '.corrupt'
        try:
            if os.path.exists(path):
                os.replace(path, corrupt)
        except Exception:
            pass
        app.logger.error('load failed %s: %s; kept corrupt copy at %s', path, e, corrupt)
        return default

# In-memory JSON read cache (mtime + TTL) — reduces repeat disk IO on hot read paths
_json_cache = {}  # path -> (data, cached_at, mtime)
def _cached_json_read(path, ttl=15, default=None):
    """Read JSON with a short-lived, mtime-aware cache. Falls back to default on missing/corrupt."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return default
    cached = _json_cache.get(path)
    now = time.time()
    if cached and cached[2] == mtime and now - cached[1] < ttl:
        return cached[0]
    data = safe_load(path, default)
    _json_cache[path] = (data, now, mtime)
    return data

# ── child / user 参数白名单（P0-2）────────────────────────────────────
# 凡是用于拼接 data/ 文件名的用户输入（child、user），必须先过 _safe_child，
# 否则 `../../xxx` 可路径穿越写出/覆盖 data/ 目录任意文件。
ALLOWED_CHILDREN = ('diya', 'jemma')

def _safe_child(value, default='diya'):
    """只放行白名单内的 child/user；非法值一律回退默认值，绝不拼进文件路径。"""
    v = str(value or '').strip().lower()
    return v if v in ALLOWED_CHILDREN else default

# ── Planner (cloud sync) ──────────────────────────────────────────
@app.route('/api/planner', methods=['GET', 'POST', 'OPTIONS'])
def planner_api():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    raw_user = request.args.get('user', 'diya') if request.method == 'GET' else (request.get_json(force=True, silent=True) or {}).get('user', 'diya')
    user = _safe_child(raw_user)
    fpath = os.path.join(DATA_DIR, f'planner_{user}.json')
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        plan = data.get('plan', {})
        atomic_write(fpath, plan, ensure_ascii=False)
        resp = jsonify({'ok': True})
    else:
        if os.path.exists(fpath):
            with open(fpath) as f:
                plan = json.load(f)
        else:
            plan = {}
        resp = jsonify(plan)
    _set_cors(resp)
    return resp

# ── Tasks (cloud sync) ─────────────────────────────────────────────
@app.route('/api/tasks', methods=['GET', 'POST', 'OPTIONS'])
def tasks_api():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    data = request.get_json(force=True, silent=True) or {}
    user = _safe_child(data.get('user', request.args.get('user', 'diya')))
    fpath = os.path.join(DATA_DIR, f'tasks_{user}.json')
    if request.method == 'POST':
        atomic_write(fpath, data.get('tasks', {}), ensure_ascii=False)
        _json_cache.pop(fpath, None)   # R2：写后失效缓存，dashboard 聚合立即读到新值
        resp = jsonify({'ok': True})
    else:
        if os.path.exists(fpath):
            with open(fpath) as f:
                resp = jsonify(json.load(f))
        else:
            resp = jsonify({})
    _set_cors(resp)
    return resp

# ── Chat / LLM ──────────────────────────────────────────────────────
@app.route('/api/chat/<child>', methods=['GET', 'POST', 'DELETE', 'OPTIONS'])
def chat_api(child):
    return _chat_handler(child)

@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat_api_plain():
    return _chat_handler(None)

def _chat_handler(child):
    """Shared handler for /api/chat and /api/chat/<child>"""
    child = _safe_child(child or 'diya')
    conv_path = os.path.join(DATA_DIR, f'chat_{child}.json')
    os.makedirs(os.path.dirname(conv_path), exist_ok=True)

    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    if request.method == 'GET':
        conv = safe_load(conv_path, [])
        limit = request.args.get('limit', type=int)
        if limit:
            conv = conv[-limit:]
        return jsonify({'messages': conv})

    if request.method == 'DELETE':
        if os.path.exists(conv_path):
            os.remove(conv_path)
        return jsonify({'ok': True})

    # POST — send message + get LLM response
    data = request.get_json(force=True, silent=True) or {}
    user_msg = data.get('message', '')
    if not user_msg:
        return jsonify({'error': 'empty message'}), 400

    if os.path.exists(conv_path):
        with open(conv_path) as f:
            conv = json.load(f)
    else:
        conv = []

    plan_info = _load_plan_context_cached(child)
    conv.append({'role': 'user', 'content': user_msg, 'time': datetime.now().isoformat()})
    ai_resp = _call_llm(conv, child, plan_info)
    conv.append({'role': 'assistant', 'content': ai_resp, 'time': datetime.now().isoformat()})

    atomic_write(conv_path, conv, ensure_ascii=False, indent=2)

    # Auto-sync latest exchange to MD
    try:
        _sync_chat_to_md(child, conv)
    except Exception as e:
        print(f'[sync] MD sync failed for {child}: {e}')

    return jsonify({'reply': ai_resp})


def _load_plan_context(child):
    """Load full planning context for the child — plan MD + research intel."""
    parts = []

    # Plan base path: configurable via env (P1-3). 默认 <DATA_DIR>/plan_context
    plan_base = os.environ.get('PLAN_MD_PATH', os.path.join(DATA_DIR, 'plan_context'))

    # Child's plan MD (full content, no truncation)
    plan_file = os.path.join(plan_base, f'{child.capitalize()}_Plan.md')
    if os.path.exists(plan_file):
        with open(plan_file, 'r', encoding='utf-8') as f:
            parts.append(f'## {child.capitalize()} 升学规划\n\n{f.read()}')
    else:
        # Docker fallback: look in /app
        alt_file = os.path.join('/app', f'{child.capitalize()}_Plan.md')
        if os.path.exists(alt_file):
            with open(alt_file, 'r', encoding='utf-8') as f:
                parts.append(f'## {child.capitalize()} 升学规划\n\n{f.read()}')

    # Research intel (Tavily search results) — env 可覆盖 (P1-3)
    intel_path = os.environ.get('RESEARCH_INTEL_PATH', os.path.join(plan_base, 'Research_Intel.md'))
    if not os.path.exists(intel_path):
        intel_path = '/app/Research_Intel.md'
    if os.path.exists(intel_path):
        with open(intel_path, 'r', encoding='utf-8') as f:
            parts.append(f'\n\n## 外部升学情报\n\n{f.read()}')

    # Nearest school report
    import glob as _glob
    hermes_home = os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes'))
    reports = sorted(_glob.glob(os.path.join(hermes_home, 'cache/documents', f'*{child.capitalize()}*')))[::-1]
    for rp in reports[:1]:  # latest only
        try:
            import pdfplumber
            with pdfplumber.open(rp) as pdf:
                text = '\n'.join(p.extract_text() or '' for p in pdf.pages)
                if text.strip():
                    parts.append(f'\n\n## 最新成绩单\n\n{text[:5000]}')
        except Exception:
            pass

    return '\n\n---\n\n'.join(parts) if parts else ''


# ── LLM 配置（UI 可配 + 持久化到 DATA_DIR/llm_config.json，见 /api/settings/llm）──
LLM_CONFIG_FILE = os.path.join(DATA_DIR, 'llm_config.json')
# 向后兼容：旧 Hermes config.yaml 仍可作为兜底
_HERMES_CFG = os.path.join(os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes')), 'config.yaml')

def _normalize_endpoint(url):
    """规整 OpenAI 兼容端点：去除末尾斜杠与 /chat/completions 等后缀。"""
    if not url:
        return ''
    url = url.strip()
    for suffix in ('/chat/completions', '/chat', '/v1/chat/completions'):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip('/')

def _load_llm_config():
    """加载 LLM 配置。优先级：持久化文件 > 进程环境变量 > 旧 Hermes config.yaml > 内置默认。
    返回 {api_key, base_url, model, temperature, openrouter_key}。"""
    cfg = {
        'api_key': '',
        'base_url': 'https://api.deepseek.com',
        'model': 'deepseek-chat',
        'temperature': 0.7,
        'openrouter_key': '',
    }

    # 1) 旧 Hermes config.yaml 兜底（deepseek provider）
    try:
        import yaml
        if os.path.exists(_HERMES_CFG):
            hc = yaml.safe_load(open(_HERMES_CFG)) or {}
            prov = hc.get('providers', {}).get(hc.get('model', {}).get('provider', 'deepseek'), {})
            if prov.get('api_key') and '...' not in prov.get('api_key', ''):
                cfg['api_key'] = prov['api_key']
            if prov.get('base_url'):
                cfg['base_url'] = prov['base_url']
            if prov.get('default_model'):
                cfg['model'] = prov['default_model']
            or_key = hc.get('providers', {}).get('openrouter', {}).get('api_key', '')
            if or_key and '...' not in or_key:
                cfg['openrouter_key'] = or_key
    except Exception:
        pass

    # 2) 进程环境变量覆盖（Docker / 宝塔 environment= 段）
    env_key = os.environ.get('LLM_API_KEY') or os.environ.get('OPENAI_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')
    if env_key:
        cfg['api_key'] = env_key
    if os.environ.get('LLM_BASE_URL'):
        cfg['base_url'] = os.environ['LLM_BASE_URL']
    if os.environ.get('LLM_MODEL'):
        cfg['model'] = os.environ['LLM_MODEL']
    if os.environ.get('LLM_TEMPERATURE'):
        try:
            cfg['temperature'] = float(os.environ['LLM_TEMPERATURE'])
        except Exception:
            pass
    if os.environ.get('OPENROUTER_API_KEY'):
        cfg['openrouter_key'] = os.environ['OPENROUTER_API_KEY']

    # 3) 持久化文件（UI 保存，最高优先级）
    if os.path.exists(LLM_CONFIG_FILE):
        try:
            f = json.load(open(LLM_CONFIG_FILE, encoding='utf-8')) or {}
            if f.get('api_key'):
                cfg['api_key'] = f['api_key']
            if f.get('endpoint') or f.get('base_url'):
                cfg['base_url'] = f.get('endpoint') or f.get('base_url')
            if f.get('model'):
                cfg['model'] = f['model']
            if f.get('temperature') is not None:
                try:
                    cfg['temperature'] = float(f['temperature'])
                except Exception:
                    pass
            if f.get('openrouter_key'):
                cfg['openrouter_key'] = f['openrouter_key']
        except Exception:
            pass

    cfg['base_url'] = _normalize_endpoint(cfg['base_url']) or 'https://api.deepseek.com'
    return cfg

def _load_config():
    """兼容旧调用方：返回 {api_key, base_url, model, temperature}（取自 _load_llm_config）。"""
    c = _load_llm_config()
    return {'api_key': c['api_key'], 'base_url': c['base_url'], 'model': c['model'], 'temperature': c['temperature']}

def _call_llm(conv, child, plan_info):
    """Call DeepSeek V4 Pro LLM with full conversation history + full plan context."""
    system_prompt = _build_system_prompt(child, plan_info)

    # Full conversation history
    # 限窗：仅保留最近 CHAT_MAX_HISTORY 条，避免 token 无限增长 (P1-4)
    recent = conv[-CHAT_MAX_HISTORY:] if len(conv) > CHAT_MAX_HISTORY else conv
    messages = [{'role': 'system', 'content': system_prompt}]
    for msg in recent:
        content = _strip(msg.get('content', ''))
        messages.append({'role': msg['role'], 'content': content})

    # Load config
    cfg = _load_config()
    api_key = cfg['api_key']
    base_url = cfg['base_url']
    model = cfg['model']
    temperature = cfg.get('temperature', 0.7)

    if not api_key:
        return '[配置错误] 未配置 LLM API Key。请在侧边栏「⚙️ LLM 设置」中填写 OpenAI 兼容端点、API Key 与模型。'

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f'{base_url}/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': model,
                    'messages': messages,
                    'max_tokens': 3000,
                    'temperature': temperature
                }
            )
            if resp.status_code == 200:
                result = resp.json()
                content = result['choices'][0]['message']['content']
                return content if content else '[模型返回空内容]'
            else:
                return f'[LLM错误 {resp.status_code}]: {resp.text[:100]}'
    except Exception as e:
        return f'[网络错误]: {str(e)[:100]}'


def _build_system_prompt(child, plan_info):
    # Try loading from external prompt file first
    prompt_file = os.path.join(os.path.dirname(__file__), 'chat_prompt.md')
    if os.path.exists(prompt_file):
        with open(prompt_file, 'r', encoding='utf-8') as f:
            template = f.read()
        return template.replace('{child}', child.capitalize()).replace('{plan_info}', plan_info)
    # Fallback hardcoded prompt
    return f"""你是海波两个女儿（Diya和Jemma）的AI升学顾问。Diya在Headington School Oxford读Year 10，Jemma读Year 8。你现在在与{child.capitalize()}对话。

以下是完整的规划背景和外部情报，请据此回答问题：

{plan_info}

对话规则：
1. 基于以上规划文档和情报回答，提供有依据的建议
2. 回复简洁、用中文
3. 帮助规划学习、解决学业问题、解读周计划、竞赛准备、选课建议等
4. 如果是Diya：目标Imperial/Edinburgh/UCL Engineering，重点 Maths+FMaths+Physics（A-level铁三角），Chemistry为高风险第四科，ESAT考试准备
5. 如果是Jemma：Maths/Science/编程启蒙，为Year 9选课和GCSE做准备，培养理科兴趣
6. 回复长度根据问题复杂度灵活调整，问得多答得多
"""


# ── Plan context cache ──────────────────────────────
_plan_context_cache = {}  # {child: (content, timestamp)}

def _load_plan_context_cached(child):
    """Load plan context with 5-minute memory cache."""
    now = time.time()
    entry = _plan_context_cache.get(child)
    if entry and now - entry[1] < 300:
        return entry[0]
    content = _load_plan_context(child)
    _plan_context_cache[child] = (content, now)
    return content


# ── Streaming Chat ──────────────────────────────────
@app.route('/api/chat/stream', methods=['POST', 'OPTIONS'])
def chat_stream():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    data = request.get_json(force=True, silent=True) or {}
    user_msg = data.get('message', '')
    child = _safe_child(data.get('user', 'diya'))
    if not user_msg:
        return jsonify({'error': 'empty message'}), 400

    conv_path = os.path.join(DATA_DIR, f'chat_{child}.json')
    conv = json.load(open(conv_path)) if os.path.exists(conv_path) else []

    plan_info = _load_plan_context_cached(child)
    system_prompt = _build_system_prompt(child, plan_info)

    # 限窗：仅保留最近 CHAT_MAX_HISTORY 条，避免 token 无限增长 (P1-4)
    recent = conv[-CHAT_MAX_HISTORY:] if len(conv) > CHAT_MAX_HISTORY else conv
    messages = [{'role': 'system', 'content': system_prompt}]
    for msg in recent:
        content = _strip(msg.get('content', ''))
        messages.append({'role': msg['role'], 'content': content})
    messages.append({'role': 'user', 'content': user_msg})

    cfg = _load_config()
    if not cfg['api_key']:
        # 503 = 服务未配置（区别于 5xx 内部错误），前端可据此提示去「⚙️ LLM 设置」填写
        return jsonify({'error': '未配置 LLM API Key，请在侧边栏「⚙️ LLM 设置」中填写。',
                        'code': 'llm_not_configured'}), 503

    def generate():
        full_reply = ''
        try:
            with httpx.Client(timeout=90.0) as client:
                with client.stream(
                    'POST',
                    f"{cfg['base_url']}/chat/completions",
                    headers={
                        'Authorization': f"Bearer {cfg['api_key']}",
                        'Content-Type': 'application/json'
                    },
                    json={
                        'model': cfg['model'],
                        'messages': messages,
                        'max_tokens': 3000,
                        'temperature': cfg.get('temperature', 0.7),
                        'stream': True
                    }
                ) as resp:
                    if resp.status_code != 200:
                        yield _sse({'error': f'LLM错误 {resp.status_code}'})
                        return
                    for line in resp.iter_lines():
                        if line.startswith('data: '):
                            chunk = line[6:]
                            if chunk == '[DONE]':
                                break
                            try:
                                import json as _json
                                delta = _json.loads(chunk)
                                content = delta.get('choices', [{}])[0].get('delta', {}).get('content', '')
                                if content:
                                    full_reply += content
                                    yield _sse({'token': content})
                            except Exception:
                                pass
        except Exception as e:
            yield _sse({'error': str(e)[:100]})

        # Save conversation
        conv.append({'role': 'user', 'content': user_msg, 'time': datetime.now().isoformat()})
        conv.append({'role': 'assistant', 'content': full_reply, 'time': datetime.now().isoformat()})
        atomic_write(conv_path, conv, ensure_ascii=False, indent=2)
        try:
            _sync_chat_to_md(child, conv)
        except Exception as e:
            print(f'[sync] MD sync failed: {e}')

        yield _sse({'done': True})

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    _set_cors(resp)
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


# ── LLM Settings (UI 配置；持久化到 DATA_DIR/llm_config.json) ──────────
def _mask_key(k):
    """对 API Key 做脱敏展示：保留前后 4 位，中间用 … 代替。"""
    if not k:
        return ''
    if len(k) <= 8:
        return '****'
    return k[:4] + '…' + k[-4:]

@app.route('/api/settings/llm', methods=['GET', 'POST', 'OPTIONS'])
def llm_settings():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    if request.method == 'GET':
        c = _load_llm_config()
        resp = jsonify({
            'endpoint': c['base_url'],
            'model': c['model'],
            'temperature': c['temperature'],
            'has_key': bool(c['api_key']),
            'key_masked': _mask_key(c['api_key']),
            'has_openrouter': bool(c['openrouter_key']),
            'openrouter_masked': _mask_key(c['openrouter_key']),
        })
        _set_cors(resp)
        return resp

    # POST — 保存配置（原子写入，避免半截文件）
    data = request.get_json(force=True, silent=True) or {}
    endpoint = (data.get('endpoint') or data.get('base_url') or '').strip()
    api_key = (data.get('api_key') or '').strip()
    model = (data.get('model') or '').strip()
    try:
        temperature = float(data.get('temperature', 0.7))
    except Exception:
        temperature = 0.7
    openrouter_key = (data.get('openrouter_key') or '').strip()

    import re as _re
    if not _re.match(r'^https?://', endpoint):
        resp = jsonify({'ok': False, 'error': '端点必须是 http(s):// 开头的 URL'})
        _set_cors(resp)
        return resp, 400

    # 读取已有配置：字段为空时保留原值（允许单独更新 key 而不清空其它项）
    cur = {}
    if os.path.exists(LLM_CONFIG_FILE):
        try:
            cur = json.load(open(LLM_CONFIG_FILE, encoding='utf-8')) or {}
        except Exception:
            cur = {}
    new_cfg = {
        'endpoint': endpoint,
        'api_key': api_key or cur.get('api_key', ''),
        'model': model or cur.get('model', 'deepseek-chat'),
        'temperature': temperature,
        'openrouter_key': openrouter_key or cur.get('openrouter_key', ''),
    }
    atomic_write(LLM_CONFIG_FILE, new_cfg, ensure_ascii=False, indent=2)
    resp = jsonify({'ok': True})
    _set_cors(resp)
    return resp


@app.route('/api/settings/llm/test', methods=['POST', 'OPTIONS'])
def llm_settings_test():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    data = request.get_json(force=True, silent=True) or {}
    # 字段留空时回退到已保存配置：既可直接测现有配置，也能测新填的值
    cur = _load_llm_config()
    endpoint = _normalize_endpoint((data.get('endpoint') or data.get('base_url') or cur['base_url'] or '').strip())
    api_key = (data.get('api_key') or cur['api_key'] or '').strip()
    model = (data.get('model') or cur['model'] or '').strip()
    if not endpoint or not api_key or not model:
        resp = jsonify({'ok': False, 'error': 'endpoint / api_key / model 均必填（留空则使用已保存配置）'})
        _set_cors(resp)
        return resp, 400
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f'{endpoint}/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={'model': model, 'messages': [{'role': 'user', 'content': 'ping'}], 'max_tokens': 5, 'temperature': 0.1}
            )
            if r.status_code == 200:
                resp = jsonify({'ok': True, 'model': model, 'status': r.status_code})
            else:
                resp = jsonify({'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:120]}'})
    except Exception as e:
        resp = jsonify({'ok': False, 'error': str(e)[:160]})
    _set_cors(resp)
    return resp


# ── Wiki / Journal ────────────────────────────────────────────────────
@app.route('/api/wiki', methods=['GET', 'POST', 'OPTIONS'])
@app.route('/api/wiki/<child>', methods=['GET', 'DELETE', 'OPTIONS'])
@app.route('/api/wiki/<child>/<entry_id>', methods=['GET', 'PUT', 'DELETE', 'OPTIONS'])
def wiki_api(child=None, entry_id=None):
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    child = child or 'diya'
    wiki_path = os.path.join(DATA_DIR, f'wiki_{child}.json')

    # Ensure file exists
    if not os.path.exists(wiki_path):
        atomic_write(wiki_path, [])

    with open(wiki_path) as f:
        entries = json.load(f)

    if request.method == 'GET' and entry_id:
        for e in entries:
            if e.get('id') == entry_id:
                return jsonify(e)
        return jsonify({'error': 'not found'}), 404

    if request.method == 'GET':  # list all entries
        return jsonify({'entries': entries})

    if request.method == 'DELETE' and entry_id:
        entries = [e for e in entries if e.get('id') != entry_id]
        atomic_write(wiki_path, entries, ensure_ascii=False, indent=2)
        return jsonify({'ok': True})

    if request.method == 'DELETE':  # delete all for child
        os.remove(wiki_path)
        return jsonify({'ok': True})

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        entry = {
            'id': str(uuid.uuid4())[:8],
            'date': data.get('date', datetime.now().strftime('%Y-%m-%d')),
            'title': data.get('title', ''),
            'content': data.get('content', ''),
            'tags': data.get('tags', []),
            'created': datetime.now().isoformat(),
            'updated': datetime.now().isoformat()
        }
        entries.insert(0, entry)  # newest first
        atomic_write(wiki_path, entries, ensure_ascii=False, indent=2)
        return jsonify(entry)

    if request.method == 'PUT' and entry_id:
        data = request.get_json(force=True, silent=True) or {}
        for e in entries:
            if e.get('id') == entry_id:
                e['title'] = data.get('title', e['title'])
                e['content'] = data.get('content', e['content'])
                e['tags'] = data.get('tags', e.get('tags', []))
                e['updated'] = datetime.now().isoformat()
                atomic_write(wiki_path, entries, ensure_ascii=False, indent=2)
                return jsonify(e)
        return jsonify({'error': 'not found'}), 404

    return jsonify({'error': 'method not allowed'}), 405


# ── Dashboard summary ─────────────────────────────────────────────
@app.route('/api/dashboard', methods=['GET', 'OPTIONS'])
@app.route('/api/dashboard/<child>', methods=['GET', 'OPTIONS'])
def dashboard_api(child=None):
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    child = child or request.args.get('child', 'diya')

    # Load wiki entries (cached)
    wiki_path = os.path.join(DATA_DIR, f'wiki_{child}.json')
    wiki_entries = _cached_json_read(wiki_path, ttl=15) or []

    # Load chat messages (last 6)
    chat_path = os.path.join(DATA_DIR, f'chat_{child}.json')
    messages = _cached_json_read(chat_path, ttl=15) or []
    chat_recent = messages[-6:] if messages else []

    # Load tasks
    # R2（2026-10-19）：twp-* 前缀键 = 三周计划勾选（由三周计划区渲染），与日常任务分开统计，
    # 避免混入环形进度导致分母膨胀；twp 完成度单独输出供前端分列显示。
    tasks_path = os.path.join(DATA_DIR, f'tasks_{child}.json')
    tasks_all = _cached_json_read(tasks_path, ttl=15) or {}
    tasks = {k: v for k, v in tasks_all.items()
             if not (isinstance(k, str) and k.startswith('twp-'))}
    twp_tasks = {k: v for k, v in tasks_all.items()
                 if isinstance(k, str) and k.startswith('twp-')}
    tasks_total = len(tasks)
    tasks_done = sum(1 for v in tasks.values() if (v if isinstance(v, bool) else v.get('done', False)))
    twp_total = len(twp_tasks)
    twp_done = sum(1 for v in twp_tasks.values() if (v if isinstance(v, bool) else v.get('done', False)))

    # Summarize wiki entries (last 5)
    recent_wiki = [
        {
            'date': e.get('date', ''),
            'title': e.get('title', ''),
            'content_preview': (e.get('content', '')[:120] + '...') if len(e.get('content', '')) > 120 else e.get('content', ''),
            'tags': e.get('tags', []),
            'id': e.get('id', '')
        }
        for e in wiki_entries[:5]
    ]

# Summarize chat (last 3 exchanges = 6 messages)
    chat_summaries = []
    for i in range(0, len(chat_recent)-1, 2):
        if i+1 < len(chat_recent):
            user_msg = _strip(chat_recent[i].get('content', ''))
            assistant_msg = _strip(chat_recent[i+1].get('content', '') if i+1 < len(chat_recent) else '')
            user_preview = user_msg[:80] + ('...' if len(user_msg) > 80 else '')
            ai_preview = assistant_msg[:100] + ('...' if len(assistant_msg) > 100 else '')
            chat_summaries.append({
                'user': user_preview,
                'ai': ai_preview,
                'time': chat_recent[i].get('time', '')
            })
    chat_summaries = chat_summaries[-3:]  # last 3 exchanges

    resp = jsonify({
        'child': child,
        'recent_wiki': recent_wiki,
        'chat_summaries': chat_summaries,
        'tasks_done': tasks_done,
        'tasks_total': tasks_total,
        'twp_done': twp_done,
        'twp_total': twp_total,
        'updated': datetime.now().isoformat()
    })
    _set_cors(resp)
    return resp


# ── Weekly Review ────────────────────────────────────────────
@app.route('/api/review', methods=['GET', 'POST', 'OPTIONS'])
def review_api():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    user = _safe_child(request.args.get('user') or (request.get_json(silent=True) or {}).get('user', 'diya'))
    review_path = os.path.join(DATA_DIR, f'reviews_{user}.json')

    if request.method == 'GET':
        if os.path.exists(review_path):
            with open(review_path) as f:
                reviews = json.load(f)
        else:
            reviews = []
        resp = jsonify(reviews)
        _set_cors(resp)
        return resp

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        review = data.get('review', data)
        if os.path.exists(review_path):
            with open(review_path) as f:
                reviews = json.load(f)
        else:
            reviews = []
        reviews.insert(0, review)
        atomic_write(review_path, reviews)
        resp = jsonify({'ok': True})
        _set_cors(resp)
        return resp


# ── Chat-to-MD Sync ────────────────────────────────────────────
SYNC_STATE_FILE = os.path.join(DATA_DIR, 'sync_state.json')

def _load_sync_state():
    if os.path.exists(SYNC_STATE_FILE):
        with open(SYNC_STATE_FILE) as f:
            return json.load(f)
    return {}

def _save_sync_state(state):
    atomic_write(SYNC_STATE_FILE, state, ensure_ascii=False, indent=2)

def _strip(t):
    """Strip thinking/reasoning tags from LLM output."""
    t = re.sub(r'<think>\n[\s\S]*?</think>\n\n', '', t)
    t = re.sub(r'<think>\n[\s\S]*?</think>\n', '', t)
    t = re.sub(r'<think>[\s\S]*?</think>', '', t)
    t = re.sub(r'\[撮要\n[\s\S]*?\[/撮要\]\n\n', '', t)
    t = re.sub(r'\[撮要\n[\s\S]*?\[/撮要\]\n', '', t)
    t = re.sub(r'\[撮要[^\]]*\][\s\S]*?\]', '', t)
    return t.strip()

def _sync_chat_to_md(child, conv):
    """Sync unsynced chat exchanges to the child's MD planning file."""
    plan_base = os.environ.get('PLAN_MD_PATH', os.path.join(DATA_DIR, 'plan_context'))
    plan_file = os.path.join(plan_base, f'{child.capitalize()}_Plan.md')
    if not os.path.exists(plan_file):
        return

    state = _load_sync_state()
    last_synced = state.get(child, -1)  # index of last synced assistant message

    # Find new exchanges (user+assistant pairs) since last sync
    new_pairs = []
    for i in range(last_synced + 1, len(conv) - 1, 2):
        if conv[i].get('role') == 'user' and i + 1 < len(conv) and conv[i + 1].get('role') == 'assistant':
            new_pairs.append((conv[i], conv[i + 1]))

    if not new_pairs:
        return

    # Build Markdown entries
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    new_lines = []
    for user_msg, ai_msg in new_pairs:
        user_text = _strip(user_msg.get('content', ''))
        ai_text = _strip(ai_msg.get('content', ''))
        # Keep first 300 chars of AI reply as summary
        ai_summary = ai_text[:300] + ('...' if len(ai_text) > 300 else '')
        new_lines.append(f'\n### {today}\n**💬 问**: {user_text[:150]}\n**🤖 答**: {ai_summary}\n')

    # Read MD file
    with open(plan_file, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # Find or create the activity log section
    section_marker = '## 📝 动态活动日志 (AI自动同步)\n'
    marker_pos = md_content.find(section_marker)

    if marker_pos == -1:
        # Section doesn't exist — append at end (before final status line if present)
        # Find the last `---` separator and insert before it
        last_sep = md_content.rfind('\n---\n')
        if last_sep != -1:
            before = md_content[:last_sep]
            after = md_content[last_sep:]
        else:
            # Find last `>` status line
            last_status = md_content.rfind('\n> **')
            if last_status != -1:
                before = md_content[:last_status]
                after = md_content[last_status:]
            else:
                before = md_content
                after = ''

        md_content = before + '\n\n---\n\n' + section_marker + '\n'.join(new_lines) + after
    else:
        # Section exists — insert new entries after the section header
        insert_pos = marker_pos + len(section_marker)
        md_content = md_content[:insert_pos] + '\n'.join(new_lines) + md_content[insert_pos:]

    # Keep only the last 20 entries to avoid bloat
    section_start = md_content.find(section_marker)
    if section_start != -1:
        header = md_content[:section_start + len(section_marker)]
        entries_part = md_content[section_start + len(section_marker):]
        # Split into entries (separated by \n###)
        entry_blocks = entries_part.split('\n### ')
        header_line = entry_blocks[0] if entry_blocks else ''
        entries = entry_blocks[1:] if len(entry_blocks) > 1 else []
        # Keep last 20
        if len(entries) > 20:
            entries = entries[-20:]
        md_content = header + header_line + ('\n### ' + '\n### '.join(entries) if entries else '')

    with open(plan_file, 'w', encoding='utf-8') as f:
        f.write(md_content)

    # Update sync state
    state[child] = len(conv) - 1
    _save_sync_state(state)


# ── Activity Feed (from MD) ──────────────────────────────────
@app.route('/api/activity/<child>', methods=['GET', 'OPTIONS'])
def activity_api(child):
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    plan_base = os.environ.get('PLAN_MD_PATH', os.path.join(DATA_DIR, 'plan_context'))
    plan_file = os.path.join(plan_base, f'{child.capitalize()}_Plan.md')
    if not os.path.exists(plan_file):
        return jsonify({'entries': [], 'child': child})

    with open(plan_file, 'r', encoding='utf-8') as f:
        md_content = f.read()

    section_marker = '## 📝 动态活动日志'
    marker_pos = md_content.find(section_marker)
    if marker_pos == -1:
        return jsonify({'entries': [], 'child': child})

    section = md_content[marker_pos:]

    # Parse entries: each starts with ### YYYY-MM-DD HH:MM
    import re as re_mod
    entries = []
    raw_entries = re_mod.split(r'\n### ', section)[1:]  # skip section header
    for raw in raw_entries[:10]:  # last 10 entries
        lines = raw.strip().split('\n')
        if not lines:
            continue
        time_str = lines[0].strip()
        question = ''
        answer = ''
        for line in lines[1:]:
            if line.startswith('**💬 问**'):
                question = line.replace('**💬 问**: ', '').strip()
            elif line.startswith('**🤖 答**'):
                answer = line.replace('**🤖 答**: ', '').strip()
        if question or answer:
            entries.append({
                'time': time_str,
                'question': question,
                'answer': answer[:200] + ('...' if len(answer) > 200 else '')
            })

    resp = jsonify({
        'child': child,
        'entries': entries,
        'updated': datetime.now().isoformat()
    })
    _set_cors(resp)
    return resp


# ── Manual Sync Trigger ──────────────────────────────────────
@app.route('/api/sync/<child>', methods=['POST', 'OPTIONS'])
def sync_api(child):
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    conv_path = os.path.join(DATA_DIR, f'chat_{child}.json')
    if not os.path.exists(conv_path):
        return jsonify({'ok': True, 'synced': 0, 'child': child})

    with open(conv_path) as f:
        conv = json.load(f)

    try:
        _sync_chat_to_md(child, conv)
        return jsonify({'ok': True, 'synced': len(conv), 'child': child})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'child': child}), 500


# ── Research Intel (from MD) ────────────────────────────────
@app.route('/api/intel', methods=['GET', 'OPTIONS'])
def intel_api():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    intel_path = os.environ.get('RESEARCH_INTEL_PATH', os.path.join(DATA_DIR, 'plan_context', 'Research_Intel.md'))
    if not os.path.exists(intel_path):
        return jsonify({'sections': [], 'updated': '', 'error': 'no data'})

    with open(intel_path, 'r', encoding='utf-8') as f:
        md = f.read()

    import re as re_mod
    sections = []
    markers = [
        ('🏫 Diya', '🎯 Diya 目标学校'),
        ('🏫 Jemma', '🎯 Jemma 目标学校'),
        ('🏆 竞赛', '🏆 竞赛日历'),
        ('📝 申请', '📝 申请经验 & 趋势'),
    ]
    for search, display in markers:
        pos = md.find(f'## {search}')
        if pos == -1:
            continue
        next_pos = md.find('\n## ', pos + 5)
        if next_pos == -1:
            next_pos = len(md)
        body = md[pos:next_pos]
        items = []
        for link in re_mod.finditer(r'- \*\*\[([^\]]+)\]\(([^)]+)\)\*\*\n  (.+)', body):
            items.append({
                'title': link.group(1),
                'url': link.group(2),
                'snippet': link.group(3).strip()[:200]
            })
        if items:
            sections.append({'title': display, 'items': items})

    updated = ''
    date_match = re_mod.search(r'自动采集时间：([^\\n]+)', md)
    if date_match:
        updated = date_match.group(1).strip()

    resp = jsonify({'sections': sections, 'updated': updated})
    _set_cors(resp)
    return resp


# ── Resource Downloads (scan download dir) ──────────────
@app.route('/api/resources', methods=['GET', 'OPTIONS'])
def resources_api():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    dl_dir = '/var/www/plan/downloads'
    if not os.path.exists(dl_dir):
        return jsonify({'files': [], 'error': 'downloads dir not found'})

    # Categorize by filename pattern
    category_map = {
        'Edexcel_GCSE_Maths': 'maths',
        'AQA_GCSE_Physics': 'physics',
        'AQA_GCSE_Chemistry': 'chemistry',
        'AQA_FMaths': 'fmaths',
        'Edexcel_GCSE_English': 'english',
        'AQA_Physics_Equation': 'physics',
        'AQA_Physics_Required': 'physics',
        'AQA_Chemistry_Required': 'chemistry',
        'Corbettmaths': 'maths',
        'AQA_FMaths_Level2': 'fmaths',
    }

    files = []
    for fname in sorted(os.listdir(dl_dir)):
        fpath = os.path.join(dl_dir, fname)
        if not os.path.isfile(fpath) or fname.startswith('.') or fname.endswith('.md'):
            continue
        size = os.path.getsize(fpath)
        # Determine category
        cat = 'other'
        for prefix, c in category_map.items():
            if fname.startswith(prefix):
                cat = c
                break
        # Human-readable name
        name = fname.replace('.pdf', '').replace('_', ' ')
        files.append({
            'name': name,
            'file': f'/downloads/{fname}',
            'category': cat,
            'size': f'{size//1024} KB' if size < 1048576 else f'{size/1048576:.1f} MB'
        })

    resp = jsonify({'files': files})
    _set_cors(resp)
    return resp


# ── Parse Error (AI Quick Capture) ──────────────────────
@app.route('/api/parse-error', methods=['POST', 'OPTIONS'])
def parse_error():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    data = request.get_json(force=True, silent=True) or {}
    raw_text = data.get('text', '').strip()
    child = data.get('child', 'diya')

    if not raw_text:
        resp = jsonify({'error': 'No text provided'})
        _set_cors(resp)
        return resp

    prompt = f"""You are an error log parser for a GCSE student (Year 10, studying Maths, FMaths, Physics, Chemistry, English, SMC).

Given this raw error description, extract structured data in JSON format:
{{
  "subject": "one of: Maths, FMaths, Physics, Chemistry, English, SMC, Other",
  "errorType": "one of: calc (calculation mistake), concept (didn't understand), reading (misread question), analysis (analysis paragraph/essay), structure (PEEL/structure), timing, other",
  "solution": "1-2 sentences on the correct approach or what to do differently",
  "title": "short summary of the mistake (max 8 words)"
}}

Raw text:
{raw_text[:800]}

Return ONLY valid JSON, nothing else."""

    try:
        cfg = _load_config()
        api_key = cfg.get('api_key', '')
        base_url = cfg.get('base_url', 'https://api.deepseek.com')
        model = cfg.get('model', 'deepseek-v4-pro')
    except:
        api_key = ''
        base_url = 'https://api.deepseek.com'
        model = 'deepseek-v4-pro'

    if not api_key:
        resp = jsonify({'error': 'LLM not configured'})
        _set_cors(resp)
        return resp

    try:
        with httpx.Client(timeout=30.0) as client:
            llm_resp = client.post(
                f'{base_url}/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': model,
                    'messages': [
                        {'role': 'system', 'content': 'You are a JSON-only parser. Return only valid JSON.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_tokens': 400,
                    'temperature': 0.1
                }
            )
            if llm_resp.status_code == 200:
                result = llm_resp.json()
                content = result['choices'][0]['message']['content']
                import re as _re
                json_match = _re.search(r'\{[^{}]*\}', content, _re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group(0))
                    resp = jsonify(parsed)
                else:
                    resp = jsonify({'raw': content, 'subject': 'Other', 'errorType': 'other', 'solution': content[:200]})
            else:
                resp = jsonify({'error': f'LLM error {llm_resp.status_code}'})
    except Exception as e:
        resp = jsonify({'error': str(e)[:100]})

    _set_cors(resp)
    return resp


@app.route('/api/error-save', methods=['POST', 'OPTIONS'])
def error_save():
    """Save an error entry from email pipeline or external source."""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    data = request.get_json(force=True, silent=True) or {}
    child = _safe_child(data.get('child', 'diya'))

    # Support both flat format (email pipeline) and rich format (exam grading)
    entry = data.get('error', None)
    if entry and isinstance(entry, dict):
        err = entry
        err.setdefault('date', datetime.now().isoformat())
        err.setdefault('source', 'Exam Grading')
        err.setdefault('enriched', True)
    else:
        # 字段名与前端读取保持一致（P0-6）：question / solution，另补 raw 别名兼容旧调用方
        err = {
            'question': data.get('question', data.get('raw', '')),
            'subject': data.get('subject', ''),
            'type': data.get('type', 'other'),
            'solution': data.get('solution', ''),
            'source': data.get('source', 'Email'),
            'date': datetime.now().isoformat(),
            'enriched': bool(data.get('subject'))
        }
    err.setdefault('id', str(uuid.uuid4()))   # P0-6：每条错题必须有稳定 id，否则无法删除

    path = os.path.join(DATA_DIR, f'errors_{child}.json')
    errors = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                errors = json.load(f)
        except Exception:
            errors = safe_load(path, [])
    errors.insert(0, err)
    atomic_write(path, errors, ensure_ascii=False, indent=2)

    resp = jsonify({'ok': True, 'child': child, 'total': len(errors)})
    _set_cors(resp)
    return resp


@app.route('/api/errors/<child>', methods=['GET', 'POST', 'OPTIONS'])
def get_errors(child):
    """Get / append error entries for a child."""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    child = _safe_child(child)

    path = os.path.join(DATA_DIR, f'errors_{child}.json')

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        err = {
            'id': str(uuid.uuid4()),
            'subject': (data.get('subject') or '').strip() or 'Other',
            # 兼容历史字段名：raw → question，correct_solution → solution
            'question': (data.get('question') or data.get('raw') or '').strip(),
            'solution': (data.get('solution') or data.get('correct_solution') or '').strip(),
            'type': (data.get('type') or 'other').strip(),
            'tags': data.get('tags', '') or '',
            'date': data.get('date') or datetime.now().strftime('%Y-%m-%d'),
            'source': data.get('source') or 'Manual',
            'mastered': bool(data.get('mastered', False)),
        }
        if not err['question']:
            resp = jsonify({'ok': False, 'error': '题目内容（question）不能为空'})
            _set_cors(resp)
            return resp, 400
        errors = list(_cached_json_read(path, ttl=15) or [])
        errors.insert(0, err)
        atomic_write(path, errors, ensure_ascii=False, indent=2)
        _json_cache.pop(path, None)
        resp = jsonify({'ok': True, 'id': err['id'], 'total': len(errors)})
        _set_cors(resp)
        return resp

    errors = _cached_json_read(path, ttl=15) or []

    # 存量补齐 id：历史记录（113 条）无 id，导致无法删除——读取时一次性回写
    need_id = [e for e in errors if isinstance(e, dict) and not e.get('id')]
    if need_id:
        for e in need_id:
            e['id'] = str(uuid.uuid4())
        atomic_write(path, errors, ensure_ascii=False, indent=2)
        _json_cache.pop(path, None)

    # #12 排序归一化：与前端 renderErrors 的排序口径一致（日期倒序 → 优先级倒序），
    # 这样分页切片后的顺序与不分页时完全相同，避免"翻页后顺序变了"的观感问题。
    _prio = {'high': 3, 'medium': 2, 'low': 1}
    errors = sorted(
        errors,
        key=lambda e: (str(e.get('date') or ''), _prio.get(e.get('priority'), 0)),
        reverse=True,
    )

    # Aggregate mode for dashboard (avoids shipping the full ~276KB file to the client)
    if request.args.get('stats') == '1':
        by_subject = {}
        by_cause = {}
        mastered_total = 0
        for e in errors:
            if not isinstance(e, dict):
                continue
            s = e.get('subject', 'Other')
            by_subject[s] = by_subject.get(s, 0) + 1
            # R7-b：错因三分类统计（粗心/没懂/没时间；未分类不计入）
            c = e.get('cause')
            if c:
                by_cause[c] = by_cause.get(c, 0) + 1
            if e.get('mastered'):
                mastered_total += 1
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        new_this_week = sum(
            1 for e in errors
            if isinstance(e, dict) and str(e.get('date') or '') >= week_ago
        )
        resp = jsonify({
            'child': child,
            'total': len(errors),
            'by_subject': by_subject,
            'by_cause': by_cause,
            # #12：分页后前端拿不到全量数据，计数改由服务端提供
            'mastered_total': mastered_total,
            'active_total': len(errors) - mastered_total,
            'new_this_week': new_this_week,
        })
        _set_cors(resp)
        return resp

    # #12 可选过滤 + 分页（不传 page/limit 时与旧行为完全一致：返回全部）
    subject = (request.args.get('subject') or '').strip()
    if subject and subject != 'all':
        errors = [e for e in errors if (e.get('subject') or 'Other') == subject]
    m_arg = request.args.get('mastered')
    if m_arg in ('0', '1'):
        want = (m_arg == '1')
        errors = [e for e in errors if bool(e.get('mastered')) == want]

    page = request.args.get('page', type=int)
    limit = request.args.get('limit', type=int)
    payload = {
        'child': child,
        'total': len(errors),          # 过滤后的总条数（非总量）
        'page': 1,
        'limit': None,
        'has_more': False,
    }
    if page or limit:
        page = max(1, page or 1)
        limit = max(1, min(int(limit or 50), 200))   # 上限 200，防止一次拉爆
        start = (page - 1) * limit
        payload['page'] = page
        payload['limit'] = limit
        payload['has_more'] = start + limit < len(errors)
        payload['errors'] = errors[start:start + limit]
    else:
        payload['errors'] = errors
    resp = jsonify(payload)
    _set_cors(resp)
    return resp


@app.route('/api/errors/<child>/<err_id>', methods=['GET', 'PUT', 'DELETE', 'OPTIONS'])
def error_item(child, err_id):
    """单条错题的读/改/删（P0-1：前端 DELETE 此前因无此路由全部 405）。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, PUT, DELETE, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    child = _safe_child(child)
    path = os.path.join(DATA_DIR, f'errors_{child}.json')
    errors = list(_cached_json_read(path, ttl=15) or [])

    if request.method == 'GET':
        hit = next((e for e in errors if str(e.get('id')) == str(err_id)), None)
        if not hit:
            resp = jsonify({'error': 'not found'})
            _set_cors(resp)
            return resp, 404
        resp = jsonify(hit)
        _set_cors(resp)
        return resp

    if request.method == 'DELETE':
        remain = [e for e in errors if str(e.get('id')) != str(err_id)]
        if len(remain) == len(errors):
            resp = jsonify({'ok': False, 'error': 'not found'})
            _set_cors(resp)
            return resp, 404
        atomic_write(path, remain, ensure_ascii=False, indent=2)
        _json_cache.pop(path, None)
        resp = jsonify({'ok': True, 'total': len(remain)})
        _set_cors(resp)
        return resp

    # PUT：局部更新（含 mastered 勾选 / R7 错因三分类）
    data = request.get_json(force=True, silent=True) or {}
    found = False
    for e in errors:
        if str(e.get('id')) == str(err_id):
            for k in ('subject', 'question', 'solution', 'type', 'tags', 'date', 'mastered', 'cause'):
                if k in data:
                    e[k] = data[k]
            found = True
            break
    if not found:
        resp = jsonify({'ok': False, 'error': 'not found'})
        _set_cors(resp)
        return resp, 404
    atomic_write(path, errors, ensure_ascii=False, indent=2)
    _json_cache.pop(path, None)
    resp = jsonify({'ok': True})
    _set_cors(resp)
    return resp


# ── Notes (路线图 #11：从 localStorage 提升到服务端持久化) ──────────
# 设计取舍：两份前端的 Notes 数据结构不同（Diya 是按科目的多条卡片数组，
# Jemma 是单段文本），因此后端**不规定结构**，只存一个自由形态的 payload
# 外加 updatedAt 时间戳，由前端自行解释。这样无需改动任何 UI 即可完成持久化，
# 也避免为两种结构各写一套接口。

def _notes_path(child):
    return os.path.join(DATA_DIR, f'notes_{child}.json')

@app.route('/api/notes/<child>', methods=['GET', 'PUT', 'OPTIONS'])
def notes_api(child):
    """Notes 读写。存储形态：{'updatedAt': ISO8601, 'payload': <任意 JSON>}"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, PUT, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    child = _safe_child(child)
    path = _notes_path(child)

    if request.method == 'GET':
        data = _cached_json_read(path, ttl=10) or {}
        if not isinstance(data, dict):
            data = {}
        resp = jsonify({
            'child': child,
            'updatedAt': data.get('updatedAt') or '',
            'payload': data.get('payload') if isinstance(data.get('payload'), dict) else {},
        })
        _set_cors(resp)
        return resp

    # PUT：整体覆盖（前端每次提交全量 payload，天然幂等）
    body = request.get_json(force=True, silent=True) or {}
    payload = body.get('payload')
    if not isinstance(payload, dict):
        resp = jsonify({'ok': False, 'error': 'payload 必须是对象'})
        _set_cors(resp)
        return resp, 400
    updated_at = str(body.get('updatedAt') or datetime.now().isoformat())
    atomic_write(path, {'updatedAt': updated_at, 'payload': payload},
                 ensure_ascii=False, indent=2)
    _json_cache.pop(path, None)
    resp = jsonify({'ok': True, 'updatedAt': updated_at})
    _set_cors(resp)
    return resp

# ── Decide（孩子的自主决定：Latin 要不要继续学等）───────────────────────
# 与 Notes 同构：自由 payload（{前端 localStorage key: choice}）+ updatedAt，
# 前端本地即时渲染、服务端做跨设备真源。data/decide_<child>.json 运行时生成，
# 不在 git 跟踪范围；CI rsync --exclude='data/'，部署流水线不会覆盖它。
def _decide_path(child):
    return os.path.join(DATA_DIR, f'decide_{child}.json')

@app.route('/api/decide/<child>', methods=['GET', 'PUT', 'OPTIONS'])
def decide_api(child):
    """决定卡状态读写。存储形态：{'updatedAt': ISO8601, 'payload': {key: choice}}"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, PUT, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    child = _safe_child(child)
    path = _decide_path(child)

    if request.method == 'GET':
        data = _cached_json_read(path, ttl=10) or {}
        if not isinstance(data, dict):
            data = {}
        payload = data.get('payload')
        resp = jsonify({
            'child': child,
            'updatedAt': data.get('updatedAt') or '',
            'payload': payload if isinstance(payload, dict) else {},
        })
        _set_cors(resp)
        return resp

    # PUT：整体覆盖（前端每次提交全量快照，天然幂等）
    body = request.get_json(force=True, silent=True) or {}
    payload = body.get('payload')
    if not isinstance(payload, dict):
        resp = jsonify({'ok': False, 'error': 'payload 必须是对象'})
        _set_cors(resp)
        return resp, 400
    updated_at = str(body.get('updatedAt') or datetime.now().isoformat())
    atomic_write(path, {'updatedAt': updated_at, 'payload': payload},
                 ensure_ascii=False, indent=2)
    _json_cache.pop(path, None)
    resp = jsonify({'ok': True, 'updatedAt': updated_at})
    _set_cors(resp)
    return resp


# ── Three-Week Plan (TWP) 三周计划：改计划不再需要发版 ──────────
# 存储形态（data/twp_<child>.json）与种子（data-seed/twp_<child>.json）同构：
#   {'child','version','updatedAt','nextCycleEnd','weeks':[{id,label,start,end,goal,focus,actions:[{id,label,sub}]}]}
# GET：优先读运行时文件；不存在则从种子复制一份（服务器 data/ 为真源，种子随 rsync 上线）。
# PUT：家长更新计划（weeks 结构校验：至少 1 周、每周必有 start/end/label、actions 可选）。
TWP_SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data-seed')

def _twp_path(child):
    return os.path.join(DATA_DIR, f'twp_{child}.json')

def _twp_seed_path(child):
    return os.path.join(TWP_SEED_DIR, f'twp_{child}.json')

def _twp_valid(data):
    """结构校验：weeks 至少 1 周，每周含 label/start/end（YYYY-MM-DD）；actions 若有须为 list。"""
    if not isinstance(data, dict) or not isinstance(data.get('weeks'), list) or not data['weeks']:
        return False
    for w in data['weeks']:
        if not isinstance(w, dict) or not w.get('label') or not w.get('start') or not w.get('end'):
            return False
        if 'actions' in w and not isinstance(w['actions'], list):
            return False
    return True

@app.route('/api/twp/<child>', methods=['GET', 'PUT', 'OPTIONS'])
def twp_api(child):
    """三周计划读写（R5：数据外置，前端异步拉取 + localStorage 降级）。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, PUT, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    child = _safe_child(child)
    path = _twp_path(child)

    if request.method == 'GET':
        data = _cached_json_read(path, ttl=10)
        if not isinstance(data, dict) or not data.get('weeks'):
            # 运行时文件不存在/为空：从种子复制一份（首次访问自动初始化）
            seed = _twp_seed_path(child)
            if os.path.exists(seed):
                try:
                    with open(seed, encoding='utf-8') as f:
                        data = json.load(f)
                    atomic_write(path, data, ensure_ascii=False, indent=2)
                    _json_cache.pop(path, None)
                except Exception:
                    data = {}
        if not isinstance(data, dict):
            data = {}
        resp = jsonify({
            'child': child,
            'updatedAt': data.get('updatedAt') or '',
            'nextCycleEnd': data.get('nextCycleEnd') or '',
            'version': data.get('version') or 0,
            'weeks': data.get('weeks') if isinstance(data.get('weeks'), list) else [],
            'seeded': not os.path.exists(path) or not data.get('weeks'),
        })
        _set_cors(resp)
        return resp

    # PUT：家长更新计划（全量覆盖，前端提交完整 weeks）
    body = request.get_json(force=True, silent=True) or {}
    weeks = body.get('weeks')
    if not _twp_valid({'weeks': weeks}):
        resp = jsonify({'ok': False, 'error': 'weeks 结构非法：至少 1 周，每周须含 label/start/end，actions 为数组'})
        _set_cors(resp)
        return resp, 400
    doc = {
        'child': child,
        'version': int(body.get('version') or 0) + 1,
        'updatedAt': str(body.get('updatedAt') or datetime.now().isoformat()),
        'nextCycleEnd': str(body.get('nextCycleEnd') or ''),
        'weeks': weeks,
    }
    atomic_write(path, doc, ensure_ascii=False, indent=2)
    _json_cache.pop(path, None)
    resp = jsonify({'ok': True, 'version': doc['version'], 'updatedAt': doc['updatedAt']})
    _set_cors(resp)
    return resp


# ── SMC Progress Records (trend tracking) ─────────────────────
SMC_DATA_FILE = os.path.join(DATA_DIR, 'smc_records.json')

@app.route('/api/smc-records', methods=['GET', 'POST', 'OPTIONS'])
def smc_records_api():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    records = []
    if os.path.exists(SMC_DATA_FILE):
        try:
            with open(SMC_DATA_FILE) as f:
                records = json.load(f)
        except Exception:
            records = safe_load(path, [])
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        rec = {
            'id': data.get('id') or f"smc-{uuid.uuid4().hex[:8]}",
            'date': data.get('date') or datetime.now().strftime('%Y-%m-%d'),
            'paper': data.get('paper', 'SMC Mock'),
            'score': int(data.get('score', 0)),
            'correct': int(data.get('correct', 0)),
            'wrong': int(data.get('wrong', 0)),
            'blank': int(data.get('blank', 0)),
            'weakness': data.get('weakness', ''),
            'notes': data.get('notes', ''),
        }
        # Avoid duplicate on same date+paper
        records = [r for r in records if not (r.get('date') == rec['date'] and r.get('paper') == rec['paper'])]
        records.insert(0, rec)
        # Sort newest first
        records.sort(key=lambda r: r.get('date', ''), reverse=True)
        atomic_write(SMC_DATA_FILE, records, ensure_ascii=False, indent=2)
        resp = jsonify({'ok': True, 'total': len(records), 'record': rec})
    else:
        resp = jsonify({'records': records, 'total': len(records)})

    _set_cors(resp)
    return resp


# ── Image OCR (Vision LLM) ────────────────────────────────────
# OCR 模型链（方案 A：优先复用主 LLM 端点与 key，UI 无需单独配置 OCR 模型）
# 默认链 = coding plan 内最强→次强旗舰：若为原生多模态则一步到位，否则错误信息会明确提示不支持图像
OCR_MODELS_MAIN = [m.strip() for m in os.environ.get(
    'OCR_MODELS', 'qwen3.8-max,qwen3.7-plus,qwen3.8-flash').split(',') if m.strip()]
# 降级链：OpenRouter 上已知存活的 VL 模型（需 openrouter_key）
OCR_MODELS_OPENROUTER = [m.strip() for m in os.environ.get(
    'OCR_MODELS_OPENROUTER', 'qwen/qwen2.5-vl-72b-instruct,baidu/ernie-4.5-vl-424b-a47b').split(',') if m.strip()]

OCR_PROMPT_DEFAULT = (
    'You are an OCR engine. Transcribe ALL text in this exam/homework page word-for-word: '
    'printed questions, answer options, marks like [3 marks], and ALL handwritten answers. '
    'Keep original order, question numbers, line breaks and math symbols. '
    'Do NOT describe the image. Do NOT add any preamble, comment, or translation. '
    'Output ONLY the transcription.'
)

# 历史实测（ernie-4.5-vl）：8/20 页输出以 "The image is a transcription of..." 开头，污染转写结果
_OCR_PREAMBLE_RE = re.compile(
    r"^\s*(?:the (?:image|page|photo)s?\b|here (?:is|are)\b|this (?:image|page|photo)s?\b"
    r"|sure\b|okay\b|i(?:'ll| will| can)\b)[^\n]*\n+", re.I)

def _strip_ocr_preamble(text):
    """剥除 VL 模型的解释性前言（最多剥两行，防止误伤正文）"""
    for _ in range(2):
        if not text or len(text) < 80:
            break
        m = _OCR_PREAMBLE_RE.match(text)
        if not m:
            break
        text = text[m.end():]
    return (text or '').strip()

def _ocr_call_compat(base_url, api_key, model, image_b64, prompt, extra_headers=None, timeout=90):
    """单模型 OpenAI 兼容带图调用。错误时保留响应体摘要——用于诊断「模型不支持图像输入」。"""
    mime = 'image/png' if image_b64[:8] == 'iVBORw0K' else 'image/jpeg'
    payload = {
        'model': model,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{image_b64}'}}
            ]
        }],
        'max_tokens': 4000,
        'temperature': 0,
    }
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    if extra_headers:
        headers.update(extra_headers)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f'{base_url}/chat/completions', headers=headers, json=payload)
        if resp.status_code == 200:
            r = resp.json()
            text = (r.get('choices') or [{}])[0].get('message', {}).get('content', '')
            usage = r.get('usage', {}) or {}
            return {'ok': True, 'text': _strip_ocr_preamble(text), 'tokens': usage.get('total_tokens', 0)}
        # 401/402/429 属于配额/凭据问题，响应体保留原始信息便于定位
        return {'ok': False, 'error': f'HTTP {resp.status_code}: {resp.text[:150]}'}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {str(e)[:120]}'}

def _call_vision_ocr(image_b64, prompt):
    """OCR 调用：主 LLM 端点（coding plan key）→ OpenRouter 降级。返回带 attempts 诊断链。"""
    attempts = []
    cfg = {}
    try:
        cfg = _load_llm_config()
    except Exception:
        pass

    # 1) 主 LLM 端点：与聊天共用 endpoint + api_key，OCR 模型链可由 OCR_MODELS env 覆盖
    if cfg.get('base_url') and cfg.get('api_key'):
        for model in OCR_MODELS_MAIN:
            r = _ocr_call_compat(cfg['base_url'], cfg['api_key'], model, image_b64, prompt)
            if r.get('ok'):
                r.update(model=model, provider='main', attempts=attempts)
                return r
            attempts.append(f'[main] {model}: {r.get("error", "")}')

    # 2) OpenRouter 降级（VL 专用模型，需 openrouter_key）
    or_key = os.environ.get('OPENROUTER_API_KEY', '') or (cfg.get('openrouter_key') or '')
    if or_key:
        for model in OCR_MODELS_OPENROUTER:
            r = _ocr_call_compat(
                'https://openrouter.ai/api/v1', or_key, model, image_b64, prompt,
                extra_headers={'HTTP-Referer': 'https://grow-consultant.com', 'X-Title': 'Study Compass OCR'},
                timeout=120)
            if r.get('ok'):
                r.update(model=model, provider='openrouter', attempts=attempts)
                return r
            attempts.append(f'[openrouter] {model}: {r.get("error", "")}')

    if not attempts:
        return {'error': 'no OCR provider configured (need main LLM key or OPENROUTER key)', 'text': '', 'attempts': []}
    return {'error': '; '.join(attempts[-3:]), 'text': '', 'attempts': attempts}


@app.route('/api/ocr', methods=['POST', 'OPTIONS'])
def ocr_api():
    """Recognize text from uploaded images."""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    data = request.get_json(force=True, silent=True) or {}
    image_b64 = data.get('image', '')
    prompt = data.get('prompt') or OCR_PROMPT_DEFAULT

    if not image_b64:
        return jsonify({'error': 'no image data'}), 400

    result = _call_vision_ocr(image_b64, prompt)
    return jsonify(result)


# ── Upload & OCR Pipeline（作业/报告上传识别入库，设计见 UPLOAD_FEATURE_DESIGN.md）──

UPLOAD_ROOT = os.path.join(DATA_DIR, 'uploads')
UPLOAD_INDEX = os.path.join(DATA_DIR, 'uploads_index.json')
UPLOAD_CHILDREN = ('diya', 'jemma')
UPLOAD_MAX_IMG = 10 * 1024 * 1024        # 图片 ≤10MB
UPLOAD_MAX_PDF = 30 * 1024 * 1024        # PDF ≤30MB
UPLOAD_MAX_PAGES = 20                    # PDF ≤20 页
OCR_EXTRACT_MODEL = os.environ.get('OCR_EXTRACT_MODEL', 'qwen3.8-flash')  # 字段抽取模型（文本）

app.config['MAX_CONTENT_LENGTH'] = 35 * 1024 * 1024

import concurrent.futures
import threading

_upload_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)   # OCR 属 IO 密集，2 并发
_upload_lock = threading.Lock()          # meta/index 进程内写锁
# 上传频率限制（进程内计数；gunicorn 每 worker 独立，家庭场景足够）
_upload_rate = {}                        # ip -> [timestamps]

try:
    import pypdfium2 as pdfium           # PDF 渲染（纯 pip，免系统 poppler）
except Exception:
    pdfium = None


def _upload_dir(child, uid):
    return os.path.join(UPLOAD_ROOT, child, uid[:8], uid)

def _upload_meta_path(child, uid):
    return os.path.join(_upload_dir(child, uid), 'meta.json')

def _save_upload_meta(child, uid, meta):
    meta['updated'] = datetime.now().isoformat(timespec='seconds')
    with _upload_lock:
        atomic_write(_upload_meta_path(child, uid), meta, indent=2)

def _load_upload_meta(child, uid):
    return safe_load(_upload_meta_path(child, uid), default=None)

def _load_upload_index():
    with _upload_lock:
        return safe_load(UPLOAD_INDEX, default=[])

def _save_upload_index(entries):
    with _upload_lock:
        atomic_write(UPLOAD_INDEX, entries, indent=2)

def _index_upsert(child, uid, patch):
    """线程安全地更新 index 条目（读-改-写）"""
    with _upload_lock:
        entries = safe_load(UPLOAD_INDEX, default=[])
        hit = None
        for e in entries:
            if e.get('id') == uid and e.get('child') == child:
                hit = e
                break
        if hit is None:
            hit = {'id': uid, 'child': child}
            entries.append(hit)
        hit.update(patch)
        atomic_write(UPLOAD_INDEX, entries, indent=2)

def _index_remove(child, uid):
    with _upload_lock:
        entries = safe_load(UPLOAD_INDEX, default=[])
        entries = [e for e in entries if not (e.get('id') == uid and e.get('child') == child)]
        atomic_write(UPLOAD_INDEX, entries, indent=2)

def _sniff_upload(data, filename):
    """文件魔数嗅探（优先于扩展名）。返回 (kind, ext) 或 (None, reason)"""
    head = data[:16]
    ext = os.path.splitext(filename or '')[1].lower()
    if head.startswith(b'%PDF'):
        return ('pdf', '.pdf')
    if head.startswith(b'\x89PNG'):
        return ('image', '.png')
    if head.startswith(b'\xff\xd8\xff'):
        return ('image', '.jpg')
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return ('image', '.webp')
    if ext in ('.pdf',):
        return (None, '文件内容不是有效 PDF（头部特征不符）')
    if ext in ('.png', '.jpg', '.jpeg', '.webp'):
        return (None, '文件内容与扩展名不符（已按文件头校验）')
    return (None, f'不支持的格式 {ext or "（无扩展名）"}，仅支持 PNG/JPG/WEBP/PDF')

def _render_pages(child, uid, meta):
    """PDF → 逐页 PNG（200dpi）；图片 → 直接引用原文件。返回 meta['pages'] 列表。"""
    d = _upload_dir(child, uid)
    kind = meta['kind']
    if kind == 'image':
        return [{'no': 1, 'image': meta['stored_name'], 'ok': False}]
    if pdfium is None:
        raise RuntimeError('服务器未安装 pypdfium2，无法解析 PDF')
    pdf = pdfium.PdfDocument(os.path.join(d, meta['stored_name']))
    total = len(pdf)
    if total > UPLOAD_MAX_PAGES:
        raise RuntimeError(f'PDF 共 {total} 页，超过上限 {UPLOAD_MAX_PAGES} 页')
    pages = []
    render_errors = []
    for i in range(total):
        try:
            img = pdf[i].render(scale=200 / 72).to_pil()
            name = f'page-{i + 1:02d}.png'
            img.save(os.path.join(d, name), 'PNG')
            pages.append({'no': i + 1, 'image': name, 'ok': False})
        except Exception as e:
            render_errors.append({'page': i + 1, 'error': str(e)[:120]})
            pages.append({'no': i + 1, 'image': None, 'ok': False, 'render_error': str(e)[:120]})
    try:
        pdf.close()
    except Exception:
        pass
    meta['render_errors'] = render_errors
    return pages

def _extract_upload_fields(full_text, child):
    """字段抽取：主 LLM（文本模型）输出 JSON。失败返回 None（不阻塞入库）。"""
    cfg = _load_llm_config()
    if not cfg.get('base_url') or not cfg.get('api_key'):
        return None
    prompt = (
        '从下面的作业/试卷 OCR 文本中抽取结构化字段，只输出 JSON（不要 markdown 围栏）：\n'
        '{"subject": "科目(枚举 Maths/FMaths/Physics/Chemistry/Biology/English/History/Drama/CS/Other)", '
        '"doc_type": "homework|report|exam|quiz", "title": "≤80字标题", "date": "YYYY-MM-DD或null", '
        '"score": {"got": 数字, "total": 数字} 或 null, '
        '"topics": ["知识点标签，≤5个"], '
        '"mistakes": [{"question_no": "题号", "wrong_answer": "学生的错误答案", '
        '"correct_answer": "正确答案或要点", "reason": "错误原因一句话"}], '
        '"weakness_summary": "一句话弱点总结，≤120字"}\n'
        '只识别真实存在的错题（有学生错误答案的题）；没有错题时 mistakes 为空数组。\n\n'
        'OCR 文本：\n' + full_text[:12000]
    )
    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f'{cfg["base_url"]}/chat/completions',
                headers={'Authorization': f'Bearer {cfg["api_key"]}', 'Content-Type': 'application/json'},
                json={'model': OCR_EXTRACT_MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                      'max_tokens': 2000, 'temperature': 0.1}
            )
        if resp.status_code != 200:
            return None
        raw = (resp.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
        m = re.search(r'\{.*\}', raw, re.S)
        if not m:
            return None
        out = json.loads(m.group(0))
        out['model'] = OCR_EXTRACT_MODEL
        return out
    except Exception:
        return None

def _process_upload(child, uid):
    """异步管线：rendering → ocr（逐页）→ extract → done。失败置 failed，文件保留可重试。"""
    meta = _load_upload_meta(child, uid)
    if not meta:
        return
    try:
        # 1) 渲染
        meta['status'] = 'rendering'
        _save_upload_meta(child, uid, meta)
        pages = _render_pages(child, uid, meta)
        meta['pages'] = pages
        meta['page_count'] = len(pages)
        meta['status'] = 'ocr'
        meta['ocr_done'] = 0
        _save_upload_meta(child, uid, meta)

        # 2) 逐页 OCR（只跑尚无文本的页——支持部分重试）
        d = _upload_dir(child, uid)
        for pg in pages:
            if pg.get('ok') or not pg.get('image'):
                continue
            try:
                with open(os.path.join(d, pg['image']), 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode()
                r = _call_vision_ocr(b64, OCR_PROMPT_DEFAULT)
                if r.get('text'):
                    pg['raw_text'] = r['text']
                    pg['clean_text'] = r['text']
                    pg['ocr_model'] = r.get('model', '')
                    pg['ok'] = True
                else:
                    pg['ocr_error'] = (r.get('error') or 'empty result')[:200]
            except Exception as e:
                pg['ocr_error'] = str(e)[:200]
            meta['ocr_done'] = meta.get('ocr_done', 0) + 1
            _save_upload_meta(child, uid, meta)   # 每页落盘 → 前端轮询可见进度

        done_pages = [p for p in pages if p.get('ok')]
        if not done_pages:
            raise RuntimeError('所有页 OCR 均失败')

        full_text = '\n\n'.join(
            f'--- page {p["no"]} ---\n{p["clean_text"]}' for p in done_pages
        )
        meta['full_text'] = full_text

        # 3) 字段抽取（失败不阻塞）
        meta['status'] = 'extract'
        _save_upload_meta(child, uid, meta)
        ext = _extract_upload_fields(full_text, child)
        if ext:
            meta['extract'] = ext
            meta['extract_status'] = 'done'
        else:
            meta['extract'] = {}
            meta['extract_status'] = 'failed'

        meta['status'] = 'done'
        _save_upload_meta(child, uid, meta)
        e = meta.get('extract') or {}
        _index_upsert(child, uid, {
            'id': uid, 'child': child,
            'original_name': meta.get('original_name', ''),
            'doc_type': e.get('doc_type') or meta.get('doc_type') or 'homework',
            'subject': e.get('subject') or meta.get('subject') or 'Other',
            'title': e.get('title') or meta.get('original_name', ''),
            'date': e.get('date') or meta.get('created', '')[:10],
            'score': e.get('score'),
            'topics': e.get('topics') or [],
            'page_count': meta.get('page_count', 1),
            'size': meta.get('size', 0),
            'status': 'done',
            'ocr_status': 'done',
            'extract_status': meta.get('extract_status', 'failed'),
            'created': meta.get('created', ''),
            'updated': meta.get('updated', ''),
        })
    except Exception as e:
        try:
            meta['status'] = 'failed'
            meta['error'] = str(e)[:300]
            meta['retry_count'] = meta.get('retry_count', 0)
            _save_upload_meta(child, uid, meta)
            _index_upsert(child, uid, {'id': uid, 'child': child, 'status': 'failed',
                                       'error': meta['error'], 'updated': meta['updated']})
        except Exception:
            pass
        app.logger.exception('upload pipeline failed %s/%s', child, uid)


@app.errorhandler(413)
def _upload_too_large(_e):
    resp = jsonify({'error': '文件超过 35MB 限制'})
    _set_cors(resp)
    return resp, 413


@app.route('/api/upload/<child>', methods=['POST', 'OPTIONS'])
def upload_api(child):
    """上传作业/报告（multipart）。SHA-256 硬去重，成功后异步进入 OCR 管线。"""
    if request.method == 'OPTIONS':
        return _cors()
    if child not in UPLOAD_CHILDREN:
        return jsonify({'error': f'未知的孩子: {child}'}), 400

    # 频率限制：30 文件/小时/IP
    now = time.time()
    with _upload_lock:
        bucket = _upload_rate.get(request.remote_addr or '?', [])
        bucket = [t for t in bucket if now - t < 3600]
        if len(bucket) >= 30:
            return jsonify({'error': '上传过于频繁（每小时限 30 个文件），请稍后再试'}), 429
        bucket.append(now)
        _upload_rate[request.remote_addr or '?'] = bucket

    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': '缺少文件（multipart 字段名应为 file）'}), 400
    data = f.read()
    if not data:
        return jsonify({'error': '空文件'}), 400

    kind, ext = _sniff_upload(data, f.filename)
    if kind is None:
        return jsonify({'error': ext}), 400
    limit = UPLOAD_MAX_PDF if kind == 'pdf' else UPLOAD_MAX_IMG
    if len(data) > limit:
        return jsonify({'error': f'{"PDF" if kind == "pdf" else "图片"}大小超过限制（{limit // 1024 // 1024}MB）'}), 413

    sha = hashlib.sha256(data).hexdigest()
    force = request.form.get('force') == '1'
    # 硬去重：同 child 同内容直接返回已存在条目
    for e in _load_upload_index():
        if e.get('child') == child and e.get('sha256') == sha:
            if not force:
                return jsonify({'error': '重复上传', 'duplicate_of': e.get('id'),
                                'hint': '文件内容相同，已存在；传 force=1 可强制重新识别'}), 409
            break

    uid = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    d = _upload_dir(child, uid)
    os.makedirs(d, exist_ok=True)
    stored_name = 'original' + ext
    with open(os.path.join(d, stored_name), 'wb') as fp:
        fp.write(data)

    meta = {
        'id': uid, 'child': child,
        'original_name': f.filename,
        'stored_name': stored_name,
        'mime': {'pdf': 'application/pdf'}.get(kind, f'image/{ext[1:]}'),
        'kind': kind, 'size': len(data), 'sha256': sha,
        'doc_type': (request.form.get('doc_type') or '').strip() or None,
        'subject': (request.form.get('subject') or '').strip() or None,
        'status': 'queued', 'retry_count': 0, 'error': None,
        'created': datetime.now().isoformat(timespec='seconds'),
        'updated': datetime.now().isoformat(timespec='seconds'),
    }
    _save_upload_meta(child, uid, meta)
    _index_upsert(child, uid, {
        'id': uid, 'child': child, 'original_name': f.filename, 'sha256': sha,
        'status': 'queued', 'size': len(data), 'page_count': 0,
        'created': meta['created'], 'updated': meta['updated'],
    })
    _upload_pool.submit(_process_upload, child, uid)
    resp = jsonify({'ok': True, 'id': uid, 'status': 'queued'})
    _set_cors(resp)
    return resp, 202


@app.route('/api/uploads/<child>', methods=['GET', 'OPTIONS'])
def uploads_list(child):
    if request.method == 'OPTIONS':
        return _cors()
    if child not in UPLOAD_CHILDREN:
        return jsonify({'error': f'未知的孩子: {child}'}), 400
    q_status = request.args.get('status')
    q_subject = request.args.get('subject')
    entries = [e for e in _load_upload_index() if e.get('child') == child]
    if q_status:
        entries = [e for e in entries if e.get('status') == q_status]
    if q_subject:
        entries = [e for e in entries if e.get('subject') == q_subject]
    entries.sort(key=lambda e: e.get('created', ''), reverse=True)
    return jsonify(entries)


@app.route('/api/uploads/<child>/<uid>', methods=['GET', 'PUT', 'DELETE', 'OPTIONS'])
def upload_detail(child, uid):
    if request.method == 'OPTIONS':
        return _cors()
    if child not in UPLOAD_CHILDREN:
        return jsonify({'error': f'未知的孩子: {child}'}), 400
    if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{6}', uid or ''):
        return jsonify({'error': '非法的 upload id'}), 400
    meta = _load_upload_meta(child, uid)
    if not meta:
        return jsonify({'error': 'not found'}), 404

    if request.method == 'GET':
        # 列表/详情分离：meta 的 raw_text 可截断返回以省带宽（full_text 保留）
        out = dict(meta)
        out.pop('sha256', None)
        return jsonify(out)

    if request.method == 'PUT':
        data = request.get_json(force=True, silent=True) or {}
        action = data.get('action')
        if action == 'retry_ocr':
            # 重置失败页，重新排队
            meta['status'] = 'queued'
            meta['error'] = None
            meta['retry_count'] = meta.get('retry_count', 0) + 1
            meta.pop('extract', None)
            _save_upload_meta(child, uid, meta)
            _upload_pool.submit(_process_upload, child, uid)
            return jsonify({'ok': True, 'status': 'queued', 'retry_count': meta['retry_count']})
        if action == 'retry_extract':
            if not meta.get('full_text'):
                return jsonify({'error': '尚无 OCR 文本，无法抽取'}), 400
            ext = _extract_upload_fields(meta['full_text'], child)
            if ext:
                meta['extract'] = ext
                meta['extract_status'] = 'done'
            else:
                meta['extract_status'] = 'failed'
            _save_upload_meta(child, uid, meta)
            return jsonify({'ok': bool(ext), 'extract_status': meta['extract_status']})
        # 手工修正字段
        extract = meta.setdefault('extract', {})
        for k in ('subject', 'doc_type', 'title', 'date', 'topics'):
            if k in data:
                extract[k] = data[k]
        if 'score' in data:
            extract['score'] = data['score']
        if extract:
            meta['extract_status'] = 'manual'
        _save_upload_meta(child, uid, meta)
        e = meta.get('extract') or {}
        _index_upsert(child, uid, {
            'id': uid, 'child': child,
            'subject': e.get('subject') or 'Other',
            'doc_type': e.get('doc_type') or 'homework',
            'title': e.get('title') or meta.get('original_name', ''),
            'date': e.get('date') or meta.get('created', '')[:10],
            'score': e.get('score'), 'topics': e.get('topics') or [],
            'updated': meta['updated'],
        })
        return jsonify({'ok': True})

    # DELETE：删目录 + 索引
    import shutil as _shutil
    try:
        _shutil.rmtree(_upload_dir(child, uid), ignore_errors=True)
    except Exception:
        pass
    _index_remove(child, uid)
    return jsonify({'ok': True})


_UPLOAD_FILE_RE = re.compile(r'^(original\.(pdf|png|jpe?g|webp)|page-\d{2}\.png)$')

@app.route('/api/uploads/<child>/<uid>/file/<name>', methods=['GET', 'OPTIONS'])
def upload_file(child, uid, name):
    """页图/原文件预览。文件名白名单 + 目录限制，防路径穿越。"""
    if request.method == 'OPTIONS':
        return _cors()
    if child not in UPLOAD_CHILDREN:
        return jsonify({'error': f'未知的孩子: {child}'}), 400
    if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{6}', uid or '') or not _UPLOAD_FILE_RE.fullmatch(name or ''):
        return jsonify({'error': '非法请求'}), 400
    path = os.path.join(_upload_dir(child, uid), name)
    if not os.path.isfile(path):
        return jsonify({'error': 'not found'}), 404
    mimes = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg',
             '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
    mime = mimes.get(os.path.splitext(name)[1], 'application/octet-stream')
    from flask import send_file
    return send_file(path, mimetype=mime)


@app.route('/api/uploads/<child>/<uid>/to-errors', methods=['POST', 'OPTIONS'])
def upload_to_errors(child, uid):
    """把抽取的 mistakes[] 批量转为错题本条目（复用 errors_<child>.json 结构）。"""
    if request.method == 'OPTIONS':
        return _cors()
    if child not in UPLOAD_CHILDREN:
        return jsonify({'error': f'未知的孩子: {child}'}), 400
    meta = _load_upload_meta(child, uid)
    if not meta:
        return jsonify({'error': 'not found'}), 404
    mistakes = ((meta.get('extract') or {}).get('mistakes')) or []
    if not mistakes:
        return jsonify({'ok': True, 'added': 0, 'note': '无错题可导入'})

    err_file = os.path.join(DATA_DIR, f'errors_{child}.json')
    errs = safe_load(err_file, default=[])
    existing = {e.get('id') for e in errs}
    added = 0
    title = (meta.get('extract') or {}).get('title') or meta.get('original_name', '')
    subject = (meta.get('extract') or {}).get('subject') or 'Other'
    for i, m in enumerate(mistakes):
        eid = f'upload-{uid}-{i + 1}'
        if eid in existing:
            continue
        qn = str(m.get('question_no') or f'Q{i + 1}')
        errs.append({
            'id': eid,
            'subject': subject,
            'question': f'{qn}（{title}）',
            'original_question': qn,
            f'{child}_answer': str(m.get('wrong_answer') or ''),
            'diya_answer': str(m.get('wrong_answer') or ''),   # 前端 app.html 读 diya_answer
            'score': '',
            'strengths': '',
            'weaknesses': str(m.get('reason') or ''),
            'correct_solution': str(m.get('correct_answer') or ''),
            'error_type': 'homework',
            'priority': 'p1',
            'tags': (meta.get('extract') or {}).get('topics') or [],
            'type': 'upload',
            'source': f'上传识别：{meta.get("original_name", "")}',
            'date': meta.get('created', ''),
            'enriched': False,
        })
        added += 1
    atomic_write(err_file, errs, indent=2)
    return jsonify({'ok': True, 'added': added, 'total_errors': len(errs)})


# ── Homework API ──────────────────────────────────────────────────

HW_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'homework_entries.json')

def _load_hw():
    if not os.path.exists(HW_DATA_FILE):
        return []
    with open(HW_DATA_FILE) as f:
        return json.load(f)

def _save_hw(entries):
    os.makedirs(os.path.dirname(HW_DATA_FILE), exist_ok=True)
    atomic_write(HW_DATA_FILE, entries, indent=2, ensure_ascii=False)

@app.route('/api/homework', methods=['GET', 'OPTIONS'])
def api_homework_list():
    if request.method == 'OPTIONS': return _cors()
    entries = _load_hw()
    return jsonify(entries)

@app.route('/api/homework/<entry_id>/review', methods=['POST', 'OPTIONS'])
def api_homework_review(entry_id):
    if request.method == 'OPTIONS': return _cors()
    entries = _load_hw()
    for e in entries:
        if str(e.get('id')) == str(entry_id):
            e['status'] = 'reviewed'
            _save_hw(entries)
            return jsonify({'ok': True, 'entry': e})
    return jsonify({'ok': False, 'error': 'Entry not found'}), 404

@app.route('/api/homework/<entry_id>', methods=['PUT', 'OPTIONS'])
def api_homework_update(entry_id):
    if request.method == 'OPTIONS': return _cors()
    data = request.get_json(silent=True) or {}
    entries = _load_hw()
    for e in entries:
        if str(e.get('id')) == str(entry_id):
            if 'status' in data: e['status'] = data['status']
            if 'grade' in data: e['grade'] = data['grade']
            if 'feedback' in data: e['feedback'] = data['feedback']
            _save_hw(entries)
            return jsonify({'ok': True, 'entry': e})
    return jsonify({'ok': False, 'error': 'Entry not found'}), 404



# ── 学习目标工作台 · 服务器数据 API（2026-10-20）──────────────────────────
# 背景：目标台页面有两种部署形态——
#   ① 资料库页（window.__SMART_PAGE__.database）→ 数据落在资料库数据表
#   ② 服务器独立目录（/goals/）→ 没有 SDK，改走本组端点，数据落服务器 data/goals.json
# 两形态的数据结构完全一致（goals / checks / stages），页面按运行时选择后端。
# 认证：全部在 /api/ 前缀下，自动复用 _auth_gate 会话校验（未登录返回 401）。
# 写操作遵循与主页一致的约定：atomic_write + 失效 _json_cache。

_GOALS_FILE = 'goals.json'

# Diya 与 Jemma 共用一个 goals.json，但数据必须隔离（两个孩子不能看到对方的目标）。
# 维度取自请求：?child=jemma 或请求体 child 字段；未传时默认 diya（保持既有行为不变）。
DEFAULT_GOAL_CHILD = 'diya'
_VALID_GOAL_CHILDREN = ('diya', 'jemma')


def _goals_path():
    return os.path.join(DATA_DIR, _GOALS_FILE)


def _goal_child(payload=None):
    """判定本次请求属于哪个孩子。优先级：请求体 > query > 默认 diya。"""
    raw = ''
    if isinstance(payload, dict):
        raw = str(payload.get('child') or '').strip().lower()
    if not raw:
        raw = str(request.args.get('child') or '').strip().lower()
    return raw if raw in _VALID_GOAL_CHILDREN else DEFAULT_GOAL_CHILD


def _goal_belongs(rec, child):
    """记录的归属判定：新数据一律带 child 字段；历史无 child 的数据归属 diya（不丢数据）。"""
    owner = str(rec.get('child') or '').strip().lower()
    if not owner:
        owner = DEFAULT_GOAL_CHILD
    return owner == child


def _read_goals():
    data = _cached_json_read(_goals_path(), ttl=5)
    if not isinstance(data, dict):
        data = {}
    data.setdefault('goals', [])
    data.setdefault('checks', [])
    data.setdefault('stages', [])
    return data


def _goals_of(data, child):
    """按孩子过滤出 goals / checks / stages 三段（前端拿到的结构与原来完全一致）。"""
    return {
        'child': child,
        'goals': [g for g in data['goals'] if _goal_belongs(g, child)],
        'checks': [c for c in data['checks'] if _goal_belongs(c, child)],
        'stages': [st for st in data['stages'] if _goal_belongs(st, child)],
    }


def _write_goals(data):
    p = _goals_path()
    atomic_write(p, data, ensure_ascii=False, indent=2)
    _json_cache.pop(p, None)


@app.route('/api/goals', methods=['GET', 'OPTIONS'])
def goals_api():
    """读取全部目标 / 打卡 / 阶段（页面首屏一次性拉取，体量小无需分页）。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    data = _read_goals()
    payload = _goals_of(data, _goal_child())
    _set_cors(jsonify(payload))
    return jsonify(payload)


@app.route('/api/goals/goal', methods=['POST', 'OPTIONS'])
def goals_create():
    """新建目标（同时可选带首段阶段）。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    body = request.get_json(force=True, silent=True) or {}
    goal = body.get('goal') or {}
    if not goal.get('目标ID'):
        resp = jsonify({'ok': False, 'error': 'missing 目标ID'})
        _set_cors(resp)
        return resp, 400
    data = _read_goals()
    child = _goal_child(body)
    goal.setdefault('record_id', uuid.uuid4().hex[:12])
    goal['child'] = child
    data['goals'].append(goal)
    for st in (body.get('stages') or []):
        st.setdefault('record_id', uuid.uuid4().hex[:12])
        st['child'] = child
        data['stages'].append(st)
    _write_goals(data)
    resp = jsonify({'ok': True, 'id': goal['record_id'], 'stages': len(body.get('stages') or [])})
    _set_cors(resp)
    return resp


@app.route('/api/goals/goal/<gid>', methods=['PUT', 'OPTIONS'])
def goals_update(gid):
    """增量更新目标字段（完成量、连续天数、状态、节奏提示等）。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'PUT, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    patch = request.get_json(force=True, silent=True) or {}
    child = _goal_child(patch)
    data = _read_goals()
    hit = None
    for g in data['goals']:
        if not _goal_belongs(g, child):
            continue
        if g.get('record_id') == gid or g.get('目标ID') == gid:
            for k, v in patch.items():
                g[k] = v
            hit = g
            break
    if not hit:
        resp = jsonify({'ok': False, 'error': 'not found'})
        _set_cors(resp)
        return resp, 404
    _write_goals(data)
    resp = jsonify({'ok': True})
    _set_cors(resp)
    return resp


@app.route('/api/goals/checkin', methods=['POST', 'OPTIONS'])
def goals_checkin():
    """写入一条打卡记录，并同步累计量/投入/连续天数到目标（服务端一次事务完成）。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    body = request.get_json(force=True, silent=True) or {}
    check = body.get('check') or {}
    patch = body.get('goal_patch') or {}
    if not check.get('记录ID'):
        resp = jsonify({'ok': False, 'error': 'missing 记录ID'})
        _set_cors(resp)
        return resp, 400
    data = _read_goals()
    child = _goal_child(body)
    check.setdefault('record_id', uuid.uuid4().hex[:12])
    check['child'] = child
    data['checks'].append(check)
    gid = check.get('目标ID')
    for g in data['goals']:
        if not _goal_belongs(g, child):
            continue
        if g.get('目标ID') == gid or g.get('record_id') == gid:
            for k, v in patch.items():
                g[k] = v
            break
    _write_goals(data)
    resp = jsonify({'ok': True, 'id': check['record_id']})
    _set_cors(resp)
    return resp


@app.route('/api/goals/stage', methods=['POST', 'OPTIONS'])
def goals_stage():
    """新增阶段记录。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    body = request.get_json(force=True) or {}
    st = body.get('stage') or body
    child = _goal_child(body)
    data = _read_goals()
    st.setdefault('record_id', uuid.uuid4().hex[:12])
    st['child'] = child
    data['stages'].append(st)
    _write_goals(data)
    resp = jsonify({'ok': True, 'id': st['record_id']})
    _set_cors(resp)
    return resp




# ── 目标台 · 示例数据播种 + AI 自然语言改单（2026-10-20）───────────────
# 1) seed：目标表为空时写入 data-seed/goals_seed.json（Diya 目标清单），
#    非空绝不覆盖——孩子的真实数据优先。
# 2) ai：前端把「自然语言改版建议 + 当前目标快照」提交上来，服务端调用已配置的 LLM，
#    要求模型只返回 JSON 操作指令，解析后按白名单落库，再把结果回传前端实时回显。

_GOAL_WRITE_FIELDS = (
    '目标名称', '单位', '目标总量', '已完成量', '开始日期', '截止日期', '状态',
    '休息日', '休息额度', '拆解计划', '障碍预案', '领域', '模板', '颜色',
    '每日上限', '今日建议量', '连续天数', '投入分钟', '节奏提示', '收口方式',
)
_GOAL_OPS = ('create_goal', 'update_goal', 'delete_goal', 'create_stage', 'create_checkin')


def _seed_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data-seed', 'goals_seed.json')


@app.route('/api/goals/seed', methods=['POST', 'OPTIONS'])
def goals_seed():
    """空表时灌入示例目标；已有数据时拒绝（保护真实数据）。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    body = request.get_json(force=True, silent=True) or {}
    child = _goal_child(body)
    data = _read_goals()
    mine = _goals_of(data, child)
    if mine['goals'] and not body.get('keep'):
        resp = jsonify({'ok': False, 'reason': 'already_has_goals', 'count': len(mine['goals'])})
        _set_cors(resp)
        return resp
    sp = _seed_path()
    if not os.path.exists(sp):
        resp = jsonify({'ok': False, 'error': 'seed file missing'})
        _set_cors(resp)
        return resp, 500
    with open(sp, 'r', encoding='utf-8') as f:
        seed = json.load(f)
    # 播种只覆盖**该孩子**自己的记录，另一位孩子的数据原样保留
    other = [g for g in data['goals'] if not _goal_belongs(g, child)]
    other_st = [st for st in data['stages'] if not _goal_belongs(st, child)]
    new_goals = seed.get('goals', [])
    new_stages = seed.get('stages', [])
    for g in new_goals:
        g.setdefault('child', child)
    for st in new_stages:
        st.setdefault('child', child)
    data['goals'] = other + new_goals
    data['stages'] = other_st + new_stages
    data.setdefault('checks', [])
    _write_goals(data)
    resp = jsonify({'ok': True, 'goals': len(new_goals), 'stages': len(new_stages)})
    _set_cors(resp)
    return resp


def _goal_brief(g):
    """给 LLM 的紧凑目标描述（避免把整表塞进 prompt）。"""
    return {
        'id': g.get('目标ID'),
        'name': g.get('目标名称'),
        'unit': g.get('单位'),
        'total': g.get('目标总量'),
        'done': g.get('已完成量'),
        'end': str(g.get('截止日期') or '')[:10],
        'status': g.get('状态'),
        'subject': g.get('领域'),
        'dailyCap': g.get('每日上限'),
    }


def _ai_prompt(goals, stages, message):
    today = datetime.now().strftime('%Y-%m-%d')
    schema = (
        '{"reply":"1-3 short English sentences telling the user what you did",'
        '"operations":[{"op":"create_goal","goal":{"目标名称":"Daily English vocab","单位":"words","目标总量":300,"截止日期":"2026-11-30","每日上限":20,"领域":"fldEn","状态":"stDoing","休息日":"restSun"}},'
        '{"op":"update_goal","target":"goal ID or name keyword","patch":{"每日上限":20}},'
        '{"op":"create_stage","target":"goal name keyword","stage":{"阶段名称":"Stage 1","阶段目标量":24}},'
        '{"op":"create_checkin","target":"goal name keyword","check":{"本次完成量":8}}]}'
    )
    return f"""You are the goal coach on a child's learning goal desk. Today is {today}.

The user (a child) will ask for changes to their learning goals in natural language — for example: regroup goals by subject, add a daily review task, push a deadline back, raise a daily cap to 20.
Translate the request into concrete changes to the goal data.

Current goals (JSON):
{json.dumps([_goal_brief(g) for g in goals], ensure_ascii=False)}

Current stages:
{json.dumps([{'id': s.get('阶段ID'), 'goal': s.get('目标ID'), 'name': s.get('阶段名称'), 'qty': s.get('阶段目标量')} for s in stages], ensure_ascii=False)}

Writable fields — these JSON keys are database identifiers and MUST be used exactly as written (never translate the keys):
目标名称=name, 单位=unit, 目标总量=total amount, 已完成量=amount done, 开始日期=start date, 截止日期=deadline, 状态=status, 休息日=rest day, 休息额度=rest credits, 拆解计划=breakdown plan, 障碍预案=backup plan, 领域=subject, 模板=template, 颜色=color, 每日上限=daily cap, 今日建议量=today's suggested amount, 连续天数=streak days, 投入分钟=minutes invested, 节奏提示=rhythm hint, 收口方式=wrap-up style

Select fields MUST use these stable ids (never the display words, never Chinese values):
状态 status: stTodo(to start) / stDoing(in progress) / stBlock(blocked) / stDone(completed) / stGive(given up, archived)
领域 subject: fldEn(English) / fldMa(Maths) / fldRead(Reading) / fldCode(Coding) / fldSci(Science) / fldSpo(Sport) / fldOther(Other)
休息日 rest day: restMon / restTue / restWed / restThu / restFri / restSat / restSun / restNone
模板 template: tplWord / tplBook / tplCourse / tplSport / tplDrill / tplCustom
收口方式 wrap-up: endNone / endDone / endDelay / endDrop
阶段状态 stage status: msTodo / msDoing / msDone
打卡类型 check-in type: ckNormal / ckMakeup / ckRest
当时状态 how it felt: stSmooth / stHard / stStuck

Output ONLY one JSON object — no explanations, no Markdown code fences:
{schema}

Rules:
1. operations[].op can only be create_goal / update_goal / create_stage / create_checkin. There is NO delete operation — never delete a goal; to retire one, use update_goal with 状态="stGive" (archive, keeps the data).
2. update_goal: patch contains only the fields to change; leave everything else untouched.
3. "Regroup by subject" → update_goal with patch.领域 for each goal.
4. create_goal REQUIRES all of: 目标名称, 单位, 目标总量, 截止日期, 每日上限 (plus 领域 and 状态). If the user's message does not provide enough information to fill every required field, DO NOT create the goal — say in the reply what is still missing. Never invent defaults like "today+30 days" or "total/30".
5. create_checkin: 本次完成量 must be greater than 0 and must not exceed that goal's 每日上限; if the user asks for more, log at most the daily cap and say so.
6. If unsure, change nothing and explain in the reply what needs clarifying.
7. All dates are YYYY-MM-DD strings.
8. Reply in English, warm and brief — you are talking to a child. You help the child run THEIR goals: when creating a goal, base every value on what the child actually said, and list the values you chose so the child can correct them."""


def _parse_ai_json(text):
    """从模型输出里抽出 JSON（容忍 ```json 包裹与前后废话）。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith('```'):
        s = s.split('```')[1]
        if s.lower().startswith('json'):
            s = s[4:]
    s = s.strip().strip('`').strip()
    start, end = s.find('{'), s.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except Exception:
        return None


def _match_goal(goals, target):
    """按目标ID 或名称关键词定位目标。"""
    if not target:
        return None
    t = str(target).strip()
    for g in goals:
        if g.get('目标ID') == t or g.get('record_id') == t:
            return g
    for g in goals:
        if t and t in str(g.get('目标名称', '')):
            return g
    return None


@app.route('/api/goals/ai', methods=['POST', 'OPTIONS'])
def goals_ai():
    """自然语言改单：前端提交 message + 当前数据快照，服务端调用 LLM 解析并落库。"""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    body = request.get_json(force=True, silent=True) or {}
    message = (body.get('message') or '').strip()
    if not message:
        resp = jsonify({'ok': False, 'error': 'empty message'})
        _set_cors(resp)
        return resp, 400
    if len(message) > 2000:
        message = message[:2000]

    child = _goal_child(body)
    data = _read_goals()
    mine = _goals_of(data, child)
    goals, stages, checks = mine['goals'], mine['stages'], mine['checks']
    cfg = _load_config()
    if not cfg.get('api_key'):
        resp = jsonify({'ok': False, 'error': 'llm_not_configured',
                        'reply': 'The server has no LLM configured yet — add the endpoint and key in Settings (LLM) on the site first.'})
        _set_cors(resp)
        return resp, 200

    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{cfg['base_url']}/chat/completions",
                headers={'Authorization': f"Bearer {cfg['api_key']}", 'Content-Type': 'application/json'},
                json={'model': cfg['model'], 'temperature': 0.2, 'max_tokens': 1200,
                      'messages': [
                          {'role': 'system', 'content': _ai_prompt(goals, stages, message)},
                          {'role': 'user', 'content': message}]})
            text = r.json()['choices'][0]['message']['content'] if r.status_code == 200 else ''
            if r.status_code != 200:
                resp = jsonify({'ok': False, 'error': f'llm_{r.status_code}',
                                'reply': f'Model call failed ({r.status_code}). Please try again later.'})
                _set_cors(resp)
                return resp, 200
    except Exception as e:
        resp = jsonify({'ok': False, 'error': 'llm_network', 'reply': f'Model call error: {str(e)[:80]}'})
        _set_cors(resp)
        return resp, 200

    plan = _parse_ai_json(text)
    if not isinstance(plan, dict):
        resp = jsonify({'ok': False, 'error': 'bad_llm_output',
                        'reply': 'The model did not return instructions I could read, so nothing was changed. Try saying it another way.'})
        _set_cors(resp)
        return resp, 200

    applied = []
    notes = []

    def _mk(gid=None):
        return {'record_id': uuid.uuid4().hex[:12], '目标ID': gid or ('g-' + uuid.uuid4().hex[:8])}

    for op in (plan.get('operations') or []):
        if not isinstance(op, dict):
            continue
        kind = op.get('op')
        if kind not in _GOAL_OPS:
            continue
        try:
            if kind == 'create_goal':
                # 严格策略（红队 P0-2）：必填字段缺失即拒绝创建，禁用一切武断兜底值——
                # 旧代码的 today+30 / 总量÷30 / 上限=总量 会把 AI 即兴值当成孩子自己的目标落库
                gsrc = op.get('goal') or {}
                _missing = []
                _name = str(gsrc.get('目标名称') or gsrc.get('name') or '').strip()
                _unit = str(gsrc.get('单位') or gsrc.get('unit') or '').strip()
                try:
                    _total = float(gsrc.get('目标总量') if gsrc.get('目标总量') is not None else gsrc.get('total'))
                except (TypeError, ValueError):
                    _total = 0
                _deadline = str(gsrc.get('截止日期') or '').strip()
                try:
                    _cap = float(gsrc.get('每日上限'))
                except (TypeError, ValueError):
                    _cap = 0
                if not _name:
                    _missing.append('name')
                if not _unit:
                    _missing.append('unit')
                if _total <= 0:
                    _missing.append('total amount')
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', _deadline) or _deadline < datetime.now().strftime('%Y-%m-%d'):
                    _missing.append('deadline (YYYY-MM-DD, not in the past)')
                if _cap <= 0:
                    _missing.append('daily cap')
                if _missing:
                    notes.append('One goal was NOT created — still missing: ' + ', '.join(_missing) + '.')
                    continue
                g = _mk(gsrc.get('目标ID'))
                g['目标名称'] = _name[:60]
                g['单位'] = _unit[:12]
                g['目标总量'] = _total
                g['已完成量'] = float(gsrc.get('已完成量') or 0)
                g['开始日期'] = str(gsrc.get('开始日期') or datetime.now().strftime('%Y-%m-%d'))[:10]
                g['截止日期'] = _deadline
                g['状态'] = gsrc.get('状态') or 'stDoing'
                g['休息日'] = gsrc.get('休息日') or 'restSun'
                g['休息额度'] = int(float(gsrc.get('休息额度') or 1))
                g['领域'] = gsrc.get('领域') or 'fldOther'
                g['模板'] = gsrc.get('模板') or 'tplCustom'
                g['每日上限'] = _cap
                # 今日建议量：LLM 未给时按「剩余天数均摊且不超每日上限」推导，而非总量÷30
                _days = max(1, (datetime.strptime(_deadline, '%Y-%m-%d') - datetime.now()).days + 1)
                g['今日建议量'] = float(gsrc.get('今日建议量') or min(_cap, max(1, round(_total / _days))))
                g['连续天数'] = 0
                g['投入分钟'] = 0
                g['拆解计划'] = str(gsrc.get('拆解计划') or '')[:500]
                g['障碍预案'] = str(gsrc.get('障碍预案') or '')[:500]
                g['颜色'] = gsrc.get('颜色') or '#5b6cff'
                g['节奏提示'] = ''
                g['收口方式'] = gsrc.get('收口方式') or 'endNone'
                g['创建时间'] = datetime.now().isoformat()
                g['最近更新'] = datetime.now().isoformat()
                g['child'] = child
                goals.append(g)
                applied.append({'op': 'create_goal', 'name': g['目标名称'], 'id': g['目标ID']})

            elif kind == 'update_goal':
                g = _match_goal(goals, op.get('target') or op.get('目标ID') or op.get('目标名称'))
                if not g:
                    continue
                patch = op.get('patch') or {}
                changed = []
                for k, v in patch.items():
                    if k in _GOAL_WRITE_FIELDS:
                        g[k] = v
                        changed.append(k)
                if changed:
                    g['最近更新'] = datetime.now().isoformat()
                    applied.append({'op': 'update_goal', 'name': g.get('目标名称'), 'fields': changed})

            elif kind == 'delete_goal':
                # 红队 P0-2：AI 不得硬删除目标——一律转为归档（状态 stGive），数据保留可恢复
                g = _match_goal(goals, op.get('target') or op.get('目标名称'))
                if not g:
                    continue
                gid = g.get('目标ID')
                g['状态'] = 'stGive'
                g['最近更新'] = datetime.now().isoformat()
                applied.append({'op': 'archive_goal', 'name': g.get('目标名称')})

            elif kind == 'create_stage':
                g = _match_goal(goals, op.get('target') or op.get('目标名称'))
                if not g:
                    continue
                s = op.get('stage') or {}
                stages.append({
                    'record_id': uuid.uuid4().hex[:12],
                    '阶段ID': 's-' + uuid.uuid4().hex[:8],
                    '目标ID': g['目标ID'],
                    'child': child,
                    '阶段名称': str(s.get('阶段名称') or 'New stage')[:40],
                    '阶段目标量': float(s.get('阶段目标量') or 1),
                    '阶段截止日': str(s.get('阶段截止日') or g.get('截止日期') or '')[:10],
                    '阶段状态': s.get('阶段状态') or '未开始',
                    '阶段顺序': len([x for x in stages if x.get('目标ID') == g['目标ID']]) + 1,
                })
                applied.append({'op': 'create_stage', 'name': g.get('目标名称'), 'stage': s.get('阶段名称')})

            elif kind == 'create_checkin':
                g = _match_goal(goals, op.get('target') or op.get('目标名称'))
                if not g:
                    continue
                c = op.get('check') or {}
                try:
                    amt = float(c.get('本次完成量') or 0)
                except (TypeError, ValueError):
                    amt = 0
                if amt <= 0:
                    notes.append('One check-in was NOT logged — the amount must be greater than 0.')
                    continue
                cap = float(g.get('每日上限') or 0)
                if cap > 0 and amt > cap:
                    notes.append('Check-in for "' + str(g.get('目标名称') or '') + '" was capped at the daily limit of '
                                 + str(int(cap)) + '.')
                    amt = cap
                new_done = float(g.get('已完成量') or 0) + amt
                total = float(g.get('目标总量') or 0)
                if total > 0:
                    new_done = min(new_done, total)
                date_s = str(c.get('打卡日期') or datetime.now().strftime('%Y-%m-%d'))[:10]
                checks.append({
                    'record_id': uuid.uuid4().hex[:12],
                    '记录ID': 'c-' + uuid.uuid4().hex[:8],
                    '目标ID': g['目标ID'],
                    'child': child,
                    '打卡日期': date_s,
                    '本次完成量': amt,
                    '累计完成量': new_done,
                    '用时分钟': float(c.get('用时分钟') or 0),
                    '打卡类型': c.get('打卡类型') or 'ckNormal',
                    '当时状态': c.get('当时状态') or 'stSmooth',
                    '备注': 'AI helper logged',
                    '提交时间': datetime.now().isoformat(),
                })
                g['已完成量'] = new_done
                g['最近更新'] = datetime.now().isoformat()
                applied.append({'op': 'create_checkin', 'name': g.get('目标名称'), 'amount': amt, 'by_ai': True})
        except Exception:
            continue

    if applied:
        # goals/stages/checks 是按孩子过滤出的新列表，必须合并写回，
        # 否则会整段覆盖掉另一个孩子的数据。
        others = {
            'goals': [g for g in data['goals'] if not _goal_belongs(g, child)],
            'stages': [st for st in data['stages'] if not _goal_belongs(st, child)],
            'checks': [c for c in data['checks'] if not _goal_belongs(c, child)],
        }
        data['goals'] = others['goals'] + goals
        data['stages'] = others['stages'] + stages
        data['checks'] = others['checks'] + checks
        _write_goals(data)
    reply = str(plan.get('reply') or 'Done.')[:800]
    if notes:
        # 服务端跳过的操作必须如实告知，不能只转述模型的 reply
        reply = (reply + ' (' + ' '.join(notes) + ')')[:800]
    resp = jsonify({'ok': True, 'reply': reply,
                    'applied': applied, 'goals': len(goals)})
    _set_cors(resp)
    return resp


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('PORT', 5000)), debug=False)