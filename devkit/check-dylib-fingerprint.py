#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""v1.6.11 产物层指纹：dylib 里必须有 mangled 类名与四个状态文案。

为什么单独成文件（而不是写在 build.sh 里）：
  · 中文文案在 Mach-O 里是 **UTF-16LE**（ObjC 的 @"..." 常量），用
    `grep -a 未开启` 按 UTF-8 搜必然找不到 —— 首轮 CI 的假失败就是这个：
    编译明明是成功的（`✓ v1.6.11 导航栏开关：11 条判据 + 18/18 破坏反证`），
    却报 `✗ dylib 缺 未开启`。
  · 而 macOS 自带 BSD grep **没有 -P**，不能用 `grep -P '\x..'` 匹配字节。
  · 本工程纪律（build.sh 4a-5）：校验逻辑外置成脚本，不写内联 heredoc。

判据不是「字符串出现在二进制某处」这么弱 —— 那样把文案写进一个没人用的
死数组也能过。这里同时要求 `okaApplyState` 这个 selector 存在：它只有在
本类里被真正调用过（`[self okaApplyState]`）才会进 __objc_methname 的
引用表，缺它就说明文案只是死字符串、没人渲染。

另注：本文件开头的 docstring 必须带 r 前缀，否则下文出现的字节转义示例
会被 Python 当转义序列解析，直接 SyntaxError 让整个脚本跑不起来
（这个 bug 真发生过：反证脚本一跑，5 个破坏样本全是「脚本崩了」造成的
假绿，并不是判据真的拦住了）。
"""
import sys

# 必须存在于产物里的标识
REQUIRED_ASCII = [
    ("_TtC7Blinker22FilesBrowserController",
     "mangled 类名（缺它则开关挂到错的类上，界面毫无变化）"),
    ("okaApplyState",
     "状态渲染 selector（缺它则文案只是死字符串，没人渲染）"),
]

# 四个状态文案：UTF-16LE 与 UTF-8 两种编码都接受（不同编译路径可能不同）
REQUIRED_CJK = ["未开启", "待连接", "已连接", "已停止"]


def find_cjk(raw: bytes, s: str) -> str:
    """返回命中的编码名，都没命中返回空串。"""
    if s.encode("utf-16-le") in raw:
        return "utf-16-le"
    if s.encode("utf-8") in raw:
        return "utf-8"
    return ""


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: check-dylib-fingerprint.py <dylib>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        print("✗ 读不到 dylib：%s" % e, file=sys.stderr)
        return 1
    if not raw:
        print("✗ dylib 是空文件：%s" % path, file=sys.stderr)
        return 1

    fails = []
    for sym, why in REQUIRED_ASCII:
        if sym.encode() not in raw:
            fails.append("缺 %s —— %s" % (sym, why))
    for s in REQUIRED_CJK:
        enc = find_cjk(raw, s)
        if not enc:
            fails.append("缺状态文案 %s（UTF-16LE / UTF-8 都没有）" % s)

    if fails:
        print("✗ dylib 指纹不全（%d 项）：" % len(fails), file=sys.stderr)
        for f in fails:
            print("   · %s" % f, file=sys.stderr)
        return 1

    print("✓ dylib 指纹齐全：mangled 类名 + okaApplyState + 四个状态文案")
    return 0


if __name__ == "__main__":
    sys.exit(main())