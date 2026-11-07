#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shell 脚本 macOS 兼容性闸门（面向 macOS 自带的 bash 3.2）。

为什么需要这个闸门
==================
CI 跑在 macos-* runner 上，/bin/bash 是 3.2（2006 年，GPLv2 时代的最后一个版本）。
本机是 Windows + Git Bash（bash 5.x）+ dash，两者都**跑不出** bash 3.2 的问题，
所以「本地跑通了」对 macOS 零约束力 —— 只能靠静态检查兜住。

本次真实踩到的坑（CI run 42346759489）
-------------------------------------
    devkit/build.sh: line 122: CPU: unbound variable

源码那一行是：

    echo "✓ 架构精确匹配（$CPU，单切片）"

看着完全正常。根因是 bash 3.2 判断变量名边界用的 legal_variable_char()：

    static int legal_variable_char(c) { return ISALNUM(c) || c == '_'; }

而 macOS 的 ISALNUM 走 BSD libc，在 UTF-8 locale 下把 0xEF（中文全角标点的
首字节）判成「字母」，于是变量名被解析成 "CPU\\xEF" 而不是 "CPU"。
`set -u` 一查：没这个变量 → unbound variable。

报错文本里 0xEF 不可打印，所以显示成 `CPU: unbound variable`，
看起来就像「变量名就是 CPU」，完全指不到真正的病灶。定位成本极高。

修法只有一个：变量后紧跟非 ASCII 字符时，一律写 ${VAR}。

检查规则
========
R1  $VAR 后紧跟非 ASCII 字节        → 变量名被吞（本次事故）
R2  declare -A / -a 等关联数组      → bash 4.0+
R3  ${var,,} / ${var^^} 大小写改写  → bash 4.0+
R4  &>> 追加重定向                  → bash 4.0+
R5  |& 管道                         → bash 4.0+
R6  mapfile / readarray             → bash 4.0+
R7  wait -n                         → bash 4.3+

用法
====
    python3 devkit/check-shell.py [仓库根目录]

退出码：0 = 干净，1 = 有命中（并逐条打印 文件:行:列 + 上下文）。
"""

import os
import re
import sys

# 扫描时跳过的目录：产物、依赖、以及 patches（补丁里含历史版本代码，
# 检查它会把已经修好的旧问题反复报出来）。
SKIP_DIRS = {'.git', '.build', '.verify', 'node_modules', 'patches', '__pycache__'}

# 需要检查的文件类型
SHELL_EXT = {'.sh', '.bash'}
YAML_EXT = {'.yml', '.yaml'}


def iter_targets(root):
    """产出 (绝对路径, 相对路径, 类别)。类别 = 'shell' | 'yaml'。

    yaml 也要扫：GitHub Actions 的 run: 块里就是 shell 代码，
    同一个陷阱在里面一样会炸，而且更难发现。
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if ext in SHELL_EXT:
                kind = 'shell'
            elif ext in YAML_EXT:
                kind = 'yaml'
            else:
                continue
            full = os.path.join(dirpath, name)
            yield full, os.path.relpath(full, root), kind


# ---------------------------------------------------------------------------
# R1：$VAR 后紧跟非 ASCII —— 这是本次 CI 失败的真因
# ---------------------------------------------------------------------------
# 用 bytes 正则而不是 str 正则：要精确捕到「首个非 ASCII 字节」，
# 若走 str 会把多字节字符整体当一个字符，看不出 0xEF 这个具体字节。
# (?!\{) 排除 ${VAR}（已经是安全写法）。
RE_BARE_VAR_NONASCII = re.compile(rb'\$(?!\{)([A-Za-z_][A-Za-z0-9_]*)([\x80-\xff])')

