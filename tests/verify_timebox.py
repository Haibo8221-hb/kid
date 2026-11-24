#!/usr/bin/env python3
"""时间盒/每日复盘 schema 回归测试（2026-11-24）。
背景：fb6482f 提交的 /api/hrd/timebox 与 /api/hrd/dayreview 依赖
timeboxes / daily_reviews 两张表，但仓库内原本无任何 DDL，首次调用必抛
no such table。本测试从 api_server.py 提取 _ensure_tb_schema 的 DDL，
在内存库上验证：建表、生命周期状态机、复盘 upsert 幂等、约束齐全。
运行：python tests/verify_timebox.py
"""
import io
import os
import re
import sqlite3
import sys
import textwrap

SRC = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'api_server.py'),
              encoding='utf-8').read()

def main():
    m = re.search(
        r'def _ensure_tb_schema\(\):.*?con = sqlite3\.connect\(RELOC_DB, timeout=10\)\n(.*?)\n    con\.commit\(\)\n    con\.close\(\)',
        SRC, re.S)
    assert m, '未找到 _ensure_tb_schema 函数体'
    body = textwrap.dedent(m.group(1))
    con = sqlite3.connect(':memory:')
    exec(body, {'con': con})

    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'timeboxes', 'daily_reviews'} <= tables, '缺表: %s' % tables
    idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert 'idx_timeboxes_tdate' in idx, '缺 tdate 索引'
    assert 'idx_daily_reviews_rdate' in idx, '缺 rdate 唯一索引'

    # timeboxes 生命周期: planned -> doing -> done
    cur = con.execute(
        "INSERT INTO timeboxes(tdate,slot,title,est_min,status,task_ref,created)"
        " VALUES('2026-11-24','09:00','冒烟盒',50,'planned','P6-08','t')")
    bid = cur.lastrowid
    con.execute("UPDATE timeboxes SET status='doing', started_at='t' WHERE id=?", (bid,))
    con.execute("UPDATE timeboxes SET status='done', actual_min=42, ended_at='t2' WHERE id=?", (bid,))
    assert con.execute('SELECT status FROM timeboxes WHERE id=?', (bid,)).fetchone()[0] == 'done'

    # daily_reviews upsert 幂等：同一 rdate 写两次仍只有 1 行
    ins = ("INSERT INTO daily_reviews(rdate,mood,pass_text,cause_text,action_text,created,updated)"
           " VALUES('2026-11-24','绿','a','b','c1','t','t')"
           " ON CONFLICT(rdate) DO UPDATE SET mood=excluded.mood,"
           " action_text=excluded.action_text, updated=excluded.updated")
    con.execute(ins)
    con.execute(ins)
    assert con.execute('SELECT COUNT(*) FROM daily_reviews').fetchone()[0] == 1, 'upsert 不幂等'
    assert con.execute('SELECT mood FROM daily_reviews').fetchone()[0] == '绿'

    print('verify_timebox: ALL OK')

if __name__ == '__main__':
    sys.exit(main())
