#!/usr/bin/env python3
"""入学情报周报生成器（2026-11-10）
- 读 data/research_queries.json（按孩子分组的查询列表）
- 每条 query 调百炼 EnhancedSearch MCP 搜索
- 用 LLM（主+fallback 链）生成 digest
- 输出 data/research_weekly.json + research-weekly.html
- 可手动跑：python3 research_weekly.py
- systemd timer 每周一 16:00 CST（08:00 UK）自动跑
"""
import os, sys, json, time, httpx
from datetime import datetime

# 环境：从 /etc/plan-api.env 读 key（systemd 跑时自动加载，手动跑需 source 或显式读）
def load_env():
    try:
        for line in open('/etc/plan-api.env'):
            line=line.strip()
            if '=' in line and not line.startswith('#'):
                k,v=line.split('=',1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception: pass
load_env()

QWEN_MCP_KEY = os.environ.get('QWEN_MCP_KEY', '')
MCP_URL = 'https://dashscope.aliyuncs.com/api/v1/mcps/EnhancedSearch/mcp'
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def mcp_search(query, timeout=30.0, limit=3500):
    """调百炼联网搜索增强版。失败返回 ''。"""
    query = (query or '').strip()[:200]
    if not query or not QWEN_MCP_KEY: return ''
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(MCP_URL, headers={
                'Authorization': f'Bearer {QWEN_MCP_KEY}',
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
            }, json={'jsonrpc':'2.0','id':1,'method':'tools/call',
                     'params':{'name':'search_pro','arguments':{'query':query}}})
        if r.status_code != 200: return ''
        txt = r.text
        if 'data:' in txt[:40]:
            for line in txt.split('\n'):
                if line.startswith('data:'): txt = line[5:].strip(); break
        d = json.loads(txt)
        blocks = d.get('result',{}).get('content',[])
        text = ' '.join(b.get('text','') for b in blocks if isinstance(b,dict))
        return text[:limit]
    except Exception as e:
        print(f'[mcp_search] failed: {str(e)[:80]}')
        return ''

def load_llm_providers():
    """复用 api_server 的供应商链逻辑（简化版：读 llm_config.json + llm_fallback.json）。"""
    provs = []
    try:
        cfg = json.load(open(os.path.join(DATA_DIR, 'llm_config.json')))
        if cfg.get('api_key'):
            provs.append({'base_url': cfg.get('endpoint') or cfg.get('base_url'),
                          'api_key': cfg['api_key'], 'model': cfg.get('model'), 'label': 'primary'})
    except Exception: pass
    try:
        fb = json.load(open(os.path.join(DATA_DIR, 'llm_fallback.json')))
        for p in (fb if isinstance(fb, list) else fb.get('providers', [])):
            if isinstance(p, dict) and p.get('api_key') and (p.get('base_url') or p.get('endpoint')) and p.get('model'):
                provs.append({'base_url': p.get('base_url') or p.get('endpoint'),
                              'api_key': p['api_key'], 'model': p['model'], 'label': p.get('label','fallback')})
    except Exception: pass
    return provs

def llm_complete(messages, timeout=90.0):
    """供应商链非流式补全。返回文本或 None。"""
    for p in load_llm_providers():
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(f"{p['base_url'].rstrip('/')}/chat/completions",
                           headers={'Authorization': f"Bearer {p['api_key']}", 'Content-Type': 'application/json'},
                           json={'model': p['model'], 'messages': messages, 'max_tokens': 2000, 'temperature': 0.5})
            if r.status_code == 200:
                content = (r.json().get('choices') or [{}])[0].get('message',{}).get('content','') or ''
                if content.strip(): return content
        except Exception as e:
            print(f"[llm] {p['label']} failed: {str(e)[:80]}")
    return None

def generate_digest(child, topic, query):
    """搜索 + LLM 摘要。返回 {topic, query, digest, sources}。"""
    print(f'  [{child}] searching: {query}')
    search_text = mcp_search(query)
    if not search_text:
        return {'topic': topic, 'query': query, 'digest': 'Search failed', 'sources': ''}
    prompt = f"""You are generating a weekly admissions intelligence digest for a UK student ({child}).
Topic: {topic}
Query: {query}

Web search results (live data):
{search_text}

Task: Summarize the KEY FACTS and DEADLINES (next 60 days) relevant to {child}. Use bullet points. Be concise (max 200 words). Cite source names when possible. If nothing new/changed, say "No significant updates this week."
Output in English."""
    digest = llm_complete([{'role':'system','content':'You are a concise research assistant.'},
                           {'role':'user','content': prompt}])
    if not digest:
        digest = f"LLM summarization failed. Raw search text (first 500 chars): {search_text[:500]}..."
    return {'topic': topic, 'query': query, 'digest': digest.strip(), 'sources': search_text[:200]}

def main():
    queries_file = os.path.join(DATA_DIR, 'research_queries.json')
    if not os.path.exists(queries_file):
        print(f'ERROR: {queries_file} not found. Create it first.')
        sys.exit(1)
    queries = json.load(open(queries_file))  # {diya: [{topic, query}], jemma: [...]}
    print(f'[{datetime.now().isoformat()}] Starting weekly research digest...')
    result = {'generated_at': datetime.now().isoformat(), 'items': {}}
    for child, qs in queries.items():
        print(f'\n=== {child.upper()} ===')
        result['items'][child] = []
        for q in qs:
            item = generate_digest(child, q['topic'], q['query'])
            result['items'][child].append(item)
            print(f'  -> digest: {item["digest"][:80]}...')
    # 写 JSON
    out_json = os.path.join(DATA_DIR, 'research_weekly.json')
    json.dump(result, open(out_json, 'w'), ensure_ascii=False, indent=2)
    print(f'\nWrote {out_json}')
    # 写 HTML（静态页面，fetch /api/research/weekly）
    out_html = os.path.join(os.path.dirname(__file__), 'research-weekly.html')
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Weekly Admissions Intelligence</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 900px; margin: 40px auto; padding: 20px; background: #f8f9fa; color: #212529; }}
h1 {{ color: #0d6efd; border-bottom: 2px solid #0d6efd; padding-bottom: 10px; }}
h2 {{ color: #198754; margin-top: 30px; }}
.item {{ background: white; border-radius: 8px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.topic {{ font-weight: 600; color: #0d6efd; font-size: 14px; }}
.digest {{ margin-top: 8px; line-height: 1.6; white-space: pre-wrap; }}
.meta {{ font-size: 11px; color: #6c757d; margin-top: 8px; }}
</style></head><body>
<h1>Weekly Admissions Intelligence</h1>
<div id="content">Loading...</div>
<script>
fetch('/api/research/weekly').then(r=>r.json()).then(d=>{{
  let h='<div class="meta">Generated: '+d.generated_at+'</div>';
  for(const child in d.items){{
    h+='<h2>'+child.toUpperCase()+'</h2>';
    d.items[child].forEach(item=>{{
      h+='<div class="item"><div class="topic">'+item.topic+'</div><div class="digest">'+item.digest+'</div></div>';
    }});
  }}
  document.getElementById('content').innerHTML=h;
}}).catch(e=>document.getElementById('content').innerHTML='Error loading: '+e);
</script></body></html>"""
    open(out_html,'w').write(html)
    print(f'Wrote {out_html}')

if __name__ == '__main__':
    main()
