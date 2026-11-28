#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-dylib-fingerprint.py 的反证（用一个假 dylib 做正/反向样本）。

为什么不用真 dylib：本地没有 iOS SDK，编不出 arm64 Mach-O；而这条判据
的本质是「在字节流里找某种编码的字面量」，用构造样本就能精确验证
**它在该失败时会不会失败**——这正是首轮 CI 假失败的根因所在
（用 UTF-8 去搜 UTF-16LE 存的串，判据自己错了，却报成源码缺东西）。

样本：
  G1 良性：UTF-16LE 存四个文案 + 两个 ASCII 标识 → 必须 PASS
  G2 良性：UTF-8 存四个文案（不同编译路径）        → 必须 PASS
  B1 缺一个中文文案（UTF-16LE）                    → 必须 FAIL
  B2 文案改成同形近字（「已连接」→「已连线」）      → 必须 FAIL
  B3 缺 okaApplyState（文案成了没人渲染的死字符串） → 必须 FAIL
  B4 缺 mangled 类名                                → 必须 FAIL
  B5 空文件                                         → 必须 FAIL
  B6 文件不存在                                     → 必须 FAIL

用法：python3 devkit/selftest-dylib-fingerprint.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "check-dylib-fingerprint.py")

MANGLED = b"_TtC7Blinker22FilesBrowserController"
APPLY = b"okaApplyState"
CJK = ["未开启", "待连接", "已连接", "已停止"]


def make(utf16=True, cjk=None, with_apply=True, with_mangled=True):
    """构造一个假 dylib 字节流（只需是可搜索的字节，不必是真 Mach-O）。"""
    cjk = CJK if cjk is None else cjk
    out = bytearray(b"\xcf\xfa\xed\xfe" + b"\x00" * 12)   # Mach-O magic 占位
    if with_mangled:
        out += MANGLED + b"\x00"
    if with_apply:
        out += APPLY + b"\x00"
    for s in cjk:
        out += s.encode("utf-16-le") if utf16 else s.encode("utf-8")
        out += b"\x00" * 4
    out += b"\x00" * 32
    return bytes(out)


def run(path):
    r = subprocess.run([sys.executable, GATE, path],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main() -> int:
    if not os.path.exists(GATE):
        print("✗ 找不到 %s" % GATE)
        return 1

    bad = 0
    tmpdir = tempfile.mkdtemp(prefix="dylibfp-")
    try:
        # ---- 良性 ---------------------------------------------------------
        for name, content in (
                ("G1 良性 UTF-16LE 文案", make(utf16=True)),
                ("G2 良性 UTF-8 文案", make(utf16=False))):
            p = os.path.join(tmpdir, "g.bin")
            open(p, "wb").write(content)
            rc, out = run(p)
            if rc == 0:
                print("  ✓ %-32s → PASS（期望 PASS）" % name)
            else:
                print("  ✗ %-32s → rc=%d（期望 0）\n%s" % (name, rc, out))
                bad += 1

        # ---- 破坏 ---------------------------------------------------------
        cases = [
            ("B1 缺一个中文文案", make(cjk=["未开启", "待连接", "已连接"])),
            ("B2 文案改为近形字", make(cjk=["未开启", "待连接", "已连线", "已停止"])),
            ("B3 缺 okaApplyState", make(with_apply=False)),
            ("B4 缺 mangled 类名", make(with_mangled=False)),
            ("B5 空文件", b""),
        ]
        for name, content in cases:
            p = os.path.join(tmpdir, "b.bin")
            open(p, "wb").write(content)
            rc, out = run(p)
            if rc == 1:
                print("  ✓ %-32s → FAIL（期望 FAIL）：%s"
                      % (name, out.splitlines()[0][:60]))
            else:
                print("  ✗ %-32s → rc=%d（期望 1，未被拦住）" % (name, rc))
                bad += 1

        # B6 文件不存在
        rc, out = run(os.path.join(tmpdir, "nope.bin"))
        if rc == 1:
            print("  ✓ %-32s → FAIL（期望 FAIL）" % "B6 文件不存在")
        else:
            print("  ✗ %-32s → rc=%d（期望 1）" % ("B6 文件不存在", rc))
            bad += 1
    finally:
        for f in os.listdir(tmpdir):
            os.remove(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)

    if bad:
        print("selftest-dylib-fingerprint: FAIL（%d 处不符）" % bad)
        return 1
    print("selftest-dylib-fingerprint: PASS（2 良性 + 6 破坏全部符合预期）")
    return 0


if __name__ == "__main__":
    sys.exit(main())