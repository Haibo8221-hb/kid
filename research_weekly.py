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
    # 发送邮件（仅 weekly 模式，即每周一自动跑时）
    if os.environ.get('RESEARCH_SEND_EMAIL', '0') == '1':
        send_email_report(result)

def send_email_report(data):
    """发送周报邮件给 Diya + Jemma（精简版：top 3 重点 + 引导访问 dashboard）"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    SMTP_HOST = 'smtp.qq.com'
    SMTP_PORT = 465
    SMTP_USER = os.environ.get('SMTP_USER', 'xgrow@qq.com')
    SMTP_PASS = os.environ.get('SMTP_PASS', '')
    
    if not SMTP_PASS:
        print('[email] SMTP_PASS not set, skipping email')
        return
    
    # 收件人
    recipients = {
        'diya': 'h00426@headingtonschool.com',
        'jemma': 'h00427@headingtonschool.com'
    }
    
    # 筛选每个孩子的 top 3 重点（按关键词优先级）
    def get_top_items(items, n=3):
        """筛选最重要的 n 条（竞赛/截止日 > 考试变化 > 其他）"""
        priority_keywords = ['deadline', 'registration', 'exam date', 'bpho', 'ukmt', 'esat', 'ucas']
        scored = []
        for item in items:
            text = (item['topic'] + ' ' + item['query']).lower()
            score = sum(2 if kw in text else 1 for kw in priority_keywords if kw in text)
            scored.append((score, item))
        scored.sort(reverse=True, key=lambda x: x[0])
        return [item for _, item in scored[:n]]
    
    # 构建 HTML 邮件（精简版）
    html = f"""
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #f8f9fa;">
        <div style="background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h1 style="color: #0d6efd; margin: 0 0 8px 0; font-size: 24px;">
                📊 Weekly Intelligence
            </h1>
            <p style="color: #6c757d; font-size: 13px; margin: 0 0 24px 0;">
                {data['generated_at'][:10]} · Top highlights for you
            </p>
    """
    
    for child in ['diya', 'jemma']:
        items = data.get('items', {}).get(child, [])
        if not items:
            continue
        
        top_items = get_top_items(items, n=3)
        html += f"""
            <div style="margin-bottom: 28px;">
                <h2 style="color: #198754; font-size: 18px; margin: 0 0 12px 0; border-bottom: 2px solid #198754; padding-bottom: 6px;">
                    {child.upper()}'s Highlights
                </h2>
        """
        
        for i, item in enumerate(top_items, 1):
            # 提取第一句话作为摘要（不超过 100 字）
            digest_lines = item['digest'].split('\n')
            summary = digest_lines[0][:100] + ('...' if len(digest_lines[0]) > 100 else '')
            
            html += f"""
                <div style="background: #f8f9fa; border-left: 3px solid #0d6efd; padding: 10px 14px; margin: 10px 0; border-radius: 6px;">
                    <div style="font-weight: 600; color: #212529; font-size: 14px; margin-bottom: 4px;">
                        {i}. {item['topic']}
                    </div>
                    <div style="color: #495057; font-size: 13px; line-height: 1.5;">
                        {summary}
                    </div>
                </div>
            """
        
        html += "</div>"
    
    html += """
            <div style="text-align: center; margin-top: 32px; padding-top: 20px; border-top: 1px solid #dee2e6;">
                <a href="https://xgrow.me/research-weekly.html" 
                   style="display: inline-block; background: #0d6efd; color: white; padding: 12px 28px; 
                          text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    View Full Dashboard →
                </a>
                <p style="color: #6c757d; font-size: 12px; margin-top: 12px;">
                    Complete analysis, timeline, and resources on your dashboard
                </p>
            </div>
        </div>
        <p style="text-align: center; color: #adb5bd; font-size: 11px; margin-top: 16px;">
            xgrow.me · Weekly Admissions Intelligence
        </p>
    </body>
    </html>
    """
    
    # 发送
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = SMTP_USER
        msg['Subject'] = f"📊 Weekly Intelligence - {data['generated_at'][:10]} (Top Highlights)"
        msg.attach(MIMEText(html, 'html'))
        
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
        server.login(SMTP_USER, SMTP_PASS)
        
        for child, email in recipients.items():
            msg['To'] = email
            server.send_message(msg)
            print(f'[email] Sent to {child} ({email})')
            del msg['To']
        
        server.quit()
        print('[email] All emails sent successfully')
    except Exception as e:
        print(f'[email] Failed: {str(e)}')

if __name__ == '__main__':
    main()
