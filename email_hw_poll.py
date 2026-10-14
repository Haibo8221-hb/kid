#!/usr/bin/env python3
"""Homework Email Pipeline — polls bo.hai@foxmail.com for homework submissions sent to hw@grow-consultant.com.
Run via cron. Only processes UNSEEN emails. Message-ID dedup prevents duplicates.
"""
import subprocess, json, sys, os, re, base64
from datetime import datetime

# ── Config ─────────────────────────────────────────────
CHILD_MAP = {
    'h00426@headingtonschool.com': 'diya',
    'h00427@headingtonschool.com': 'jemma',
    # Parent emails — child determined by subject/body keywords ('diya'/'jemma')
    'bo.hai@foxmail.com': None,
    'yilian325@gmail.com': None,
}
API_BASE = 'http://127.0.0.1:5000'
DRY_RUN = '--dry-run' in sys.argv
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEDUP_FILE = os.path.join(SCRIPT_DIR, 'data', 'hw_processed_ids.txt')
HW_DIR = os.path.join(SCRIPT_DIR, 'homework_uploads')
HW_DATA_FILE = os.path.join(SCRIPT_DIR, 'data', 'homework_entries.json')

def himalaya(*args):
    cmd = ['himalaya'] + list(args) + ['--output', 'json']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"  himalaya error: {r.stderr.strip()[:200]}")
            return None
        return json.loads(r.stdout) if r.stdout.strip() else []
    except Exception as e:
        print(f"  himalaya exception: {e}")
        return None

def get_to_address(msg_id):
    """Read TO header from an email."""
    raw = subprocess.run(['himalaya', 'message', 'read', str(msg_id)],
                         capture_output=True, text=True, timeout=10)
    if raw.returncode != 0:
        return ''
    for line in raw.stdout.split('\n'):
        if line.startswith('To:') and '<' in line:
            return line.split('<')[1].split('>')[0].strip().lower()
    return ''

def load_processed_ids():
    if not os.path.exists(DEDUP_FILE):
        return set()
    with open(DEDUP_FILE) as f:
        return set(line.strip() for line in f if line.strip())

def save_processed_id(msg_id):
    os.makedirs(os.path.dirname(DEDUP_FILE), exist_ok=True)
    with open(DEDUP_FILE, 'a') as f:
        f.write(str(msg_id) + '\n')

def load_hw_entries():
    if not os.path.exists(HW_DATA_FILE):
        return []
    with open(HW_DATA_FILE) as f:
        return json.load(f)

def save_hw_entries(entries):
    os.makedirs(os.path.dirname(HW_DATA_FILE), exist_ok=True)
    with open(HW_DATA_FILE, 'w') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

def save_attachment(msg_id, part):
    """Save an attachment to the uploads directory."""
    os.makedirs(HW_DIR, exist_ok=True)
    filename = part.get('filename', f'attachment_{msg_id}')
    # Sanitize filename
    safe_name = re.sub(r'[^\w\.\-]', '_', filename)
    filepath = os.path.join(HW_DIR, f'{msg_id}_{safe_name}')
    
    # Try to download via himalaya attachment
    result = subprocess.run(
        ['himalaya', 'attachment', 'download', str(msg_id), '--output', filepath],
        capture_output=True, text=True, timeout=30
    )
    
    # Alternative: save base64 content if present in the part
    if result.returncode != 0 and part.get('content'):
        try:
            content = base64.b64decode(part['content'])
            with open(filepath, 'wb') as f:
                f.write(content)
        except:
            pass
    
    print(f"  Attachment saved: {filepath}")
    return filepath

