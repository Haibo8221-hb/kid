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
# JS 动态拼接的 href 值（如 href="'+u+'"），非静态路径，跳过
DYNAMIC_JS = ("'",)

# 服务器专有目录：这些文件只存在于 ECS（体积大 / 版权资料），既不入 git 也不参与 rsync，
# 因此在 CI（干净 checkout）里必然"不存在"，不能算失效链接。
# 与 .github/workflows/deploy.yml 的 --exclude 保持一致。
SERVER_ONLY_DIRS = ('downloads/', 'resources/', 'backups/', 'data/', 'logs/')

def scan(root, files):
    bad = []
    skipped = []
    for f in files:
        p = os.path.join(root, f)
        if not os.path.exists(p):
            continue
        src = open(p, encoding='utf-8', errors='ignore').read()
        for u in HREF.findall(src):
            u = u.strip()
            if u.startswith(SKIP_PREFIX) or u.startswith(PLACEHOLDER):
                continue
            if any(q in u for q in DYNAMIC_JS):
                continue
            rel = u.lstrip('/').split('?')[0].split('#')[0]
            if not rel or rel.startswith(PLACEHOLDER):
                continue
            if rel.startswith(SERVER_ONLY_DIRS):
                skipped.append((f, u))
                continue
            if not os.path.exists(os.path.join(root, rel)):
                bad.append((f, u))
    return sorted(set(bad)), sorted(set(skipped))

if __name__ == '__main__':
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = sys.argv[1:] or DEFAULT_FILES
    bad, skipped = scan(root, files)
    print(f'扫描 {len(files)} 个文件；本地失效链接 {len(bad)} 条；'
          f'服务器专有目录链接 {len(skipped)} 条（不校验）')
    for f, u in skipped:
        print(f'  SKIP  {f} -> {u}')
    for f, u in bad:
        print(f'  MISS  {f} -> {u}')
    sys.exit(1 if bad else 0)
