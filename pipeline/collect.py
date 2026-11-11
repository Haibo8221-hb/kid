#!/usr/bin/env python3
"""L1 采集 + L2 判定（每 30 分钟，systemd kid-collect.timer）

L1：对 research_queries.json 的每条 query 调百炼 MCP 搜索；
    文本指纹（sha256前16位）与上次一致 → 跳过；不一致 → 原文追加 research_items.jsonl。
    不直接改任何仪表盘文件。
L2：变更文本走规则打分（0-10）：
    - ≥7  → events_pending.json（高优事件；含决策关键词 → requires_human=true，人工闸门）
    - ≥3  → 仅对该 topic 重生成 LLM digest，合并进 research_weekly.json（前端 intel 卡消费）
    - <3  → 仅归档 changes_log.jsonl
    全部变更记 changes_log.jsonl（L3 双周刷新消费）。
守卫：距上次成功 <25min 跳过（防双触发）；单 query 连续失败计数 → source_health 消费。
"""
import os, sys, json, re, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (DATA_DIR, REFRESH_DIR, log, now_iso, atomic_write_json, append_jsonl,
                    read_json, fingerprint, mcp_search, llm_complete, run_logged, write_alert)

QUERIES_FILE = os.path.join(DATA_DIR, 'research_queries.json')
ITEMS_FILE = os.path.join(REFRESH_DIR, 'research_items.jsonl')
CHANGES_FILE = os.path.join(REFRESH_DIR, 'changes_log.jsonl')
EVENTS_FILE = os.path.join(REFRESH_DIR, 'events_pending.json')
STATE_FILE = os.path.join(REFRESH_DIR, 'collect_state.json')
WEEKLY_FILE = os.path.join(DATA_DIR, 'research_weekly.json')

# ── L2 规则打分 ──
HIGH_KW = ['deadline', 'registration', 'entry requirement', 'requirement', 'specification change',
           'ucas', 'esat', 'tmua', 'step ', 'bpho', 'ukmt', ' smc', 'imc', 'bebras', 'olympiad',
           'offer', 'policy', 'cancelled', 'canceled', 'postponed', 'new date', 'tuition', 'fee',
           'visa', 'shortage list', 'exam board', 'aqa', 'edexcel', 'ocr ', 'grade boundary']
DECISION_KW = ['a-level', 'a level', 'entry requirement', 'subject requirement', 'course change',
               'discontinue', 'ucas deadline', 'esat', 'tmua', 'grade boundary', 'specification change',
               'registration deadline', 'exam date']
ENTITY_KW = ['imperial', 'cambridge', 'oxford', 'ucl', 'hkust', 'edinburgh', 'headington',
             'loughborough', 'warwick', 'manchester', 'kcl', 'rwth', 'tu munich', 'tu berlin']
LOW_KW = ['ranking', 'league table', 'blog', 'opinion', 'podcast', 'review of the week']
DATE_PAT = re.compile(r'\b(20[23][0-9])\b|\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+20[23][0-9]\b', re.I)


def judge_score(text, topic=''):
    t = (text or '').lower()
    topic_l = (topic or '').lower()
    score = 1  # 有变更即 1 分起步
    hits = sum(1 for k in HIGH_KW if k in t)
    score += min(hits, 3) * 2                      # 高优关键词，最多 +6
    if any(k in t for k in ENTITY_KW):
        score += 1
    if DATE_PAT.search(text or ''):
        score += 1
    score -= sum(1 for k in LOW_KW if k in t)      # 排名/新闻类降分
    return max(0, min(10, score))


def requires_human(text, importance):
    t = (text or '').lower()
    return importance >= 8 or any(k in t for k in DECISION_KW)


def clean_excerpt(text, limit=600):
    """MCP 搜索返回可能是 {"pages":[{"snippet":...}]} 结构——抽取人类可读正文再入事件队列。"""
    t = (text or '').strip()
    if t.startswith('{') or t.startswith('['):
        try:
            d = json.loads(t)
            parts = []
            def walk(o):
                if isinstance(o, dict):
                    for k in ('title', 'snippet', 'content', 'text'):
                        v = o.get(k)
                        if isinstance(v, str) and v.strip():
                            parts.append(v.strip())
                    for v in o.values():
                        if isinstance(v, (dict, list)):
                            walk(v)
                elif isinstance(o, list):
                    for x in o:
                        walk(x)
            walk(d)
            if parts:
                t = ' | '.join(dict.fromkeys(parts))  # 去重保序
        except Exception:
            pass
    return t[:limit]


def add_event(ev):
    """事件去重（同 child+topic+fp 不重复入队）。返回是否新增。"""
    events = read_json(EVENTS_FILE, []) or []
    for e in events:
        if e.get('fp') == ev['fp'] and e.get('child') == ev['child'] and e.get('topic') == ev['topic']:
            return False
    events.append(ev)
    events = events[-500:]  # 只留最近500条
    atomic_write_json(EVENTS_FILE, events)
    return True


