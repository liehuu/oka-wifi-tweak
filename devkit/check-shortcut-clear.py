#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快捷菜单「关开关即自清」闸门（v1.6.4）。

用法：python3 devkit/check-shortcut-clear.py <Tweak.xm>
退出码 0 = 全部通过；1 = 有判据不满足（stderr 逐条说明，带判据键名）。

修的缺陷（用户实测）：
  插件卸载后长按 App 图标仍显示「开启WiFi传输」，点击无效果。
  根因链：Info.plist 无静态 UIApplicationShortcutItems（写它会破坏签名）→ 运行时
  注入 → 系统按 bundle id 把快捷项持久化在 App 数据容器 → 插件卸载后无人再
  写回，菜单项永驻。而点击回调是插件用 class_addMethod 补的，App 本体未实现
  → 卸载后菜单项变成「点了没反应的空壳」。
  v1.6.3 及以前的原实现是 `if (!gEnableShortcut) return r;` —— 一个字节都不写，
  「关开关再卸载」这条路根本不存在，用户只能删 App 重装。

判据全部跑在**剥注释后**的源码上：
  注释里的同名关键字会污染字符串定位（v1.6.3 的 hover 闸门被自己的注释打出
  6 条假红，根因就是没剥注释），所以先剥再判，并用内置 stripUnit 自证剥离有效。
分支内断言用花括号配对取 body，不用「分支头起 N 字符」的窗口：
  窗口越界会匹配到相邻分支里的同一行赋值（初版就是这么假绿的）。
