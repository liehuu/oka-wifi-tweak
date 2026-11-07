#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen-webassets.py 的反向注入自测：证明闸门会失败，而不是永远通过。

三段式：注入成功（临时副本真的被改了）→ 该报的报了（非零退出 / 解码不一致）
        → 原始资源复跑干净。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "devkit", "gen-webassets.py")
SRC = os.path.join(ROOT, "web")

spec = importlib.util.spec_from_file_location("genweb", GEN)
genweb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(genweb)

results = []


def run_gen(webdir, out):
    return subprocess.run([sys.executable, GEN, webdir, out],
                          capture_output=True, text=True)


def make_web(td):
    wd = os.path.join(td, "web")
    os.makedirs(wd)
    shutil.copyfile(os.path.join(SRC, "oka-wifi.js"), os.path.join(wd, "oka-wifi.js"))
    shutil.copyfile(os.path.join(SRC, "oka-wifi.css"), os.path.join(wd, "oka-wifi.css"))
    return wd


def check(name, mutate, expect_fail):
    with tempfile.TemporaryDirectory() as td:
        wd = make_web(td)
        injected = mutate(wd) if mutate else None
        out = os.path.join(td, "OkaWebAssets.h")
        p = run_gen(wd, out)
        got_fail = (p.returncode != 0)
        ok = (got_fail == expect_fail)
        # 注入类用例必须确认副本真的变了，否则是「没注入所以没报」的假通过
        if injected is not None and not injected:
            ok = False
        msg = (p.stderr.strip() or p.stdout.strip()).splitlines()
        detail = msg[-1] if msg else "(no output)"
        results.append((name, ok, "exit=%d %s" % (p.returncode, detail[:110])))


def inject_close_script(wd):
    p = os.path.join(wd, "oka-wifi.js")
    with open(p, "ab") as f:
        f.write(b"\nvar x = '</script>';\n")
    return os.path.getsize(p) > 0


def inject_template_token(wd):
    p = os.path.join(wd, "oka-wifi.css")
    with open(p, "ab") as f:
        f.write(b"\n/* %epilogue% */\n")
    return b"%epilogue%" in open(p, "rb").read()


def delete_js(wd):
    os.remove(os.path.join(wd, "oka-wifi.js"))
    return not os.path.exists(os.path.join(wd, "oka-wifi.js"))


def empty_css(wd):
    open(os.path.join(wd, "oka-wifi.css"), "wb").close()
    return os.path.getsize(os.path.join(wd, "oka-wifi.css")) == 0


def nothing(wd):
    return None


check("正向：原始资源应通过", nothing, False)
check("反向：JS 含 </script 应拒绝", inject_close_script, True)
check("反向：CSS 含 %epilogue% 应拒绝", inject_template_token, True)
check("反向：缺 oka-wifi.js 应拒绝", delete_js, True)
check("反向：oka-wifi.css 为空应拒绝", empty_css, True)

# 解码闸门：把已生成头里的一个转义字节改掉，decode_array 必须还原出不同字节
with tempfile.TemporaryDirectory() as td:
    out = os.path.join(td, "OkaWebAssets.h")
    p = run_gen(SRC, out)
    with open(out, "rb") as f:
        header = f.read().decode("ascii")
    js_raw = open(os.path.join(SRC, "oka-wifi.js"), "rb").read()
    before = genweb.decode_array(header, "kOkaWebJS")
    # 把第一个字面量里的一个八进制转义 \\0NN 改成不同值（不破坏语法）
    import re
    m = re.search(r'\\(0[0-7][0-7])', header)
    altered = None
    if m:
        old = m.group(1)
        new = "0" + ("0" if old[1] != "0" else "1") + old[2]
        altered = header[:m.start(1)] + new + header[m.end(1):]
    if not altered or altered == header:
        results.append(("反向：篡改 C 转义应被解码检出", False, "没找到可篡改的八进制转义"))
    else:
        after = genweb.decode_array(altered, "kOkaWebJS")
        detected = (before == js_raw) and (after != js_raw)
        results.append(("反向：篡改 C 转义应被解码检出", detected,
                        "原解码一致=%s 篡改后不同=%s" % (before == js_raw, after != js_raw)))

# 原始资源在临时副本里复跑干净
with tempfile.TemporaryDirectory() as td:
    wd = make_web(td)
    p = run_gen(wd, os.path.join(td, "OkaWebAssets.h"))
    results.append(("还原后复跑干净", p.returncode == 0, "exit=%d" % p.returncode))

passed = 0
for name, ok, detail in results:
    print("%s %-38s %s" % ("✓" if ok else "✗", name, detail))
    if ok:
        passed += 1

print("\n%d/%d 用例符合预期" % (passed, len(results)))
sys.exit(0 if passed == len(results) else 1)
