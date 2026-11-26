#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 devkit/check-hover-css.py 做反证：这条闸门到底能不能失败。

★ 关键设计：本脚本**不重写**闸门逻辑，而是用 subprocess 直接运行
  devkit/check-hover-css.py —— 也就是 build.sh 构建时运行的那一份。
  这样「破坏样本能不能被抓到」的结论直接对生产生效。
  前一版是在 /c/tmp 里另写一份等价 Python，那只能证明我的理解自洽，
  对真实闸门零约束力（也正是那一版漏掉了 grep 摆设与 transition 误伤）。

判据：
  - 真实 CSS 必须 rc=0
  - 每个破坏样本必须 rc=1，且 stderr 里出现预期的判据编号
  - 每个样本必须先确认「注入真的改了文件」，否则是空操作（假测试）
  - 不该被误伤的东西（进度条的 width 过渡）必须放过

用法：python3 devkit/selftest-hover-gate.py
退出码 0 = 闸门可信；1 = 有破坏样本漏网或误伤。
"""
import os
import re
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ★ 路径一律从本文件位置推出来，不硬编码绝对路径 ——
#   在 CI 的 macOS runner 上 /c/Users/... 根本不存在。
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "devkit", "check-hover-css.py")
CSS = os.path.join(REPO, "web", "oka-wifi.css")
PY = sys.executable

REAL = open(CSS, encoding="utf-8").read()


def run_gate(path):
    r = subprocess.run([PY, GATE, path], capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def tmp_write(s):
    fd, p = tempfile.mkstemp(suffix=".css")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    return p


def drop_media_block(s, needle="(hover: none)"):
    """按花括号配平删掉含 needle 的整个 @media 块。

    ★ 不能用 `re.sub(r'@media \\(hover: none\\)\\s*\\{(?:[^{}]|\\{[^{}]*\\})*\\}')`：
      `[^{}]` 本身就能匹配 `}`，配平会走偏 —— 第一版这么写，结果注入是空操作
      （文件长度没变），整个样本什么都没测到。必须手写计数。
    """
    i = s.find("@media")
    while i != -1:
        if needle not in s[i:s.find("{", i)]:
            i = s.find("@media", i + 1)
            continue
        j = s.find("{", i)
        depth, k = 0, j
        while k < len(s):
            if s[k] == "{":
                depth += 1
            elif s[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        return s[:i] + s[k + 1:]
    return s


problems = []

print("=" * 72)
print("STEP 1  真实 web/oka-wifi.css 必须 rc=0")
print("=" * 72)
rc, out, err = run_gate(CSS)
print("  rc=%d" % rc)
for line in (out + err).strip().splitlines():
    print("    " + line)
if rc != 0:
    problems.append("真实 CSS 未通过闸门（rc=%d）" % rc)

# ---------------- 破坏样本 ----------------
# (名称, 变换函数, 预期出现在 stderr 的判据编号)
cases = []

cases.append((
    "①-a 删掉顶层 `:hover > td` 整条规则（只留 @media 里那条 transparent）",
    lambda s: s.replace(
        "#listing tr.row-file:hover > td {\n  background-color: #f0f6fd;\n}\n", ""),
    "①",
))

cases.append((
    "①-b 把 `:hover > td` 的色值改成 transparent（等于没上色）",
    lambda s: s.replace(
        "#listing tr.row-file:hover > td {\n  background-color: #f0f6fd;\n}",
        "#listing tr.row-file:hover > td {\n  background-color: transparent;\n}"),
    "①",
))

cases.append((
    "②-a 悬停色改成和选中色一样（#e8f3ff）",
    lambda s: s.replace("background-color: #f0f6fd;", "background-color: #e8f3ff;"),
    "②",
))

cases.append((
    "②-b tr 悬停色与 td 悬停色不一致（整行会被切成两段）",
    lambda s: s.replace(
        "#listing tr.row-file:hover > td {\n  background-color: #f0f6fd;\n}",
        "#listing tr.row-file:hover > td {\n  background-color: #dde9f8;\n}"),
    "②",
))

cases.append((
    "③ 把 transition 改成 all",
    lambda s: s.replace(
        "transition: background-color 120ms ease-out;",
        "transition: all 120ms ease-out;"),
    "③",
))

cases.append((
    "④ 删掉 @media (hover: none) 整块",
    lambda s: drop_media_block(s, "(hover: none)"),
    "④",
))

cases.append((
    "④-b @media(hover:none) 里改成 opaque 色（触屏降级实际没生效）",
    lambda s: s.replace(
        "  #listing tr.row-file:hover > td {\n    background-color: transparent;\n  }",
        "  #listing tr.row-file:hover > td {\n    background-color: #f0f6fd;\n  }"),
    "④",
))

cases.append((
    "⑤ 选中色重复定义第二个来源（反证会失效）",
    lambda s: s + "\n#listing tr.row-file.oka-selected > td { background-color: #e8f3ff; }\n",
    "⑤",
))

print("")
print("=" * 72)
print("STEP 2  破坏样本必须全部被抓到")
print("=" * 72)

for name, fn, expect in cases:
    mut = fn(REAL)
    print("")
    print("--- 注入：%s ---" % name)
    if mut == REAL:
        print("  [SETUP FAIL] 注入没有改变文件内容，这个样本什么都没测到")
        problems.append("%s：注入是空操作" % name)
        continue
    print("  长度 %d → %d" % (len(REAL), len(mut)))
    p = tmp_write(mut)
    try:
        rc, out, err = run_gate(p)
    finally:
        os.remove(p)
    caught = rc != 0 and expect in err
    if not caught:
        problems.append("%s：未被抓到（rc=%d，期望判据 %s）" % (name, rc, expect))
    print("  [%s] rc=%d" % ("CAUGHT" if caught else "MISSED", rc))
    for line in err.strip().splitlines():
        print("      " + line)

# ---------------- 边界：progress-bar 的 width 过渡不能被误伤 ----------------
print("")
print("=" * 72)
print("STEP 3  回归：不该被误伤的东西")
print("=" * 72)

# 3a. 既有的 progress-bar 宽度过渡必须放过（第一版扫全文件时被误伤 → 卡住发布）
assert "transition: width .12s linear;" in REAL
print("  [OK] 真实 CSS 含 `.progress-bar { transition: width .12s linear }` —— 未被误伤")

# 3b. 给 progress-bar 再加一条别的过渡，仍应放过（证明范围限定真的只看 .row-file）
mut = REAL.replace(
    "transition: width .12s linear;",
    "transition: width .12s linear, opacity .2s ease;")
if mut == REAL:
    print("  [SETUP FAIL] 无法构造 progress-bar 样本")
    problems.append("progress-bar 样本注入是空操作")
else:
    p = tmp_write(mut)
    try:
        rc, out, err = run_gate(p)
    finally:
        os.remove(p)
    if rc != 0:
        print("  [MISSED/误伤] 进度条的多属性过渡被误判为违规：%s" % err.strip())
        problems.append("progress-bar 多属性过渡被误伤")
    else:
        print("  [OK] 进度条 `transition: width ..., opacity ...` 不被误伤（范围确实限定在 .row-file）")

# 3c. 把 .row-file 的 transition 整段删掉 → 必须报 ③（切换硬跳）
mut = REAL.replace("  transition: background-color 120ms ease-out;\n", "")
if mut == REAL:
    print("  [SETUP FAIL] 无法构造「删 transition」样本")
    problems.append("删 transition 样本注入是空操作")
else:
    p = tmp_write(mut)
    try:
        rc, out, err = run_gate(p)
    finally:
        os.remove(p)
    caught = rc != 0 and "③" in err
    print("  [%s] 删掉 .row-file 的 transition → rc=%d %s"
          % ("CAUGHT" if caught else "MISSED", rc, err.strip()[:80]))
    if not caught:
        problems.append("删掉 .row-file transition 未被抓到")

# ---------------- 一致性：build.sh 确实调用同一个脚本 ----------------
print("")
print("=" * 72)
print("STEP 4  build.sh 调用的必须是本脚本（同一份代码）")
print("=" * 72)
bsraw = open(os.path.join(REPO, "devkit", "build.sh"), encoding="utf-8").read()


def strip_sh_comments(s):
    """剥掉 shell 注释行（行首空白后 # 开头的整行）。

    ★ 必须剥。build.sh 的注释里我写了「第一版这里写的是 grep -qF ...」
      来记录踩过的坑，那段文本是**文档**不是**代码**。不剥注释就直接搜，
      会把「记录了旧做法」判成「代码还留着旧做法」—— 这正是本次全程在防的
      「判据盯源码长相，不盯实际行为」。
    """
    out = []
    for line in s.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


bs = strip_sh_comments(bsraw)
needle = 'python3 "$ROOT/devkit/check-hover-css.py" "$ROOT/web/oka-wifi.css"'
if needle not in bs:
    print("  [FAIL] build.sh 没有调用 check-hover-css.py —— 验证的与构建用的不是同一份")
    problems.append("build.sh 未调用 check-hover-css.py")
else:
    print("  [OK] build.sh 调用：%s" % needle)

# 旧的内联判据必须已经从「4a-5 这一段的可执行部分」删干净。
# ★ 范围必须限定在 4a-5 段内。第一版拿 `src = re.sub(r'/\*.*?\*/', ...)`
#   这个字符串搜全文件，结果命中 v1.1.8「导航式下载」闸门里**合法存在**的
#   注释剥离代码（build.sh:381），误判成「行悬停还留着旧内联判据」。
#   过宽的字符串匹配就是这样：判据盯源码长相不盯实际行为，必然误伤。
#
# 定位方式：按行扫**原始文本**（bsraw，不是剥过注释的 bs——段标题本身就是
# 注释，在 bs 里已经被剥掉，从 bs 里找段标题必然定位失败，那是本脚本第一版
# 的错，报出来的 "定位不到 4a-5 段" 完全是自己的缺陷）。
# 从 `# ---------- 4a-5)` 起，到下一个 `# ---------- ` 形态的段标题为止。
lines = bsraw.splitlines()
seg_lines, in_seg = [], False
for line in lines:
    if re.match(r"^# -+ 4a-5\)", line):
        in_seg = True
        continue
    if in_seg and re.match(r"^# -+ \S", line):
        break
    if in_seg:
        seg_lines.append(line)
seg_raw = "\n".join(seg_lines) if in_seg else ""
# 残留判据要在**剥注释后**的可执行部分上查：注释里记录「第一版用过 grep -qF」
# 那是文档，不是代码。
seg = strip_sh_comments(seg_raw)

if not seg_raw:
    print("  [FAIL] 在 build.sh 里定位不到 4a-5 段落 —— 段标题可能被改名，本检查失效")
    problems.append("build.sh 里定位不到 4a-5 段")
else:
    print("  4a-5 段：%d 行（剥注释后 %d 行可执行）"
          % (len(seg_lines), len(seg.splitlines())))

stale_list = [
    "grep -qF '#listing tr.row-file:hover > td {'",
    "hov = re.search(r'\\.row-file:hover",
    "sel = re.search(r'\\.row-file\\.oka-selected",
]
stale_hit = []
for stale in stale_list:
    for ln, line in enumerate(seg.splitlines(), 1):
        if stale in line:
            stale_hit.append((stale, ln, line.strip()))
if stale_hit:
    for stale, ln, line in stale_hit:
        print("  [FAIL] 4a-5 段内第 %d 行仍含旧内联判据：%r" % (ln, line[:90]))
        problems.append("4a-5 段残留旧内联判据：%s" % stale)
else:
    print("  [OK] 4a-5 段内的旧内联判据已清除（%d 行，其它段的同类代码不受影响）"
          % len(seg.splitlines()))


def seg_has_stale(seg_text):
    for stale in stale_list:
        for line in seg_text.splitlines():
            if stale in line:
                return True
    return False


# ★ 这一段自己也要能失败。否则「定位不到 4a-5 段 → seg 为空 → 找不到旧字符串
#   → 报 OK」就是一条永真判据。这里往段文本里塞一条旧字符串，必须被抓到。
if seg:
    fake = seg + "\nif ! grep -qF '#listing tr.row-file:hover > td {' \"$ROOT/web/oka-wifi.css\"; then\n"
    if not seg_has_stale(fake):
        print("  [FAIL] 残留检查抓不到人造的旧判据 —— 这段检查是装饰")
        problems.append("4a-5 残留检查无法失败（永真判据）")
    else:
        print("  [OK] 反向验证：人造的旧判据能被抓到（该检查不是永真）")
else:
    problems.append("4a-5 段为空，无法做反向验证")

print("")
print("=" * 72)
print("RESULT problems=%d" % len(problems))
for p in problems:
    print("  - %s" % p)
print("VERDICT " + ("FAIL" if problems else "PASS"))
sys.exit(1 if problems else 0)