#!/usr/bin/env python3
# v1.6.10：桌面动态图标（红染绿）功能已整体移除 —— 负向闸门。
#
# 为什么防回流：
#   用户 2026-11-28 反馈「没生效」，注入链路（iconinject helper +
#   CFBundleAlternateIcons + Info.plist 写键）与切换链路
#   （setAlternateIconName:）已整体删除。这套代码静默失效过两代
#   （v1.6.6 装完没反应、v1.6.9.2 门控修正后仍未生效），若哪天有人
#   「顺手加回来」重试，症状是又一年的「装了没反应」—— 所以这里必须
#   由负向闸门钉住：标识符一出现在源码里，构建直接失败。
#
# 判据必须能失败：
#   · 检查对象是**剥注释后**的源码 —— 记录移除范围的注释里本身就写着
#     这些名字（changelog 性质），不剥注释这条闸门会永远 FAIL（假 FAIL
#     比漏报更能把人带偏）。
#   · 反向验证见 devkit/selftest-noicongate.py：1 个良性样本必须 PASS、
#     7 个破坏样本（逐个加回被删的标识符）必须 FAIL。
#
# 用法：python3 check-no-icon-gate.py <Tweak.xm 路径>
#   退出码 0 = 干净；1 = 发现回流（逐条列出）。
import re
import sys

# 被删功能的代码标识符（函数 / 全局变量 / 常量 / 偏好键 / selector）。
# 选词标准：**只可能**来自被删功能的字符串。general 词不列
# （比如 "icon" 会误伤文件列表图标的 oka-*.js 常量）。
FORBIDDEN = [
    "OkaSetAppIcon",          # 图标切换函数本体
    "OkaIconSwitchReady",     # 注入就绪判定
    "OkaHostIsAppStore",      # 宿主装态判定（图标注入门控专用）
    "gEnableSwitchIcon",      # 功能开关全局变量
    "OKA_ICON_NAME_GREEN",    # 备选图标名常量
    "OKA_ICON_KEY_INJECTED",  # Info.plist 注入标记键常量
    "setAlternateIconName",   # 切换 API selector
    "CFBundleAlternateIcons", # 备选图标声明键
    "oka-iconinject",         # 注入 helper 二进制名
    "iconinject-core.h",      # 已删除的共用头
    "oka-icon-common.h",      # 已删除的共用常量头
    '"IconSwitch"',           # 偏好键（ObjC 字面量形式）
]


def strip_comments(src: str) -> str:
    """剥掉 // 与 /* */ 注释；字符串字面量先占位保护。

    本工程的注释里必然出现被删功能的名字（移除说明），
    不剥就会被自己打死 —— 这是本项目判据的既有纪律
    （build.sh 4a-5 的 window.location 负向同理）。
    """
    out, i, n = [], 0, len(src)
    in_str = None
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ('"', "'"):
            in_str = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: check-no-icon-gate.py <源文件>")
        return 2
    src = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    code = strip_comments(src)
    hits = [w for w in FORBIDDEN if w in code]
    if hits:
        print("✗ 桌面动态图标（红染绿）标识符回流（v1.6.10 已整体移除）：")
        for w in hits:
            print("   ·", w)
        print("  排查：Tweak.xm 是否又被加回注入/切换/装态判定代码；"
              "确需恢复请先与用户确认「未生效」的根因。")
        return 1
    print("✓ 无桌面动态图标标识符回流（剥注释判据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())