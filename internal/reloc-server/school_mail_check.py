#!/usr/bin/env python3
"""学校邮件自动检查：IMAP 读 xgrow@qq.com，过滤 admissions/学校域名新邮件，
存 DB + 输出微信推送摘要（无新邮件则静默退出）。"""
import os, sys, re, sqlite3, datetime, email, imaplib
from email.header import decode_header, make_header

DB = '/www/wwwroot/kid/data/reloc/tasks.db'
ENV = '/etc/plan-api.env'

# 学校/招生相关发件人特征
BLOCK = ['headingtonschool', 'goodschoolsguide', 'gillmanandsoame', 'goodschools']
DOMAINS = ['dess.sch.ae', 'horizonschooldubai', 'horizontownsquare', 'vhprimary', 'vhps',
           'kingsdubai', 'kings-edu', 'dubaibritishschool', 'dubaibritishfs', 'taaleem',
           'gemseducation', 'jumeirahprimaryschool', 'openapply', 'cognita', 'safacommunity',
           'jess.sch.ae', 'schoolwise', 'edcare']
SUBJECT_KW = ['admission', 'FS1', 'enquiry', 'assessment', 'application', 'Chengze']

def env(k, d=''):
    for line in open(ENV):
        line = line.strip()
        if line.startswith(k + '='):
            return line.split('=', 1)[1].strip().strip('"')
    return d

def decode(s):
    if not s: return ''
    try: return str(make_header(decode_header(s)))
    except Exception: return str(s)

def body_of(msg):
    out = ''
    for part in msg.walk():
        if part.get_content_type() == 'text/plain':
            try:
                out += part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'ignore')
            except Exception: pass
        elif part.get_content_type() == 'text/html' and not out:
            try:
                h = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'ignore')
                out = re.sub(r'<style.*?</style>', '', h, flags=re.S)
                out = re.sub(r'<[^>]+>', ' ', out)
                out = re.sub(r'\s+', ' ', out)
            except Exception: pass
    return out.strip()

imap = imaplib.IMAP4_SSL('imap.qq.com', 993)
USER, PASS = env('SMTP_USER'), env('SMTP_PASS')
imap.login(USER, PASS)
imap.select('INBOX', readonly=True)

# 自上次水位以来的邮件
con = sqlite3.connect(DB, timeout=10)
try:
    last_date = con.execute("SELECT v FROM meta WHERE k='school_mail_last'").fetchone()[0]
except Exception:
    last_date = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()

d_since = datetime.date.fromisoformat(last_date).strftime('%d-%b-%Y')
typ, data = imap.search(None, f'(SINCE {d_since})')
ids = data[0].split()
hits, seen_ids = [], set()
try:
    seen = con.execute("SELECT message_id FROM school_mails").fetchall()
    seen_ids = {s[0] for s in seen}
except Exception:
    con.execute("CREATE TABLE IF NOT EXISTS school_mails(id INTEGER PRIMARY KEY, message_id TEXT, received DATE, sender TEXT, subject TEXT, body_snip TEXT, created TIMESTAMP)")

for i in ids[-200:]:
    typ, md = imap.fetch(i, '(RFC822)')
    if typ != 'OK': continue
    msg = email.message_from_bytes(md[0][1])
    mid = msg.get('Message-ID', '') or f'{i}-{msg.get("Date","")}'
    if mid in seen_ids: continue
    frm = decode(msg.get('From', ''))
    subj = decode(msg.get('Subject', ''))
    low = (frm + ' ' + subj).lower()
    if any(b in low for b in BLOCK):
        continue
    if not (any(d in low for d in DOMAINS) or any(k.lower() in subj.lower() for k in SUBJECT_KW)):
        continue
    body = body_of(msg)[:1200]
    recv = datetime.date.today().isoformat()
    con.execute("INSERT INTO school_mails(message_id,received,sender,subject,body_snip,created) VALUES(?,?,?,?,?,?)",
                (mid, recv, frm[:120], subj[:200], body, datetime.datetime.now().isoformat(timespec='seconds')))
    hits.append({'from': frm, 'subj': subj, 'body': body[:600]})

con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('school_mail_last',?)", (datetime.date.today().isoformat(),))
con.commit(); con.close()
imap.logout()

if not hits:
    print('NO_NEW')
else:
    print(f'NEW_SCHOOL_MAILS: {len(hits)}')
    for h in hits:
        print('\n---')
        print('From:', h['from'][:80])
        print('Subj:', h['subj'][:120])
        print('Body:', h['body'][:450])
