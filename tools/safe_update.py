#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe_update.py — 方案 A「快照基线 + 双重门禁」的落地脚本

用法（三步）：
  1) 编辑前   python3 tools/safe_update.py snapshot <文件>              # 建 .versions 基线
  2) 编辑后   python3 tools/safe_update.py verify <文件>                # 双重门禁（结构平衡 + 与基线 diff 复核）
  3) 出问题时 python3 tools/safe_update.py rollback <文件>              # 30 秒回滚到编辑前

目录结构：
  <文件所在目录>/.versions/<文件名>.<时间戳>.html   ← 编辑前快照（基线）
"""
import sys, pathlib, shutil, datetime, re, difflib

VOID = {'br','hr','img','meta','link','input','area','base','col','embed','source','track','wbr'}

def versions_dir(f: pathlib.Path) -> pathlib.Path:
    return f.parent / '.versions'

def latest_snapshot(f: pathlib.Path):
    vd = versions_dir(f)
    if not vd.exists(): return None
    cands = sorted(vd.glob(f.name + '.*.html'))
    return cands[-1] if cands else None

def cmd_snapshot(f: pathlib.Path):
    snap = latest_snapshot(f)
    # 幂等：若最新快照与当前文件一致，不重复快照（避免同一版本多份）
    if snap and snap.read_bytes() == f.read_bytes():
        print(f"OK 幂等：最新快照即当前内容 ({snap.name})"); return 0
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    vd = versions_dir(f); vd.mkdir(exist_ok=True)
    dst = vd / f"{f.name}.{ts}.html"
    shutil.copy2(f, dst)
    print(f"OK 基线已建: {dst}")
    print(f"   字节数: {f.stat().st_size}")
    return 0

def _check_structure(src: str):
    """门禁 1：HTMLParser 全量解析，返回 (错误列表, 未闭合栈)"""
    from html.parser import HTMLParser
    class P(HTMLParser):
        def __init__(s):
            super().__init__(convert_charrefs=True); s.stack=[]; s.errs=[]
        def handle_starttag(s,t,a):
            if t not in VOID: s.stack.append((t,s.getpos()))
        def handle_endtag(s,t):
            if t in VOID: return
            if not s.stack: s.errs.append(('多余的闭合标签',t,s.getpos())); return
            if s.stack[-1][0]==t: s.stack.pop()
            else: s.errs.append(('闭合不匹配',t,s.getpos(),f'栈顶为 {s.stack[-1][0]}'))
    pp = P(); pp.feed(src)
    return pp.errs, pp.stack

def cmd_verify(f: pathlib.Path):
    src = f.read_text(encoding='utf-8')
    errs, stack = _check_structure(src)
    ok = True
    # 门禁 1：结构平衡
    if errs or stack:
        ok = False
        print("FAIL 门禁1 · 结构不平衡：")
        for e in errs[:10]: print(f"   {e}")
        for t,pos in stack[:10]: print(f"   未闭合: <{t}> 开于 {pos}")
    else:
        print("PASS 门禁1 · 结构完全平衡（HTMLParser 零错误）")
    # 门禁 2：与基线 diff 复核（改动范围可追溯）
    snap = latest_snapshot(f)
    if not snap:
        print("WARN 门禁2 · 无基线可比（本次为首个版本，跳过 diff 复核）")
    else:
        base = snap.read_text(encoding='utf-8').splitlines()
        cur = src.splitlines()
        removed = [l for l in difflib.unified_diff(base, cur, lineterm='') if l.startswith('-') and not l.startswith('---')]
        added   = [l for l in difflib.unified_diff(base, cur, lineterm='') if l.startswith('+') and not l.startswith('+++')]
        print(f"PASS 门禁2 · 与基线 diff：删除 {len(removed)} 行 / 新增 {len(added)} 行")
        print(f"   基线: {snap.name}")
        if removed:
            print("   —— 删除行（须逐条可追溯到本次需求）——")
            for l in removed[:10]: print(f"   {l[:100]}")
            if len(removed) > 10: print(f"   …共 {len(removed)} 行")
    # 汇总
    print("\n结论: " + ("✅ 可安全落盘/交付" if ok else "❌ 禁止交付——先修复或 rollback"))
    return 0 if ok else 1

def cmd_rollback(f: pathlib.Path):
    snap = latest_snapshot(f)
    if not snap:
        print("FAIL 无可回滚快照"); return 1
    trash = f.parent / (f.name + '.broken.html')
    shutil.copy2(f, trash)          # 出错现场留存，便于诊断
    shutil.copy2(snap, f)           # 基线回写
    print(f"OK 已回滚到: {snap.name}")
    print(f"   出错现场留存为: {trash.name}（诊断后可删）")
    return 0

def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('snapshot','verify','rollback'):
        print(__doc__); sys.exit(2)
    f = pathlib.Path(sys.argv[2])
    if not f.exists():
        print(f"FAIL 文件不存在: {f}"); sys.exit(2)
    sys.exit({'snapshot':cmd_snapshot,'verify':cmd_verify,'rollback':cmd_rollback}[sys.argv[1]](f))

if __name__ == '__main__':
    main()
