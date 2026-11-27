#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 devkit/check-shortcut-clear.py 做反证：这条闸门到底能不能失败。

★ 关键设计：本脚本**不重写**闸门逻辑，而是用 subprocess 直接运行
  devkit/check-shortcut-clear.py —— 也就是 build.sh 构建时运行的那一份。
  这样「破坏样本能不能被抓到」的结论直接对生产生效。
  另写一份等价实现只能证明我的理解自洽，对真实闸门零约束力。

判据：
  - 真实 Tweak.xm 必须 rc=0
  - 每个破坏样本必须 rc=1，且实际触发的判据键集合**精确等于**预期集合
    （多了是误伤，少了是漏网）
  - 每个样本必须先确认「注入真的改了文件」，否则是空操作（假测试）
  - 良性样本（只改注释）必须 rc=0，证明闸门不误伤
  - 全程不许碰磁盘上的 Tweak.xm：变异只发生在临时文件里
    （前后 sha256 比对证明）
  - 复原复跑必须干净 rc=0

用法：python3 devkit/selftest-shortcut-gate.py
退出码 0 = 闸门可信；1 = 有破坏样本漏网、误伤或脚本自身 SETUP FAIL。
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ★ 路径一律从本文件位置推出来，不硬编码绝对路径 ——
#   在 CI 的 macOS runner 上 /c/Users/... 根本不存在。
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "devkit", "check-shortcut-clear.py")
TWEAK = os.path.join(REPO, "Tweak.xm")
PY = sys.executable

REAL = open(TWEAK, encoding="utf-8").read()
KEY_RE = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")


