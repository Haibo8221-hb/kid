#!/usr/bin/env python3
"""Flask API for planner data storage + AI chat + wiki logs"""
import json, os, re, uuid
from datetime import datetime
from flask import Flask, request, jsonify, Response, stream_with_context
import httpx, time

app = Flask(__name__)
DATA_DIR = os.environ.get('DATA_DIR', '/var/www/plan/data')
os.makedirs(DATA_DIR, exist_ok=True)
# P1-4: 聊天上下文窗口上限，避免历史无限增长导致 token 超限
CHAT_MAX_HISTORY = int(os.environ.get('CHAT_MAX_HISTORY', '20'))

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
    os.replace(tmp, path)

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

# ── Planner (cloud sync) ──────────────────────────────────────────
@app.route('/api/planner', methods=['GET', 'POST', 'OPTIONS'])
def planner_api():
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    user = request.args.get('user', 'diya') if request.method == 'GET' else (request.get_json(force=True, silent=True) or {}).get('user', 'diya')
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
    user = data.get('user', request.args.get('user', 'diya'))
    fpath = os.path.join(DATA_DIR, f'tasks_{user}.json')
    if request.method == 'POST':
        atomic_write(fpath, data.get('tasks', {}), ensure_ascii=False)
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
    child = child or 'diya'
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

    plan_info = _load_plan_context(child)
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


def _load_config():
    """Load LLM config from Hermes config.yaml — uses the active provider.
    Falls back to .env.local for API keys if config has masked placeholders."""
    import yaml
    cfg_path = os.path.join(os.environ.get('HERMES_HOME', os.path.expanduser('~/.hermes')), 'config.yaml')
    
    # Load .env.local overrides first
    env_overrides = {}
    env_local = os.path.join(os.path.dirname(__file__), '.env.local')
    if os.path.exists(env_local):
        for line in open(env_local):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env_overrides[k] = v
    
    if os.path.exists(cfg_path):
        try:
            cfg = yaml.safe_load(open(cfg_path))
            active_provider = cfg.get('model', {}).get('provider', 'deepseek')
            provider_cfg = cfg.get('providers', {}).get(active_provider, {})
            api_key = provider_cfg.get('api_key', '')
            # If key is masked (contains '...'), try env override
            if '...' in api_key:
                env_var = f'{active_provider.upper()}_API_KEY'
                if env_var in env_overrides:
                    api_key = env_overrides[env_var]
            return {
                'api_key': api_key,
                'base_url': provider_cfg.get('base_url', 'https://api.deepseek.com'),
                'model': provider_cfg.get('default_model', 'deepseek-v4-pro')
            }
        except Exception:
            pass
    return {'api_key': '', 'base_url': 'https://api.deepseek.com', 'model': 'deepseek-v4-pro'}

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

    if not api_key:
        return '[配置错误] 未找到DeepSeek API Key'

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
                    'temperature': 0.7
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
    child = data.get('user', 'diya')
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
        return jsonify({'error': 'no API key'}), 500

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
                        'temperature': 0.7,
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

    # Load wiki entries
    wiki_path = os.path.join(DATA_DIR, f'wiki_{child}.json')
    wiki_entries = []
    if os.path.exists(wiki_path):
        with open(wiki_path) as f:
            wiki_entries = json.load(f)

    # Load chat messages (last 6)
    chat_path = os.path.join(DATA_DIR, f'chat_{child}.json')
    chat_recent = []
    if os.path.exists(chat_path):
        with open(chat_path) as f:
            messages = json.load(f)
            chat_recent = messages[-6:] if messages else []

    # Load tasks
    tasks_path = os.path.join(DATA_DIR, f'tasks_{child}.json')
    tasks_done = 0
    tasks_total = 0
    if os.path.exists(tasks_path):
        with open(tasks_path) as f:
            tasks = json.load(f)
            tasks_total = len(tasks)
            tasks_done = sum(1 for v in tasks.values() if (v if isinstance(v, bool) else v.get('done', False)))

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
    def _strip(t):
        # Remove all common LLM thinking/reasoning tags and their content
        # MiniMax format: <think>\n<thinking>\n[/think]\n\n<actual response>
        t = re.sub(r'<think>\n[\s\S]*?</think>\n\n', '', t)
        # Handle single newline after [/think]: <think>\n...\n[/think]\n
        t = re.sub(r'<think>\n[\s\S]*?</think>\n', '', t)
        # Handle single-line <think>... or malformed <think>...[/think]
        t = re.sub(r'<think>[\s\S]*?</think>', '', t)
        # Handle Chinese variant [撮要\n...\n[/撮要]\n\n
        t = re.sub(r'\[撮要\n[\s\S]*?\[/撮要\]\n\n', '', t)
        t = re.sub(r'\[撮要\n[\s\S]*?\[/撮要\]\n', '', t)
        t = re.sub(r'\[撮要[^\]]*\][\s\S]*?\]', '', t)
        return t.strip()
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

    user = request.args.get('user') or (request.get_json(silent=True) or {}).get('user', 'diya')
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
    plan_file = os.path.join('/mnt/d/MyAI', f'{child.capitalize()}_Plan.md')
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

    plan_file = os.path.join('/mnt/d/MyAI', f'{child.capitalize()}_Plan.md')
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
    child = data.get('child', 'diya')
    if child not in ('diya', 'jemma'):
        child = 'diya'

    # Support both flat format (email pipeline) and rich format (exam grading)
    entry = data.get('error', None)
    if entry and isinstance(entry, dict):
        err = entry
        err.setdefault('date', datetime.now().isoformat())
        err.setdefault('source', 'Exam Grading')
        err.setdefault('enriched', True)
    else:
        err = {
            'raw': data.get('raw', ''),
            'subject': data.get('subject', ''),
            'type': data.get('type', 'other'),
            'solution': data.get('solution', ''),
            'source': data.get('source', 'Email'),
            'date': datetime.now().isoformat(),
            'enriched': bool(data.get('subject'))
        }

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