def process_email(msg_id):
    """Read email, determine child + subject, extract homework info, save to JSON."""
    raw = subprocess.run(['himalaya', 'message', 'read', str(msg_id)],
                         capture_output=True, text=True, timeout=15)
    if raw.returncode != 0:
        print(f"  Failed to read msg {msg_id}")
        return False
    
    text = raw.stdout
    lines = text.split('\n')
    
    # Parse headers
    from_addr = ''
    subject = ''
    to_addr = ''
    date_str = ''
    body_start = 0
    
    for i, line in enumerate(lines):
        if line.startswith('From:') and '<' in line:
            from_addr = line.split('<')[1].split('>')[0].strip().lower()
        elif line.startswith('Subject:'):
            subject = line[8:].strip()
        elif line.startswith('To:'):
            to_addr = line[3:].strip().lower()
        elif line.startswith('Date:'):
            date_str = line[5:].strip()
        elif line.strip() == '' and i > 3:
            body_start = i + 1
            break
    
    # Determine child
    child = None
    for addr, name in CHILD_MAP.items():
        if addr in from_addr:
            child = name
            break
    
    if not child:
        combined = (subject + ' ' + '\n'.join(lines[body_start:body_start+5])).lower()
        if 'jemma' in combined:
            child = 'jemma'
        elif 'diya' in combined:
            child = 'diya'
    
    if not child:
        print(f"  Cannot determine child from {from_addr} — skipping")
        return False
    
    # Extract body
    body = '\n'.join(lines[body_start:])
    # Clean: strip quoted replies
    body = re.sub(r'\n>.*', '', body)
    body = body.strip()
    
    # Detect subject from email subject or body
    hw_subject = subject.strip()
    if not hw_subject or hw_subject.lower() in ['homework', 'hw', '作业']:
        # Try first line of body
        first_line = body.split('\n')[0].strip()
        if first_line and len(first_line) < 80:
            hw_subject = first_line
    
    # Parse date
    try:
        dt = datetime.strptime(date_str[:25].strip(), '%a, %d %b %Y %H:%M:%S')
        date_iso = dt.isoformat()
    except:
        date_iso = datetime.now().isoformat()
    
    # Build entry
    entry = {
        'id': str(msg_id),
        'child': child,
        'subject': hw_subject,
        'body': body[:2000],
        'from': from_addr,
        'date': date_iso,
        'attachments': [],
        'status': 'submitted',  # submitted, reviewed, graded
        'grade': None,
        'feedback': ''
    }
    
    # Check for attachments via himalaya attachments list
    att_list = himalaya('attachment', 'list', str(msg_id))
    if att_list:
        for att in att_list:
            path = save_attachment(msg_id, att)
            if path:
                entry['attachments'].append(os.path.basename(path))
    
    print(f"  ✓ [{child.upper()}] {hw_subject} from {from_addr}")
    if entry['attachments']:
        print(f"    Attachments: {', '.join(entry['attachments'])}")
    
    # Save to JSON
    entries = load_hw_entries()
    # Avoid duplicates
    if not any(e['id'] == str(msg_id) for e in entries):
        entries.insert(0, entry)
        save_hw_entries(entries)
    
    return True


def main():
    print(f"📚 Homework Pipeline — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   Checking for emails to hw@grow-consultant.com...")
    
    # Get unseen emails
    msgs = himalaya('envelope', 'list', '--page-size', '50')
    if msgs is None:
        print("  No messages (himalaya error)")
        return
    
    if DRY_RUN:
        print(f"  [DRY RUN] Found {len(msgs)} messages")
    else:
        print(f"  Found {len(msgs)} messages total")
    
    processed = load_processed_ids()
    new_count = 0
    
    for msg in msgs:
        msg_id = msg.get('id')
        if not msg_id or str(msg_id) in processed:
            continue
        
        # Use envelope's 'to' field directly — avoids per-message IMAP call
        to_data = msg.get('to', {})
        if isinstance(to_data, dict):
            to_addr = to_data.get('addr', '').lower()
        elif isinstance(to_data, str):
            to_addr = to_data.lower()
        else:
            to_addr = ''
        
        # Only process emails TO hw@grow-consultant.com
        if 'hw@grow-consultant.com' not in to_addr:
            continue
        
        if not DRY_RUN:
            print(f"\n  Processing msg {msg_id} (to: {to_addr})")
            if process_email(msg_id):
                save_processed_id(msg_id)
                new_count += 1
        else:
            print(f"  [DRY RUN] Would process msg {msg_id} (to: {to_addr})")
            new_count += 1
    
    print(f"\n  Done — {new_count} new homework submission(s)")


if __name__ == '__main__':
    main()
