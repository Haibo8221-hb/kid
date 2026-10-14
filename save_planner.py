#!/usr/bin/env python3
"""Simple CGI-like planner save/load via nginx"""
import cgi, json, os, sys

DATA_DIR = '/var/www/plan/data'
os.makedirs(DATA_DIR, exist_ok=True)

method = os.environ.get('REQUEST_METHOD', 'GET')

if method == 'POST':
    cl = int(os.environ.get('CONTENT_LENGTH', 0))
    body = sys.stdin.read(cl) if cl > 0 else ''
    try:
        data = json.loads(body)
        user = data.get('user', 'diya')
        plan = data.get('plan', {})
        # 每用户独立存储
        fpath = os.path.join(DATA_DIR, f'planner_{user}.json')
        with open(fpath, 'w') as f:
            json.dump(plan, f)
        print('Content-Type: application/json')
        print()
        print(json.dumps({'ok': True}))
    except Exception as e:
        print('Content-Type: application/json')
        print()
        print(json.dumps({'ok': False, 'error': str(e)}))
else:
    # GET
    qs = os.environ.get('QUERY_STRING', '')
    params = cgi.parse_qs(qs)
    user = params.get('user', ['diya'])[0]
    fpath = os.path.join(DATA_DIR, f'planner_{user}.json')
    if os.path.exists(fpath):
        with open(fpath) as f:
            data = json.load(f)
    else:
        data = {}
    print('Content-Type: application/json')
    print()
    print(json.dumps(data))
