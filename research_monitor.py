#!/usr/bin/env python3
"""Phase 1 监控脚本：检查 30min 频率运行状况
- 检查上次运行结果（成功/失败/耗时）
- 检查系统负载（CPU/内存）
- 检查数据质量（JSON 大小/条目数）
- 输出报告
"""
import os, json, subprocess, time
from datetime import datetime, timedelta

LOG_FILE = '/var/log/research-urgent.log'
DATA_FILE = '/www/wwwroot/kid/data/research_weekly.json'

def check_last_run():
    """检查上次运行结果"""
    if not os.path.exists(LOG_FILE):
        return {'status': 'no_log', 'message': 'No log file found'}
    
    with open(LOG_FILE) as f:
        lines = f.readlines()
    
    # 找最后一次运行的开始/结束
    last_start = None
    last_end = None
    for i, line in enumerate(lines):
        if 'Starting weekly research digest' in line:
            last_start = i
        if 'Wrote' in line and 'research_weekly.json' in line:
            last_end = i
    
    if last_start is None:
        return {'status': 'no_run', 'message': 'No runs found in log'}
    
    # 提取时间戳
    start_line = lines[last_start].strip()
    start_time = start_line.split(']')[0][1:]  # [2026-11-11T14:17:23.123456]
    
    if last_end and last_end > last_start:
        end_line = lines[last_end].strip()
        # 计算耗时
        try:
            start_dt = datetime.fromisoformat(start_time)
            # 结束时间需要从日志推断（简化：假设成功）
            status = 'success'
            message = f'Last run started at {start_time}'
        except:
            status = 'unknown'
            message = f'Last run started at {start_time} (time parse failed)'
    else:
        status = 'running'
        message = f'Run started at {start_time} (still running or failed)'
    
    return {'status': status, 'message': message, 'start_time': start_time}

def check_system_load():
    """检查系统负载"""
    try:
        # CPU load
        load1, load5, load15 = os.getloadavg()
        # Memory
        with open('/proc/meminfo') as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0][:-1]] = int(parts[1])
            total = meminfo.get('MemTotal', 0)
            available = meminfo.get('MemAvailable', 0)
            mem_pct = (1 - available / total) * 100 if total > 0 else 0
        
        return {
            'load1': load1,
            'load5': load5,
            'load15': load15,
            'mem_pct': mem_pct,
            'status': 'ok' if load1 < 2.0 and mem_pct < 80 else 'warning'
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def check_data_quality():
    """检查数据质量"""
    if not os.path.exists(DATA_FILE):
        return {'status': 'no_data', 'message': 'No data file found'}
    
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
        
        file_size = os.path.getsize(DATA_FILE)
        generated_at = data.get('generated_at', 'unknown')
        diya_count = len(data.get('items', {}).get('diya', []))
        jemma_count = len(data.get('items', {}).get('jemma', []))
        
        # 检查数据新鲜度（应该 < 1 小时）
        try:
            gen_time = datetime.fromisoformat(generated_at)
            age_hours = (datetime.now() - gen_time).total_seconds() / 3600
            freshness = 'fresh' if age_hours < 1 else 'stale'
        except:
            freshness = 'unknown'
            age_hours = -1
        
        return {
            'status': 'ok' if diya_count > 0 and jemma_count > 0 else 'warning',
            'file_size_kb': file_size / 1024,
            'generated_at': generated_at,
            'diya_items': diya_count,
            'jemma_items': jemma_count,
            'age_hours': age_hours,
            'freshness': freshness
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def main():
    print(f"=== Research System Monitor ===")
    print(f"Time: {datetime.now().isoformat()}")
    print()
    
    # 1. 上次运行
    print("1. Last Run:")
    last_run = check_last_run()
    print(f"   Status: {last_run['status']}")
    print(f"   Message: {last_run['message']}")
    print()
    
    # 2. 系统负载
    print("2. System Load:")
    load = check_system_load()
    if load['status'] == 'ok':
        print(f"   CPU Load: {load['load1']:.2f} / {load['load5']:.2f} / {load['load15']:.2f}")
        print(f"   Memory: {load['mem_pct']:.1f}%")
        print(f"   Status: OK")
    else:
        print(f"   Status: WARNING")
        print(f"   Details: {load}")
    print()
    
    # 3. 数据质量
    print("3. Data Quality:")
    quality = check_data_quality()
    if quality['status'] == 'ok':
        print(f"   File size: {quality['file_size_kb']:.1f} KB")
        print(f"   Generated: {quality['generated_at']}")
        print(f"   Diya items: {quality['diya_items']}")
        print(f"   Jemma items: {quality['jemma_items']}")
        print(f"   Freshness: {quality['freshness']} ({quality['age_hours']:.1f}h ago)")
    else:
        print(f"   Status: {quality['status'].upper()}")
        print(f"   Details: {quality}")
    print()
    
    # 4. 总结
    overall = 'OK' if (last_run['status'] == 'success' and load['status'] == 'ok' and quality['status'] == 'ok') else 'WARNING'
    print(f"=== Overall: {overall} ===")

if __name__ == '__main__':
    main()
