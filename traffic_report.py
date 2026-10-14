#!/usr/bin/env python3
"""Traffic report for Diya (app.html) and Jemma (jemma.html) from nginx access logs."""
import subprocess, sys, re
from datetime import datetime, timedelta
from collections import defaultdict

LOG_PATH = '/var/log/nginx/access.log'
DAYS = 2  # How many days back to look

def get_logs(days_back):
    """Get logs from last N days — merge current + rotated .gz files."""
    data = ''
    # Current log
    try:
        with open(LOG_PATH) as f:
            data += f.read()
    except:
        pass
    
    # Rotated logs (access.log.1 is yesterday, access.log.2.gz is day before, etc.)
    for i in range(1, days_back + 2):
        for suffix in ['', '.gz']:
            path = f'{LOG_PATH}.{i}{suffix}'
            try:
                if suffix == '.gz':
                    r = subprocess.run(['zcat', path], capture_output=True, text=True, timeout=10)
                    if r.returncode == 0:
                        data += r.stdout
                else:
                    with open(path) as f:
                        data += f.read()
            except:
                continue
    
    return data


def parse_logs(data):
    """Parse nginx access log entries for app.html and jemma.html page views."""
    cutoff = datetime.now() - timedelta(days=DAYS)
    
    # Patterns
    page_pattern = re.compile(r'GET /(app|jemma)\.html')
    ip_pattern = re.compile(r'^(\S+)')
    date_pattern = re.compile(r'\[(\d{2}/\w{3}/\d{4}):')
    
    diya = defaultdict(set)    # date -> set of IPs
    jemma = defaultdict(set)
    diya_visits = defaultdict(int)  # date -> total hits
    jemma_visits = defaultdict(int)
    
    for line in data.split('\n'):
        page_match = page_pattern.search(line)
        if not page_match:
            continue
        
        date_match = date_pattern.search(line)
        ip_match = ip_pattern.match(line)
        if not date_match or not ip_match:
            continue
        
        try:
            dt = datetime.strptime(date_match.group(1), '%d/%b/%Y')
        except:
            continue
        
        if dt < cutoff:
            continue
        
        date_key = dt.strftime('%a %d %b')
        ip = ip_match.group(1)
        page = page_match.group(1)
        
        if page == 'app':
            diya[date_key].add(ip)
            diya_visits[date_key] += 1
        else:
            jemma[date_key].add(ip)
            jemma_visits[date_key] += 1
    
    return diya, jemma, diya_visits, jemma_visits


def main():
    data = get_logs(DAYS)
    diya, jemma, diya_visits, jemma_visits = parse_logs(data)
    
    all_dates = sorted(set(list(diya.keys()) + list(jemma.keys())))
    
    if not all_dates:
        print("No traffic detected in the last 2 days.")
        return
    
    print(f"📊 Website Traffic Report — Last {DAYS} Days")
    print()
    
    total_diya_ips = 0
    total_jemma_ips = 0
    
    for date in all_dates:
        d_ips = len(diya.get(date, set()))
        j_ips = len(jemma.get(date, set()))
        total_diya_ips += d_ips
        total_jemma_ips += j_ips
        
        d_hits = diya_visits.get(date, 0)
        j_hits = jemma_visits.get(date, 0)
        
        print(f"{date}")
        print(f"  Diya  — {d_ips} unique visitors, {d_hits} page loads")
        print(f"  Jemma — {j_ips} unique visitors, {j_hits} page loads")
        print()
    
    print(f"Total: Diya {total_diya_ips} visitors | Jemma {total_jemma_ips} visitors")


if __name__ == '__main__':
    main()
