#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-nolog.py 的自测：正反两向反证（不需要 macOS、不需要编译）。

为什么必须有
============
这条闸门在 v1.6.12 之前是 build.sh 里的一段裸 grep，两个缺陷：

  · 没剥注释 → v1.6.12 的说明性注释把它打死，三个 job 全 red（假 FAIL）；
  · 只查 NSLog → 注释从 v1.1.3 起写着「禁止 NSLog / os_log」，
    os_log 却一直没人查（永不失败的规则）。

修完之后如果只验「真源码通过」，就只证明了「不误报」，没证明「会红」。
两个方向都必须测：

  should_pass 样本：注释里提到 NSLog / os_log / LOG( —— 必须通过（不误报）
   should_fail 样本：真写了调用 —— 必须失败（不空转）

★ 每条 should_fail 都断言**报出的行号**：只判 exit 码的话，剥离器把整段
  代码剥没了也会"通过"，那种绿是假的。

用法
====
    python3 devkit/selftest-nolog.py <Tweak.xm>

退出码：0 全部符合预期；1 有偏差（打印期望 vs 实际）。
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "check-nolog.py")
PY = sys.executable

# 锚点：所有样本都插在这两行之间（一个真正的函数定义之前），
# 这样插入的代码落在文件中部，行号断言才有意义（不是首行特例）。
ANCHOR = "static id OkaXferDiagResponse(void) {"


def run_check(path):
    """跑 check-nolog.py，返回 (exit_code, stderr_text)。"""
    p = subprocess.run([PY, CHECK, path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stderr or "")


def base_line(src):
    """锚点行在原文里的行号（1 起）。样本会把代码插在锚点前，用于算期望行号。"""
    idx = src.index(ANCHOR)
    return src.count("\n", 0, idx) + 1


def main(src_path):
    raw = open(src_path, encoding="utf-8", errors="replace").read()
    if ANCHOR not in raw:
        print("✗ 锚点不在当前 Tweak.xm 里（样本过期，需更新）：%r" % ANCHOR)
        return 1
    anchor_line = base_line(raw)

    fails = []
    tmpdir = tempfile.mkdtemp(prefix="nolog-")

    def write(name, text):
        p = os.path.join(tmpdir, name + ".xm")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return p

    def inject(code, times=1):
        """把 code 插到锚点之前。返回 (新文本, 插入代码首行的行号)。"""
        head, tail = raw.split(ANCHOR, 1)
        new = head + code + "\n" + ANCHOR + tail
        # 插入块从 head 的最后一行之后开始；head 里的换行数即下一行行号
        first_line = head.count("\n") + 1
        return new, first_line

    def expect(name, text, want_rc, want_line=None, want_word=None):
        p = write(name, text)
        rc, err = run_check(p)
        ok = True
        if rc != want_rc:
            fails.append("样本「%s」退出码 %d，期望 %d\n    stderr=%r"
                         % (name, rc, want_rc, err[:400]))
            ok = False
        if want_line is not None and ok:
            if ("第 %d 行" % want_line) not in err:
                fails.append("样本「%s」没报出期望行号 %d\n    stderr=%r"
                             % (name, want_line, err[:400]))
                ok = False
        if want_word is not None and ok:
            if want_word not in err:
                fails.append("样本「%s」预期报出 %r，实际没有\n    stderr=%r"
                             % (name, want_word, err[:400]))
                ok = False
        return ok

    # ---------- 良性：注释里提到日志函数名，绝不能误报 ----------
    cases_pass = [
        ("行注释里写 NSLog",
         '// 这里禁止 NSLog(@"x")，写日志会让构建失败'),
        ("块注释里写 os_log",
         '/* 产物里不能出现 os_log(u, "y") */'),
        ("块注释里写 LOG 宏",
         '/* LOG(@"x") 是禁止的 */'),
        ("三种名字同时出现在注释里",
         '// NSLog\n/* os_log */\n// LOG(\n'),
        ("字符串里写 NSLog（不是调用，但也是代码里的字符串）",
         'static NSString *okaHint(void) { return @"不要用 NSLog 写日志"; }'),
    ]
    for name, code in cases_pass:
        text, _ = inject(code)
        expect("pass-" + name, text, 0)

    # ---------- 破坏：真写调用，必须红，且行号准确 ----------
    cases_fail = [
        ("真调用 NSLog", 'static void okaLeak(void) { NSLog(@"leak"); }', "NSLog"),
        ("真调用 os_log",
         'static void okaLeak(void) { os_log(OS_LOG_DEFAULT, "leak"); }', "os_log"),
        ("真调用 os_log_error（同族）",
         'static void okaLeak(void) { os_log_error(OS_LOG_DEFAULT, "leak"); }', "os_log"),
        ("真调用 LOG 宏", 'static void okaLeak(void) { LOG(@"leak"); }', "LOG宏"),
        ("NSLog 带空格不换行",
         'static void okaLeak(void) { NSLog (@"leak"); }', "NSLog"),
    ]
    for name, code, word in cases_fail:
        text, first_line = inject(code)
        expect("fail-" + name, text, 1, want_line=first_line, want_word=word)

    # ---------- 破坏：注释 + 真调用混在同一行，必须只报真调用 ----------
    text, first_line = inject('/* NSLog */ static void okaLeak(void) { NSLog(@"x"); }')
    expect("fail-注释与真调用同行", text, 1, want_line=first_line, want_word="NSLog")

    # ---------- 破坏：剥注释器被破坏时不能放行（exit 2 而不是 0） ----------
    # 这里直接篡改 check-nolog.py 的副本，把 strip_comments 换成不剥注释的
    # 恒等函数。若主流程没有 check_stripper 自证，注释里的 NSLog 就会误报，
    # 而「不剥」的副本仍能通过真源码 —— 那种通过毫无意义。
    with open(CHECK, encoding="utf-8") as f:
        checker_src = f.read()
    broken = checker_src.replace(
        "def strip_comments(s):",
        "def strip_comments(s):\n    return s   # 故意不剥（自证必须拦住）\n", 1)
    if broken == checker_src:
        fails.append("样本构造失败：没能在 check-nolog.py 里插入「不剥注释」")
    else:
        bp = os.path.join(tmpdir, "broken-check.py")
        with open(bp, "w", encoding="utf-8", newline="\n") as f:
            f.write(broken)
        p = subprocess.run([PY, bp, src_path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if p.returncode != 2:
            fails.append("剥注释器坏掉时应 exit 2（判据不可信），实际 %d\n"
                         "    stderr=%r" % (p.returncode, (p.stderr or "")[:400]))
        else:
            print("  ✓ 破坏「剥注释器退化」→ stripUnit 变红（exit 2）")

    # ---------- 汇总 ----------
    n_pass = len(cases_pass)
    n_fail = len(cases_fail) + 1  # +1 是「注释与真调用同行」
    print("  ✓ 良性样本 %d 个全部通过（注释/字符串里提到日志函数名不误报）" % n_pass)
    if not fails:
        print("  ✓ 破坏样本 %d 个全部变红（含行号与命中词断言）" % n_fail)
        print("  反证完成：%d 良性 + %d 破坏 + 1 剥离器退化，全部符合预期"
              % (n_pass, n_fail))
        return 0

    for f in fails:
        print("  ✗ " + f)
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("用法: selftest-nolog.py <Tweak.xm>\n")
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv[1]))