# -*- coding: utf-8 -*-
"""OCR 前言清洗单测（_strip_ocr_preamble）"""
import os
import sys

sys.path.insert(0, '.')
os.environ.setdefault('DATA_DIR', '/tmp/ocrclean')
from api_server import _strip_ocr_preamble as s

# (输入, 期望包含, 期望不包含, 名称)
CASES = [
    # 历史实测污染样式：前言独占一行 → 剥除，正文保留
    ('The image is a transcription of a GCSE Chemistry exam paper with student answers:\n'
     '**04.1** answer here\nmore content below',
     '**04.1** answer here', 'transcription of', '剥除The image前言'),
    # "Sure! Here is..." 前言与正文同行 → 剥除
    ('Sure! Here is the transcription of the exam page:\n'
     'Q1: What is an isotope? Answer: same protons different neutrons [2 marks]',
     'Q1: What is an isotope', 'Here is the transcription', '剥除Sure前言'),
    # 正常转写（无前言）→ 原样保留
    ('03.3 Chadwick showed neutrons exist.\nThis led to understanding of isotopes',
     'Chadwick showed neutrons', '', '无前言不动'),
    # 正文本身以 "This page contains..." 语义开头但其实是题目内容 → 不误伤（短文本保护）
    ('short answer', '', '', '短文本不剥'),
    # 空输入
    ('', '', '', '空串安全'),
    # 前言剥除后仍有第二层前言 → 最多剥两行（贴近真实页长度，>80 字符）
    ('Here is the transcription of the uploaded exam page:\n'
     'The image shows a chemistry paper with handwritten answers:\n'
     '**Q5** actual content here with electrolysis half equations Cu2+ + 2e- -> Cu',
     '**Q5** actual content', '', '连剥两层'),
]

fails = []
for src, must_have, must_not, name in CASES:
    out = s(src)
    ok = (not must_have or must_have in out) and (not must_not or must_not not in out)
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {name}: -> {out[:60]!r}')
    if not ok:
        fails.append(name)

print()
print('RESULT:', 'ALL PASS' if not fails else f'FAILED {fails}')
sys.exit(1 if fails else 0)
