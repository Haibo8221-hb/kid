#!/usr/bin/env python3
"""资源链接健康检查：扫描前端 HTML 中所有本地 href，报告文件/目录不存在者。

用法：python tests/check_links.py [文件...]   （默认扫描仓库根目录 html）
退出码：0 = 无失效链接；1 = 存在失效链接
"""
import os, re, sys

DEFAULT_FILES = ['app.html', 'jemma.html', 'index.html', 'english_p1_answers.html']
HREF = re.compile(r'href="([^"]+)"')
SKIP_PREFIX = ('http', '#', 'javascript:', 'mailto:', 'data:', 'tel:')
PLACEHOLDER = ('$', '{')      # ${item.url} / $1 之类模板占位符，跳过

def scan(root, files):
    bad = []
    for f in files:
        p = os.path.join(root, f)
        if not os.path.exists(p):
            continue
        src = open(p, encoding='utf-8', errors='ignore').read()
        for u in HREF.findall(src):
            u = u.strip()
            if u.startswith(SKIP_PREFIX) or u.startswith(PLACEHOLDER):
                continue
            rel = u.lstrip('/').split('?')[0].split('#')[0]
            if not rel or rel.startswith(PLACEHOLDER):
                continue
            if not os.path.exists(os.path.join(root, rel)):
                bad.append((f, u))
    return sorted(set(bad))

if __name__ == '__main__':
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = sys.argv[1:] or DEFAULT_FILES
    bad = scan(root, files)
    print(f'扫描 {len(files)} 个文件；本地失效链接 {len(bad)} 条')
    for f, u in bad:
        print(f'  MISS  {f} -> {u}')
    sys.exit(1 if bad else 0)