"""
import re
import sys

# 注册段边界锚点（原文里就是这两行注释）。
REG_A = "// ---- 1: 快捷菜单 ----"
REG_B = "// ---- 3: 电脑网页端增强"


def strip_comments(s):
    """剥掉 // 与 /* */ 注释，保留换行与字符串字面量内容。

    状态机必须认识 @"..." / "..." / '...'：
      · 字符串里的 // 与 /* 不是注释；
      · 注释里的引号不是字符串起点（否则 /* */ 里的一个 " 会把后面全吞进字符串）。
    块注释按其中的换行数补回空行，保持行结构（对定位有帮助，对判据无害）。
    """
    out = []
    i, n = 0, len(s)
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


def extract_block(s, brace_idx):
    """brace_idx 指向 '{'，花括号配对返回 (body, end)。跳过字符串里的括号。"""
    depth = 0
    i, n = brace_idx, len(s)
    while i < n:
        c = s[i]
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
            i = j
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[brace_idx + 1:i], i + 1
        i += 1
    return None, -1


def main(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    src = strip_comments(raw)
    fails = []

    # ---------- stripUnit：剥注释自证 ----------
    # 这条不依赖 Tweak.xm 内容，固定样本内置断言：注释必须被剥掉、字符串必须留住。
    unit = (
        "a = 1; // if (gEnableShortcut) return r;\n"
        "b = 2; /* app.shortcutItems = items; */\n"
        "c = \"keep this // and /* too */ string\";\n"
    )
    su = strip_comments(unit)
    if "if (gEnableShortcut) return r;" in su:
        fails.append(("stripUnit", "剥注释不干净：// 注释内容仍留在剥后文本里"))
    if "app.shortcutItems = items;" in su:
        fails.append(("stripUnit", "剥注释不干净：/* */ 注释内容仍留在剥后文本里"))
    if "keep this // and /* too */ string" not in su:
        fails.append(("stripUnit", "剥过头了：字符串字面量被误删"))

    # ---------- 函数级判据 ----------
    if "my_didFinishLaunching(id self, SEL _cmd" not in src:
        fails.append(("fnFound", "找不到 my_didFinishLaunching(id self, SEL _cmd —— 函数被改名或删除"))

    # 负向：旧形态（v1.6.3 基线）不允许回流。
    if re.search(r"if\s*\(\s*!gEnableShortcut\s*\)\s*return\b", src):
        fails.append(("oldGuardGone",
                      "仍有 `if (!gEnableShortcut) return ...` 旧写法：关开关时一个字节都不写回，"
                      "系统持久化的快捷项永驻，卸载后长按菜单照样显示且点了没反应"))

    # 清除块：必须是 if (!gEnableShortcut) { ... } 花括号块（不是单行 return）。
    blocks = [m.end() - 1
              for m in re.finditer(r"if\s*\(\s*!gEnableShortcut\s*\)\s*\{", src)]
    if not blocks:
        fails.append(("clearBlockFound",
                      "找不到 `if (!gEnableShortcut) {` 清除块（关开关分支不存在或被写成单行）"))

    ok_write = ok_order = ok_had = checked = False
    for bi in blocks:
        body, _ = extract_block(src, bi)
        if body is None:
            continue
        checked = True
        has_write = "app.shortcutItems = keep;" in body
        has_had = "hadOurs" in body
        ret = re.search(r"\breturn\b", body)
        if has_write:
            ok_write = True
        if has_had:
            ok_had = True
        if has_write and ret and body.find("app.shortcutItems = keep;") < ret.start():
            ok_order = True
    if checked:
        if not ok_write:
            fails.append(("clearWritesKeep",
                          "关开关分支里没有 `app.shortcutItems = keep;`：不写回就等于不清除"))
        if not ok_order:
            fails.append(("clearBeforeReturn",
                          "关开关分支里写回排在 return 之后：永远不会执行，属于装饰代码"))
        if not ok_had:
            fails.append(("hadOursGuard",
                          "关开关分支缺 hadOurs 守卫：没有自己那条时也写回，每次冷启动"
                          "都触发一次无意义的持久化写入"))

    # 保留他人快捷项：一次遍历的 else 分支 + 启用分支插入到 keep（不许整体重写 items）。
    if "[keep addObject:it]" not in src:
        fails.append(("keepsForeign",
                      "缺 `else [keep addObject:it]`：一次遍历没把别人的快捷项留下来，"
                      "写回时会把 App 自带项一起清掉"))
    if "[keep insertObject:MakeShortcutItem()" not in src or "[items insertObject:" in src:
        fails.append(("keepInsert",
                      "启用分支没有插入到 keep（或又改回整体重写 items）：会丢掉他人快捷项"))

    if "isEqualToString:SHORTCUT_TYPE" not in src:
        fails.append(("typeMacro", "缺 isEqualToString:SHORTCUT_TYPE：认不出自己那条快捷项"))
    if "MakeShortcutItem()" not in src:
        fails.append(("maker", "缺 MakeShortcutItem()：快捷项构造函数没被调用"))

    # ---------- 注册段判据（hook 必须无条件挂载） ----------
    a = raw.find(REG_A)
    b = raw.find(REG_B)
    if a < 0 or b < 0 or b <= a:
        fails.append(("regFound", "找不到注册段锚点 %r / %r" % (REG_A, REG_B)))
    else:
        reg = strip_comments(raw[a:b])
        i_hook = reg.find("HookInstanceMethod(appDel")
        if i_hook < 0:
            fails.append(("regFound", "注册段里没有 HookInstanceMethod(appDel"))
        elif "@selector(application:didFinishLaunchingWithOptions:)" not in reg:
            fails.append(("hookPresent",
                          "注册段没 hook application:didFinishLaunchingWithOptions:：清除分支不可达"))
        else:
            gates = list(re.finditer(r"if\s*\(\s*gEnableShortcut\s*\)", reg))
            # ★ 两条判据必须独立，不能 elif 短路：计数已经错了，「第一个 gate 在 hook
            #   之前」依然是独立事实（反证 M3 两个性质同时被破坏，只报一条会漏报）。
            if len(gates) != 1:
                fails.append(("gateCountOne",
                              "注册段里 `if (gEnableShortcut)` 有 %d 处（应为 1：只 gate "
                              "class_addMethod；hook 若被 gate 住，关开关后清除分支根本不会挂载）"
                              % len(gates)))
            if gates and gates[0].start() < i_hook:
                fails.append(("gateAfterHook",
                              "`if (gEnableShortcut)` 出现在 HookInstanceMethod(appDel 之前："
                              "关开关后 hook 不挂载，清除分支永远走不到（装饰代码）"))

    if fails:
        for k, msg in fails:
            sys.stderr.write("  ✗ [%s] %s\n" % (k, msg))
        sys.stderr.write("  共 %d 条判据未通过\n" % len(fails))
        return 1
    print("  ✓ 关开关主动摘除自己那条（app.shortcutItems = keep，写在 return 之前，带 hadOurs 守卫）")
    print("  ✓ 他人快捷项保留（else [keep addObject:it] + 插入到 keep，不整体重写 items）")
    print("  ✓ didFinishLaunching 无条件 hook；if (gEnableShortcut) 仅 gate class_addMethod 且在 hook 之后")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("用法: check-shortcut-clear.py <Tweak.xm>\n")
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv[1]))