def update_digest(child, topic, query, text, importance):
    """仅对 importance>=3 的变更 topic 重生成 digest，合并进 research_weekly.json。"""
    prompt = (f'You are an admissions-intel analyst for a UK-educated student ({child}). '
              f'Topic: {topic}. Query: {query}\n\nLatest search results:\n{text[:3000]}\n\n'
              'Write a concise digest (max 180 words) of what matters for this family: '
              'hard facts first (dates, requirements, scores), then implications. '
              'Mark uncertain items "待核实". Plain text, no markdown headers.')
    digest = llm_complete([{'role': 'user', 'content': prompt}], max_tokens=600)
    if not digest:
        log(f'[digest] LLM failed for {child}/{topic} — keeping old digest')
        return
    weekly = read_json(WEEKLY_FILE, None)
    if not isinstance(weekly, dict) or 'items' not in weekly:
        log('[digest] research_weekly.json missing/malformed — skip merge (weekly full run will rebuild)')
        return
    items = weekly['items'].setdefault(child, [])
    for it in items:
        if it.get('topic') == topic:
            it['digest'] = digest
            it['topic_updated_at'] = now_iso()
            it['importance'] = importance
            break
    else:
        items.append({'topic': topic, 'query': query, 'digest': digest,
                      'topic_updated_at': now_iso(), 'importance': importance})
    weekly['generated_at'] = now_iso()
    atomic_write_json(WEEKLY_FILE, weekly)
    log(f'[digest] updated {child}/{topic} (importance={importance})')


def collect_once():
    # 守卫：25min 内不重复跑
    state = read_json(STATE_FILE, {}) or {}
    per_q = state.setdefault('per_query', {})
    last_ok = state.get('collect', {}).get('last_success')
    if last_ok and os.environ.get('FORCE') != '1':
        try:
            from datetime import datetime
            age = (datetime.now() - datetime.fromisoformat(last_ok)).total_seconds()
            if age < 25 * 60:
                log(f'guard: last success {int(age)}s ago (<25min) — skip')
                return {'skipped': True}
        except Exception:
            pass

    queries = read_json(QUERIES_FILE, None)
    if not queries:
        raise RuntimeError(f'{QUERIES_FILE} missing')

    stats = {'searched': 0, 'changed': 0, 'events': 0, 'digests': 0, 'empty': 0}
    for child, qs in queries.items():
        for q in qs:
            topic, query = q.get('topic', ''), q.get('query', '')
            key = f'{child}::{topic}'
            qstate = per_q.setdefault(key, {'consecutive_fail': 0})
            text = mcp_search(query)
            stats['searched'] += 1
            if not text:
                qstate['consecutive_fail'] = qstate.get('consecutive_fail', 0) + 1
                qstate['last_fail'] = now_iso()
                stats['empty'] += 1
                if qstate['consecutive_fail'] == 3:
                    write_alert('red', 'collect', f'{key}: 3 consecutive empty search results')
                time.sleep(0.4)
                continue
            qstate['consecutive_fail'] = 0
            qstate['last_ok'] = now_iso()
            fp = fingerprint(text)
            if fp == qstate.get('fp'):
                time.sleep(0.4)
                continue
            # ── 变更 ──
            stats['changed'] += 1
            imp = judge_score(text, topic)
            append_jsonl(ITEMS_FILE, {'ts': now_iso(), 'child': child, 'topic': topic,
                                      'query': query, 'fp': fp, 'importance': imp, 'text': text})
            append_jsonl(CHANGES_FILE, {'ts': now_iso(), 'child': child, 'topic': topic,
                                        'importance': imp, 'fp': fp})
            qstate['fp'] = fp
            qstate['last_change'] = now_iso()
            qstate['last_importance'] = imp
            log(f'[change] {key} importance={imp}')
            if imp >= 7:
                added = add_event({'id': f"ev-{int(time.time())}-{abs(hash(key)) % 10000}",
                                   'ts': now_iso(), 'child': child, 'topic': topic,
                                   'importance': imp, 'fp': fp,
                                   'requires_human': requires_human(text, imp),
                                   'excerpt': clean_excerpt(text),
                                   'status': 'pending_review' if requires_human(text, imp) else 'pending_notify'})
                if added:
                    stats['events'] += 1
                    log(f'[EVENT] {key} imp={imp} queued')
            if imp >= 3:
                update_digest(child, topic, query, text, imp)
                stats['digests'] += 1
            time.sleep(0.4)

    state['collect'] = {'last_run': now_iso(), 'stats': stats}
    atomic_write_json(STATE_FILE, state)
    log(f'[stats] {stats}')
    return stats


if __name__ == '__main__':
    run_logged('collect', collect_once)
