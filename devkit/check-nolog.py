#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断日志负向闸门（v1.1.3 起；v1.6.12 改为剥注释 + 补 os_log）。

用法：python3 devkit/check-nolog.py <Tweak.xm>
退出码 0 = 通过；1 = 命中日志调用；2 = 剥注释器自证失败（判据不可信）。

为什么从 build.sh 里搬出来
==========================
这条判据原来是 build.sh 第 4b 段里的一段裸 shell：

    if grep -nE '(^|[^A-Za-z_])LOG\\(' Tweak.xm || grep -n 'NSLog' Tweak.xm

两个缺陷，v1.6.12 同时踩爆：

1) **没剥注释**。v1.6.12 在 OkaXferDiagResponse 上方写了一整段「产物里
   禁止 NSLog / os_log，写日志会让整个构建失败」的说明 —— 文档里提到名字，
   裸 grep 就当代码命中，三个 job 全 red。这个坑本仓库已经踩过两次
   （v1.1.8 的 window.location、v1.6.3 的 hover 闸门 6 条假红），
   根因都是「注释是文档不是代码」。

2) **承诺了 os_log 却没查**。注释从 v1.1.3 起就写着「禁止 NSLog / os_log」，
   但判据只有 NSLog —— 一条不出现的规则就是永远通过的规则。os_log 一样
   会写系统日志，必须同罪同罚。

搬出成独立脚本的真正理由是可测：写在 heredoc 里的判据没法被 selftest 注入
破坏样本来反证，等于「看起来有闸门、其实没人验证过它会红」。独立成文件后
由 selftest-nolog.py 做正反两向反证（见该文件）。

判据只跑在**剥注释后**的源码上，且剥离器自身先做自证（自证失败直接 exit 2，
不放行）——剥离器坏了却继续判，等于所有判据一起变瞎。
"""
import re
import sys

# 命中即失败的三类调用。全部按「函数名 + 左括号」匹配，避免把
# 注释/变量名里的同名词当命中（剥注释已挡掉注释，这一步挡标识符）。
PATS = [
    ("NSLog", r"\bNSLog\s*\("),
    # os_log / os_log_create / os_log_with_type 等一族。
    ("os_log", r"\bos_log[a-z_]*\s*\("),
    # LOG(...) 宏；前面不能是标识符字符（挡住 MYLOG( / DebugLOG( 里的子串误判？
    # 不 —— 那些本来就是「日志类调用」，宁可拦下让人来解释，也不放过。
    # 这里只挡 `_LOG(` 这种系统前缀与非调用形态。
    ("LOG宏", r"(?<![A-Za-z_])LOG\s*\("),
]


def strip_comments(s):
    """剥掉 // 与 /* */ 注释，保留换行与字符串/字符字面量内容。

    状态机必须认识 @"" / "" / ''：
      · 字符串里的 // 与 /* 不是注释；
      · 注释里的引号不是字符串起点（否则 /* */ 里的一个 " 会把后面全吞进字符串）。
    块注释按其中的换行数补回空行，保持行结构 —— 命中时能报出与原文一致的行号。
    """
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i)
            if j < 0:
                break
            i = j  # 换行符本身不消费，交给下面原样输出
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            if j < 0:
                break
            out.append("\n" * s.count("\n", i, j + 2))
            i = j + 2
            continue
        if c in "\"'":
            j = i + 1
            while j < n:
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == c:
                    j += 1
                    break
                j += 1
            out.append(s[i:j])
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check_stripper(strip):
    """剥离器自证。返回 None = 正常，否则返回失败原因。

    这不依赖 Tweak.xm 的内容，是固定样本上的内置断言：
      · 行注释里的 NSLog 必须消失；
      · 块注释里的 os_log 必须消失；
      · 字符串里的 // 与 /* 必须留住（不然会把代码误当注释剥掉 → 假绿）。
    """
    unit = ('a = 1; // NSLog(@"x");\n'
            'b = 2; /* os_log(u, "y"); */\n'
            'c = @"keep // and /* too */";\n')
    su = strip(unit)
    lines = su.split("\n")
    if len(lines) < 3:
        return "剥后行数异常（%d 行），行结构没保住" % len(lines)
    if "NSLog" in lines[0]:
        return "// 注释内容仍留在剥后文本里"
    if "os_log" in lines[1]:
        return "/* */ 注释内容仍留在剥后文本里"
    if "keep // and /* too */" not in su:
        return "字符串字面量被误当注释剥掉"
    return None


def main(path):
    raw = open(path, encoding="utf-8", errors="replace").read()

    why = check_stripper(strip_comments)
    if why:
        sys.stderr.write("  ✗ [stripUnit] 剥注释器自证失败：%s\n" % why)
        sys.stderr.write("  剥离器不可信时任何「没命中」都不作数，直接判失败\n")
        return 2

    code = strip_comments(raw)
    hits = []
    for name, pat in PATS:
        for m in re.finditer(pat, code):
            hits.append((code.count("\n", 0, m.start()) + 1, name))
    hits.sort()

    if hits:
        for line, name in hits:
            sys.stderr.write("  ✗ [noDiagnosticLog] 第 %d 行：%s\n" % (line, name))
        sys.stderr.write("  共 %d 处日志调用（v1.1.3 起已移除；可观测性走 HTTP，"
                         "见 /oka-xfer）\n" % len(hits))
        return 1

    print("  ✓ 无诊断日志调用（剥注释后源码；NSLog / os_log / LOG 宏三类均查）")
    print("  ✓ 剥注释器自证通过（注释剥净、字符串留住、行结构保持）")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("用法: check-nolog.py <Tweak.xm>\n")
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv[1]))