def run_gate(text):
    """把 text 落成临时文件，跑**真实**闸门，返回 (rc, 全部输出)。

    写盘后读回比对：证明 subprocess 拿到的就是这个文本，
    不是空操作（空操作的「测试」比没有测试更糟）。
    """
    fd, tmp = tempfile.mkstemp(suffix=".xm", prefix="gate-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        back = open(tmp, encoding="utf-8").read()
        if back != text:
            return None, "临时文件写读不一致"
        p = subprocess.run([PY, GATE, tmp], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    finally:
        os.remove(tmp)


def grab(pattern, what):
    """从真实源码里抓一段原文（缩进跟着源码走，不硬编码空格）。

    抓不到就 SETUP FAIL：说明源码写法变了，本脚本要跟着改，
    而不是换个宽松正则把样本糊过去。
    """
    m = re.search(pattern, REAL)
    if not m:
        print("[SETUP FAIL] 从 Tweak.xm 取不到 %s —— 写法变了，本脚本要跟着改" % what)
        sys.exit(1)
    return m.group(0)


def build_cases():
    """返回 [(名称, old, new, 预期触发键集合)]。

    old/new 全部来自源码真实片段（grab），每个 old 在全文必须恰好出现一次。
    """
    # 关开关分支的「写回 + return」两行组合（1731-1732）。
    pair = grab(r"[ \t]*if \(hadOurs\) app\.shortcutItems = keep;\n[ \t]*return r;",
                "hadOurs 写回行 + return 行")
    write_line, ret_line = pair.split("\n")
    # hook 注册的两行锚点（2281-2282）。
    hook = grab(r"[ \t]*HookInstanceMethod\(appDel,\n"
                r"[ \t]*@selector\(application:didFinishLaunchingWithOptions:\),",
                "didFinishLaunching hook 注册两行")
    # v1.6.3 基线形态的清除块（1723-1733 整块）。
    # ★ 用 [^{}]* 而不是 .*?：块内没有嵌套花括号，字符类天然不越过块边界；
    #   且字符类含换行，不需要 re.S（`.` 不加 re.S 匹配不到换行，第一版就栽在这）。
    m7 = re.search(r"if \(!gEnableShortcut\) \{[^{}]*\}", REAL)
    if not m7:
        print("[SETUP FAIL] 从 Tweak.xm 取不到 整个 if (!gEnableShortcut) 清除块"
              " —— 写法变了，本脚本要跟着改")
        sys.exit(1)
    old_block = m7.group(0)
    line_start = REAL.rfind("\n", 0, m7.start()) + 1
    indent = REAL[line_start:m7.start()]
    comment = grab(r"// ★ 修：关掉开关时[^\n]*", "★ 修 注释行")

    cases = [
        # M1 去掉 hadOurs 守卫 → 关开关后无条件写回（每次冷启动都做无意义持久化）
        ("M1 去掉 hadOurs 守卫",
         write_line, write_line.replace("if (hadOurs) ", ""),
         {"hadOursGuard"}),
        # M2 写回排到 return 之后 → 永远不会执行的装饰代码
        ("M2 写回排在 return 之后",
         pair, ret_line + "\n" + write_line,
         {"clearBeforeReturn"}),
        # M3 把 hook 也套进 if (gEnableShortcut) → 关开关后 hook 不挂载，清除分支不可达
        ("M3 hook 套回 if (gEnableShortcut)",
         hook, hook.replace("HookInstanceMethod(appDel,",
                            "if (gEnableShortcut) HookInstanceMethod(appDel,", 1),
         {"gateCountOne", "gateAfterHook"}),
        # M4 删掉 else 保留分支 → 写回时把别人快捷项一起清掉
        ("M4 删掉 else 保留分支",
         grab(r"[ \t]*else \[keep addObject:it\];\n", "else 保留分支行"), "",
         {"keepsForeign"}),
        # M5 关开关分支只 return 不写回 → 等于没修（v1.6.3 基线行为）
        ("M5 关开关分支只 return 不写回",
         pair, ret_line,
         {"clearWritesKeep", "clearBeforeReturn", "hadOursGuard"}),
        # M6 启用分支改回整体重写 items → 丢掉他人快捷项
        ("M6 插入改回 items",
         grab(r"\[keep insertObject:MakeShortcutItem\(\) atIndex:0\];", "keep 插入行"),
         "[items insertObject:MakeShortcutItem() atIndex:0];",
         {"keepInsert"}),
        # M7 整个退回 v1.6.3 基线 → 撤销本次修复必须构建失败
        ("M7 整个退回 v1.6.3 基线",
         old_block, indent + "if (!gEnableShortcut) return r;",
         {"oldGuardGone", "clearBlockFound"}),
    ]
    for name, old, _new, _exp in cases:
        if REAL.count(old) != 1:
            print("[SETUP FAIL] %s：注入锚点在 Tweak.xm 里出现 %d 次（必须恰好 1 次）"
                  % (name, REAL.count(old)))
            sys.exit(1)
    return cases


BENIGN = [
    # 只改注释文字与空行：闸门必须放过（证明它不是什么都拦）。
    ("B1 只改注释文字与空行",
     grab(r"// ★ 修：关掉开关时[^\n]*", "★ 修 注释行"),
     None),  # new 在 main 里组装（要保留前导缩进）
]


def main():
    before = hashlib.sha256(open(TWEAK, "rb").read()).hexdigest()

    # 1) 真实源码必须通过
    rc, out = run_gate(REAL)
    if rc != 0:
        print("✗ 真实 Tweak.xm 没过闸门（rc=%d）——先修闸门或源码，别谈反证" % rc)
        print(out)
        return 1
    print("  ✓ 真实 Tweak.xm 通过闸门")

    bad = 0
    cases = build_cases()

    # 2) 破坏样本：必须 rc=1 且实际触发键集合 == 预期集合
    for name, old, new, expect in cases:
        mutated = REAL.replace(old, new, 1)
        if mutated == REAL:
            print("  ✗ %s：注入是空操作（替换前后一致）——假测试" % name)
            bad += 1
            continue
        rc, out = run_gate(mutated)
        got = set(KEY_RE.findall(out))
        if rc != 1:
            print("  ✗ %s：闸门 rc=%d（应为 1）——漏网" % (name, rc))
            print(out)
            bad += 1
            continue
        if got != expect:
            print("  ✗ %s：触发键不符\n      预期 %s\n      实际 %s"
                  % (name, sorted(expect), sorted(got)))
            print(out)
            bad += 1
            continue
        print("  ✓ %-28s → %s" % (name, "/".join(sorted(got))))

    # 3) 良性样本：必须 rc=0（闸门不误伤）
    for name, old, _new in BENIGN:
        indent = old[:len(old) - len(old.lstrip())]
        new = indent + "// 良性变更测试：重写这句注释不影响任何判据。"
        mutated = REAL.replace(old, new, 1)
        if mutated == REAL:
            print("  ✗ %s：注入是空操作" % name)
            bad += 1
            continue
        rc, out = run_gate(mutated)
        if rc != 0:
            print("  ✗ %s：良性变更被误伤（rc=%d）" % (name, rc))
            print(out)
            bad += 1
            continue
        print("  ✓ %-28s → 放过" % name)

    # 4) 全程不许碰磁盘上的 Tweak.xm
    after = hashlib.sha256(open(TWEAK, "rb").read()).hexdigest()
    if before != after:
        print("  ✗ 磁盘上的 Tweak.xm 被改动了（sha256 前后不一致）")
        bad += 1
    else:
        print("  ✓ 磁盘 Tweak.xm 未被改动（sha256 前后一致）")

    # 5) 复原复跑必须干净
    rc, out = run_gate(REAL)
    if rc != 0:
        print("  ✗ 复原复跑不干净（rc=%d）" % rc)
        print(out)
        bad += 1
    else:
        print("  ✓ 复原复跑干净")

    print("")
    if bad:
        print("selftest-shortcut-gate: FAIL（%d 项不符）" % bad)
        return 1
    print("selftest-shortcut-gate: PASS（%d 个破坏样本全部触发预期判据，%d 个良性样本全部放过）"
          % (len(cases), len(BENIGN)))
    return 0


if __name__ == "__main__":
    sys.exit(main())