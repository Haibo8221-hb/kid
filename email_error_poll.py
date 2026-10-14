#!/usr/bin/env python3
"""Error Book Email Pipeline — polls bo.hai@foxmail.com for error submissions from Diya/Jemma.
Run via cron. Only processes UNSEEN emails. Message-ID dedup prevents duplicates.
"""
import subprocess, json, sys, os

# ── Config ─────────────────────────────────────────────
CHILD_MAP = {
    'h00426@headingtonschool.com': 'diya',
    'h00427@headingtonschool.com': 'jemma',
}
API_BASE = 'http://127.0.0.1:5000'
DRY_RUN = '--dry-run' in sys.argv
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEDUP_FILE = os.path.join(SCRIPT_DIR, 'data', 'processed_msg_ids.txt')

def himalaya(*args):
    cmd = ['himalaya'] + list(args) + ['--output', 'json']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
    """Load set of already-processed message IDs."""
    if not os.path.exists(DEDUP_FILE):
        return set()
    with open(DEDUP_FILE) as f:
        return set(line.strip() for line in f if line.strip())

def save_processed_id(msg_id):
    """Append a message ID to the dedup file."""
    os.makedirs(os.path.dirname(DEDUP_FILE), exist_ok=True)
    with open(DEDUP_FILE, 'a') as f:
        f.write(str(msg_id) + '\n')

def process_email(msg_id):
    """Read email, determine child, extract error text, POST to API."""
    # Read full message
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
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith('From:') and '<' in line:
            from_addr = line.split('<')[1].split('>')[0].strip().lower()
        elif line.startswith('Subject:'):
            subject = line[8:].strip()
        elif line.strip() == '' and i > 3:
            body_start = i + 1
            break
    
    # Determine child — try FROM address first, then subject/body keywords
    child = None
    for addr, name in CHILD_MAP.items():
        if addr in from_addr:
            child = name
            break
    
    if not child:
        # Try to extract from subject or body
        combined = (subject + ' ' + '\n'.join(lines[body_start:])).lower()
        if 'jemma' in combined:
            child = 'jemma'
        elif 'diya' in combined:
            child = 'diya'
    
    if not child:
        print(f"  ✗ Cannot determine child from {from_addr} — add 'diya' or 'jemma' in subject")
        return False
    
    # Extract body (after headers), decode HTML entities
    body = '\n'.join(lines[body_start:])
    # Decode HTML entities (&nbsp; &amp; etc.)
    import html as _html
    body = _html.unescape(body)
    # Remove excessive blank lines
    import re as _re
    body = _re.sub(r'\n{4,}', '\n\n', body)
    # Truncate at common signature markers
    for marker in ['\nBo Hai', '\n-- \n', '\n_____', 'Sent from my iPhone', 'Sent from my iPad']:
        idx = body.find(marker)
        if idx > 0:
            body = body[:idx]
    body = body.strip()
    
    # Combine subject + body as error raw text
    error_text = subject
    if body and body != subject:
        error_text += '\n' + body
    error_text = error_text.strip()[:2000]
    
    if not error_text:
        print(f"  ✗ Empty content from {child}")
        return False
    
    print(f"  📧 {child}: {error_text[:80]}...")
    
    if DRY_RUN:
        print("  [DRY RUN] Would POST to API")
        return True
    
    # POST to parse-error API
    try:
        import urllib.request
        req = urllib.request.Request(
            f'{API_BASE}/api/parse-error',
            data=json.dumps({'text': error_text, 'child': child}).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        if result.get('error'):
            print(f"  ⚠️ API error: {result['error']}")
            return False
        
        # Save error to the right child's localStorage via API
        err_entry = {
            'child': child,
            'raw': error_text,
            'subject': result.get('subject', ''),
            'type': result.get('errorType', 'other'),
            'solution': result.get('solution', ''),
            'source': f'Email ({subject[:40]})',
        }
        req2 = urllib.request.Request(
            f'{API_BASE}/api/error-save',
            data=json.dumps(err_entry).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        resp2 = urllib.request.urlopen(req2, timeout=10)
        save_result = json.loads(resp2.read())
        print(f"  ✅ Saved: {save_result}")
        return True
    except Exception as e:
        print(f"  ❌ API call failed: {e}")
        return False

def main():
    print(f"\n🔍 Error Email Poll — {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    # Load dedup set
    processed_ids = load_processed_ids()
    print(f"  Dedup DB: {len(processed_ids)} previously processed")
    
    # Get recent emails (page 1, 30 items)
    envelopes = himalaya('envelope', 'list', '--page-size', '30')
    if envelopes is None:
        print("  Failed to list envelopes")
        return
    
    # Only process recent emails (last 48 hours)
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(hours=48)
    
    processed = 0
    skipped_dup = 0
    skipped_seen = 0
    for env in envelopes:
        msg_id = env.get('id')
        subject = env.get('subject', '')
        flags = env.get('flags', [])
        
        # Skip old emails
        date_str = env.get('date', '')
        try:
            email_date = datetime.strptime(date_str[:19], '%Y-%m-%d %H:%M')
            if email_date < cutoff:
                continue
        except:
            pass  # Can't parse date, process anyway
        
        # Skip already-seen emails (processed by us previously)
        if 'Seen' in flags:
            skipped_seen += 1
            continue
        
        # Quick filter: check subject for error-submission keywords
        subject_lower = subject.lower()
        quick_match = any(kw in subject_lower for kw in 
            ['error', '错题', '错', 'mistake', 'wrong', 'wrongly', 'incorrect',
             '不会', '不懂', '做错', '不会做', '算错', '搞错'])
        if not quick_match:
            continue
        
        # Dedup: skip if already processed (by message ID)
        if str(msg_id) in processed_ids:
            skipped_dup += 1
            continue
        
        # Only process emails sent TO error@grow-consultant.com
        to_addr = get_to_address(msg_id)
        if 'error@grow-consultant.com' not in to_addr:
            continue
        
        print(f"\n  Processing #{msg_id}: {subject[:50]}")
        
        ok = process_email(msg_id)
        if ok:
            # Mark as seen + save to dedup
            if not DRY_RUN:
                subprocess.run(['himalaya', 'flag', 'add', str(msg_id), '--flag', 'seen'],
                             capture_output=True, timeout=10)
                save_processed_id(str(msg_id))
                processed_ids.add(str(msg_id))
            processed += 1
    
    print(f"\n  Done: {processed} processed, {skipped_seen} skipped (already seen), {skipped_dup} skipped (dedup)")

if __name__ == '__main__':
    main()
