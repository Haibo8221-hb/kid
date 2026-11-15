#!/usr/bin/env python3
"""Grow Weekly 邮件发送：把草稿 md 渲染成 HTML 发出。
用法: grow_weekly_send.py <WEEK> <child> [--to EMAIL]
默认家长预览（主题带 PREVIEW）；学校发送须家长明确指令后用 --to 学校邮箱。
"""
import os, re, sys, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

WEEK, CHILD = sys.argv[1], sys.argv[2]
TO = 'bo.hai@foxmail.com'
PREVIEW = '--to' not in sys.argv
if '--to' in sys.argv:
    TO = sys.argv[sys.argv.index('--to') + 1]

env = {}
for line in open('/etc/plan-api.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()
USER = env.get('SMTP' + '_USER', 'xgrow@qq.com')
PASS = env.get('SMTP' + '_PASS', '')

md = open(f'/www/wwwroot/kid/data/weekly_drafts/{WEEK}-{CHILD}.md', encoding='utf-8').read()

def inline(s):
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em>\1</em>', s)
    return s

def table_html(rows):
    head, body = rows[0], rows[1:]
    t = ['<table style="width:100%;border-collapse:collapse;font-size:14px;margin:8px 0">']
    t.append('<tr>' + ''.join('<th style="text-align:left;padding:6px 10px;border-bottom:2px solid #d8dee9;'
                              'color:#5b6474;font-size:12.5px">' + inline(c) + '</th>' for c in head) + '</tr>')
    for r0 in body:
        warn = '⚠️' in ''.join(r0)
        bg = ' style="background:#fef2f2"' if warn else ''
        t.append('<tr' + bg + '>' + ''.join('<td style="padding:6px 10px;border-bottom:1px solid #eef1f6">'
                                            + inline(c) + '</td>' for c in r0) + '</tr>')
    t.append('</table>')
    return ''.join(t)

lines = md.splitlines()
html, i = [], 0
while i < len(lines):
    s = lines[i].strip()
    if not s:
        i += 1; continue
    if s.startswith('|'):
        rows = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
            if not all(re.fullmatch(r':?-{3,}:?', c) for c in cells):
                rows.append(cells)
            i += 1
        if rows:
            html.append(table_html(rows))
        continue
    if s.startswith('# '):
        html.append('<h1 style="font-size:21px;margin:0 0 6px">' + inline(s[2:]) + '</h1>')
    elif s.startswith('## '):
        html.append('<h2 style="font-size:15px;margin:18px 0 6px;color:' +
                    ('#3f7dff' if CHILD == 'diya' else '#7c3aed') +
                    ';border-bottom:1px solid #eef1f6;padding-bottom:4px">' + inline(s[3:]) + '</h2>')
    elif s == '---':
        html.append('<hr style="border:0;border-top:1px solid #eef1f6;margin:14px 0">')
    elif s.startswith('- '):
        items = []
        while i < len(lines) and lines[i].strip().startswith('- '):
            items.append(lines[i].strip()[2:]); i += 1
        html.append('<ul style="padding-left:20px;margin:6px 0">' +
                    ''.join('<li style="margin:4px 0;font-size:14.5px">' + inline(x) + '</li>' for x in items) + '</ul>')
        continue
    else:
        html.append('<p style="margin:8px 0;font-size:14.5px;line-height:1.7">' + inline(s) + '</p>')
    i += 1

theme = {'diya': '#3f7dff', 'jemma': '#7c3aed'}.get(CHILD, '#3f7dff')
tag = 'PREVIEW · parent copy' if PREVIEW else ''
body_html = ('<html><body style="font-family:-apple-system,Segoe UI,PingFang SC,sans-serif;color:#1a1d28;'
             'max-width:660px;margin:0 auto;padding:24px 16px;background:#fff">'
             '<div style="display:inline-block;background:' + theme + ';color:#fff;font-weight:700;font-size:12px;'
             'padding:2px 12px;border-radius:10px;margin-bottom:10px">' + CHILD.upper() + ' · GROW WEEKLY · ' + WEEK +
             (' · ' + tag if tag else '') + '</div>' + ''.join(html) +
             '<p style="color:#8a93a5;font-size:12px;margin-top:18px">Grow Weekly · xgrow.me dashboard · '
             'numbers come from your own records</p></body></html>')

plain = re.sub(r'\*\*(.+?)\*\*', r'\1', md)
subject = '[' + CHILD.upper() + '] Grow Weekly ' + WEEK + (' — PREVIEW' if PREVIEW else '')
msg = MIMEMultipart('alternative')
msg['From'] = USER
msg['To'] = TO
msg['Subject'] = subject
msg.attach(MIMEText(plain, 'plain', 'utf-8'))
msg.attach(MIMEText(body_html, 'html', 'utf-8'))
srv = smtplib.SMTP_SSL('smtp.qq.com', 465, timeout=30)
srv.login(USER, PASS)
refused = srv.sendmail(USER, [TO], msg.as_string())
srv.quit()
print('SENT [' + subject + '] -> ' + TO + ' | refused: ' + (str(refused) if refused else 'none'))