# ---------------------------------------------------------------------------
# R2~R7：bash 4.0+ 专有语法
# ---------------------------------------------------------------------------
BASH4_PATTERNS = [
    ('R2', re.compile(rb'\bdeclare\s+-\w*[Aa]'), 'declare -A/-a（关联数组/索引数组声明）是 bash 4.0+'),
    ('R3', re.compile(rb'\$\{[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?(?:,,|,\}|\^\^|\^\})'),
     '${var,,} / ${var^^} 大小写改写是 bash 4.0+'),
    ('R4', re.compile(rb'&>>'), '&>> 同时追加 stdout/stderr 是 bash 4.0+'),
    ('R5', re.compile(rb'(?<!\|)\|&(?!\|)'), '|& 管道（自动合并 stderr）是 bash 4.0+'),
    ('R6', re.compile(rb'\b(?:mapfile|readarray)\b'), 'mapfile / readarray 是 bash 4.0+'),
    ('R7', re.compile(rb'\bwait\s+-n\b'), 'wait -n 是 bash 4.3+'),
]


def context_of(line_bytes, byte_pos, width=56):
    """取命中位置附近的上下文，解码成可读文本。

    从命中位置往前退一点、往后取一点，让「$CPU，单切片」整段可见。
    errors='replace' 是刻意的：坏字节显示成 ? 比抛 UnicodeDecodeError 有用。
    """
    start = max(0, byte_pos - 12)
    chunk = line_bytes[start:start + width]
    prefix = '...' if start > 0 else ''
    suffix = '...' if start + width < len(line_bytes) else ''
    return prefix + chunk.decode('utf-8', 'replace') + suffix


def scan_bytes(path, rel, kind):
    """扫描单个文件，返回 [(rel, lineno, col, code, message)]。"""
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError as exc:
        return [(rel, 0, 0, 'E0', '读不到文件: %s' % exc)]

    findings = []
    lines = raw.split(b'\n')
    for idx, line in enumerate(lines, 1):
        # 列号按「字节列」计（1-based）。中文行里字节列和视觉列不一致，
        # 但配上下文片段足够定位，不必换算成显示列。
        for m in RE_BARE_VAR_NONASCII.finditer(line):
            findings.append((
                rel, idx, m.start() + 1, 'R1',
                '$%s 后紧跟非 ASCII 字节 0x%02X（%s）→ bash 3.2 会把它吞进变量名，'
                'set -u 下报 unbound variable。请改写为 ${%s}\n      上下文: %s'
                % (m.group(1).decode('ascii'), m.group(2)[0],
                   m.group(2).decode('utf-8', 'replace'),
                   m.group(1).decode('ascii'),
                   context_of(line, m.start()))
            ))

        # bash 4+ 语法检查。yaml 里也扫：run: 块就是 shell。
        # 注释行不跳过 —— 注释里写坏语法同样误导后来人，且成本为零。
        for code, pat, desc in BASH4_PATTERNS:
            for m in pat.finditer(line):
                findings.append((
                    rel, idx, m.start() + 1, code,
                    '%s → macOS 自带 bash 3.2 不支持。\n      上下文: %s'
                    % (desc, context_of(line, m.start()))
                ))
    return findings


def main(argv):
    root = argv[1] if len(argv) > 1 else '.'
    root = os.path.abspath(root)

    if not os.path.isdir(root):
        print('✗ 不是目录: %s' % root)
        return 2

    results = []
    scanned = 0
    for full, rel, kind in iter_targets(root):
        scanned += 1
        results.extend(scan_bytes(full, rel, kind))

    if results:
        # 统计各规则命中数，先给结论再给明细
        counts = {}
        for _rel, _ln, _col, code, _msg in results:
            counts[code] = counts.get(code, 0) + 1
        summary = '  '.join('%s=%d' % (k, v) for k, v in sorted(counts.items()))
        print('✗ shell 兼容性闸门：%d 处问题（%s），共扫描 %d 个文件'
              % (len(results), summary, scanned))
        print()
        for rel, lineno, col, code, msg in results:
            print('  %s:%d:%d  [%s]  %s' % (rel, lineno, col, code, msg))
        print()
        print('  说明：CI 的 /bin/bash 是 3.2，本机 bash 5 / dash 跑不出这些问题，')
        print('        只能静态兜。修完重跑本脚本直到 0 命中。')
        return 1

    print('✓ shell 兼容性闸门通过：%d 个文件，0 处问题（bash 3.2 安全）' % scanned)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))