@app.route('/api/errors/<child>', methods=['GET', 'OPTIONS'])
def get_errors(child):
    """Get error entries for a child."""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    if child not in ('diya', 'jemma'):
        child = 'diya'

    path = os.path.join(DATA_DIR, f'errors_{child}.json')
    errors = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                errors = json.load(f)
        except Exception:
            errors = safe_load(path, [])
    resp = jsonify({'child': child, 'errors': errors, 'total': len(errors)})
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
@app.route('/api/ocr', methods=['POST', 'OPTIONS'])
def ocr_api():
    """Recognize text from uploaded images. Uses OpenRouter vision models."""
    if request.method == 'OPTIONS':
        resp = jsonify({})
        _set_cors(resp)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    data = request.get_json(force=True, silent=True) or {}
    image_b64 = data.get('image', '')
    child = data.get('child', 'diya')
    prompt = data.get('prompt', 'Transcribe all text in this image word-for-word. Preserve line breaks. Output ONLY the transcription.')

    if not image_b64:
        return jsonify({'error': 'no image data'}), 400

    result = _call_vision_ocr(image_b64, prompt)
    return jsonify(result)


def _call_vision_ocr(image_b64, prompt):
    """Call OpenRouter vision model for OCR. Falls back through models."""
    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        # Try from config
        try:
            import yaml
            cfg = yaml.safe_load(open(os.path.expanduser('~/.hermes/config.yaml')))
            api_key = cfg.get('providers', {}).get('openrouter', {}).get('api_key', '')
        except:
            pass

    if not api_key:
        return {'error': 'OpenRouter API key not configured', 'text': ''}

    # Model fallback chain: best → good → cheap → pure OCR
    models = [
        'baidu/ernie-4.5-vl-424b-a47b',    # 🥇 百度文心VL：最强OCR，1048字符/张
        'bytedance-seed/seed-2.0-lite',    # 🥈 豆包：768字符，稳定
        'meta-llama/llama-4-maverick',     # 🥉 Llama：647字符，便宜
        'baidu/qianfan-ocr-fast',          # 纯OCR兜底
    ]

    last_error = ''
    for model in models:
        try:
            payload = {
                'model': model,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {
                            'url': f'data:image/jpeg;base64,{image_b64}'
                        }}
                    ]
                }],
                'max_tokens': 3000
            }

            import httpx as _httpx
            resp = _httpx.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                    'HTTP-Referer': 'https://xmemory.work',
                    'X-Title': 'Cloud Hermes OCR'
                },
                json=payload,
                timeout=120.0
            )

            if resp.status_code == 200:
                r = resp.json()
                text = r['choices'][0]['message']['content']
                cost = float(r.get('usage', {}).get('cost', 0))
                return {
                    'text': text,
                    'model': model,
                    'cost': cost,
                    'tokens': r.get('usage', {}).get('total_tokens', 0)
                }
            else:
                last_error = f'{model}: {resp.status_code}'
        except Exception as e:
            last_error = f'{model}: {str(e)[:100]}'
            continue

    return {'error': last_error, 'text': ''}


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


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)