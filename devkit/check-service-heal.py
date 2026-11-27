#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务假死自愈（v1.6.7）闸门。

用法：python3 devkit/check-service-heal.py <Tweak.xm>
退出码 0 = 全部通过；1 = 有判据不满足（stderr 逐条说明，带判据键名）。

用户实测复现路径（本闸门的存在理由）：
  「开着 WiFi 传输过一会儿网页打不开，App 没被杀，连接状态从『已连接』
    变『等待中』；关掉再打开开关，电脑立刻能访问，状态回『已连接』。」
  = WebDAV 服务端假死（监听 socket 被系统回收 / Wi-Fi 漫游后僵住），
    而宿主 UI 的 ON/OFF 与实例 isRunning 都是 YES，都不体现这个死态。

设计要点（为什么必须是这些判据）：
  · 探活**不能**用 isRunning —— 假死时它恒 YES，探了等于没探；
    必须本地回环 connect 实测（只碰 127.0.0.1，不外联）。
  · 自愈**必须**重放用户手动「关开」那条路（objc_msgSend
    startWithOptions:error: 自然经过 my hook，suspend=NO / 密码头 /
    端口 fallback 全部自动补齐），不许另起私路只改状态位。
  · 防抖 90s + 45s 定时器 + 回前台触发，三个触发面缺一不可；
    timer 忘了 dispatch_resume 是永不触发的传统死法。
  · 实例/最近 options 必须在 my_startWithOptions / my_serverURL
    里落袋，否则自愈重放拿不到同样配置（密码头丢失 = 网页端 401）。

判据同样全部跑在**剥注释后**的源码上（同 check-icon-switch.py）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def strip_comments(s):
    """剥掉 // 与 /* */ 注释，保留换行与字符串字面量内容。

    状态机必须认识 @"..." / "..." / '...'：
      · 字符串里的 // 与 /* 不是注释；
      · 注释里的引号不是字符串起点。
    // 注释消费到换行前，/* */ 按其中换行数补回空行 —— 剥后行号与
    原文一一对应，函数体判据拿到的文本与行号判据互不打架。
    """
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i)
            if j < 0:
                break
            i = j
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


def fn_body(src, signature):
    """按函数签定位函数体：签名 → 第一个 '{' → 花括号配对。

    前向声明（签名与下一个 '{' 之间先遇到 ';'）一律跳过，从下一个匹配
    继续找 —— v1.6.6 prototype 上线后的实测教训：命中声明行再往后找
    '{' 抓到的是别处的块，函数体判据拿错文本假红。
    """
    start = 0
    while True:
        p = src.find(signature, start)
        if p < 0:
            return None
        t = p + len(signature)
        m = re.search(r"[;{]", src[t:t + 512])
        if m is None:
            return None
        if m.group() == "{":
            body, _ = extract_block(src, t + m.start())
            return body
        start = p + 1  # 前向声明：跳过这个匹配，继续找定义


def main(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    src = strip_comments(raw)
    fails = []

    # ---------- stripUnit：剥注释自证 ----------
    unit = (
        "a = 1; // if (!gEnableServiceHeal)          return;\n"
        "b = 2; /* CFSocketConnectToAddress junk; */\n"
        "c = \"keep this // and /* too */ string\";\n"
    )
    su = strip_comments(unit)
    if "if (!gEnableServiceHeal)" in su:
        fails.append(("stripUnit", "剥注释不干净：// 注释内容仍留在剥后文本里"))
    if "CFSocketConnectToAddress junk" in su:
        fails.append(("stripUnit", "剥注释不干净：/* */ 注释内容仍留在剥后文本里"))
    if "keep this // and /* too */ string" not in su:
        fails.append(("stripUnit", "剥过头了：字符串字面量被误删"))
    if len(su.split("\n")) != len(unit.split("\n")):
        fails.append(("stripUnit", "剥注释没保住行结构：剥后行数与原文不一致，行序判据会失效"))

    # ---------- 函数定位 ----------
    alive = fn_body(src, "static BOOL OkaPortAlive(NSUInteger port)")
    heal = fn_body(src, "static void OkaSelfHealService(void)")
    timer = fn_body(src, "static void OkaStartHealTimer(void)")
    prefs = fn_body(src, "static void LoadPrefs(void)")
    become = fn_body(src, "static void my_applicationDidBecomeActive(id self, SEL _cmd, UIApplication *app)")
    launch = fn_body(src, "static BOOL my_didFinishLaunching(id self, SEL _cmd, UIApplication *app, NSDictionary *opts)")
    start = fn_body(src, "static BOOL my_startWithOptions(id self, SEL _cmd, NSDictionary *options, NSError **error)")

    if alive is None:
        fails.append(("healAliveFn", "找不到 OkaPortAlive 定义：没有探活函数，自愈整段不存在"))
    if heal is None:
        fails.append(("healFn", "找不到 OkaSelfHealService 定义：假死不可能自愈，用户只能手动关开"))
    if timer is None:
        fails.append(("healFn", "找不到 OkaStartHealTimer 定义：45s 周期探活兜底不存在"))
    if start is None:
        fails.append(("healFn", "找不到 my_startWithOptions 定义：实例/options 落袋判据无从验"))

    # ---------- 探活：本地回环实测，不得用 isRunning ----------
    if alive is not None:
        if "CFSocketConnectToAddress" not in alive:
            fails.append(("healAlive",
                          "OkaPortAlive 没有 CFSocketConnectToAddress：没有本地回环实测，"
                          "假死的端口探出来也是『活着』"))
        if "INADDR_LOOPBACK" not in alive:
            fails.append(("healAlive",
                          "OkaPortAlive 没连 INADDR_LOOPBACK：探活地址不是本机回环，"
                          "可能外联或探错目标"))
        if "isRunning" in alive:
            fails.append(("healNoIsRunning",
                          "探活读了 isRunning：假死时实例 isRunning 恒 YES（用户实测"
                          "App 没杀、状态显示已连接正是它），拿它当判据等于没探"))
        # CFSocketCreate 签名七个参数（allocator, family, type, proto,
        # callBackTypes, **callout**, context）—— callout 位必须显式 NULL。
        # v1.6.7 首版就漏了这个参数：本地没有 iOS SDK 编不了，CI clang
        # 一次报 too few arguments（expected 7, have 6）。参数个数这种
        # 编译期事实只能靠闸门在文本侧钉死。
        if not re.search(r"kCFSocketNoCallBack\s*,\s*NULL\s*,", alive):
            fails.append(("healSocketCtor",
                          "CFSocketCreate 少了 callout 参数：签名是 (allocator, "
                          "protocolFamily, socketType, protocol, callBackTypes, "
                          "callout, context) 七个参数，无回调场景 callout 位必须"
                          "显式写 NULL（v1.6.7 首版正是漏它，CI clang 报 "
                          "too few arguments, expected 7, have 6）"))

    # ---------- 自愈：重放原路 + 防抖 ----------
    if heal is not None:
        if "objc_msgSend" not in heal or "startWithOptions:error:" not in heal:
            fails.append(("healReplay",
                          "OkaSelfHealService 没走 objc_msgSend startWithOptions:error:："
                          "自愈必须重放用户手动『关开』那条路（自然经过 my hook，"
                          "suspend=NO / 密码头 / 端口 fallback 自动补齐）；"
                          "另起私路只改状态位 = 桌面图标与实际服务脱节"))
        if "isRunning" in heal:
            fails.append(("healNoIsRunning",
                          "自愈读了 isRunning：假死时它恒 YES，以它为依据的判断全部失真"))
        if "gLastHealAt" not in heal:
            fails.append(("healDebounce",
                          "OkaSelfHealService 没有 gLastHealAt：无限重拉耗电刷日志，"
                          "还掩盖真问题（连续假死通常是路由器踢客户端/系统限制）"))
        elif not re.search(r"<\s*90", heal):
            fails.append(("healDebounce",
                          "防抖窗口不是 90s：连续假死时重拉间隔失控"))

    # ---------- 触发面一：回前台 ----------
    if become is not None and "OkaSelfHealService" not in become:
        fails.append(("healTrigger",
                      "my_applicationDidBecomeActive 没调 OkaSelfHealService："
                      "用户回来看网页的第一时间不修，还会再吃一次打不开"))

    # ---------- 触发面二：启动挂定时器 ----------
    if launch is not None and "OkaStartHealTimer" not in launch:
        fails.append(("healTrigger",
                      "my_didFinishLaunching 没调 OkaStartHealTimer："
                      "「没人切前后台、一直挂着」的场景没有兜底探活"))
    if timer is not None:
        for tok in ("dispatch_source_create", "DISPATCH_SOURCE_TYPE_TIMER",
                    "dispatch_source_set_timer", "dispatch_resume"):
            if tok not in timer:
                fails.append(("healTimer",
                              "OkaStartHealTimer 缺 %s：45s 定时器不完整" % tok))

    # ---------- 落袋：实例 + 最近 options ----------
    if start is not None:
        if "gServerInstance = self;" not in start:
            fails.append(("healInstance",
                          "my_startWithOptions 没落 gServerInstance = self：自愈拿不到"
                          "服务实例，重放无处可发"))
        okblk = None
        for m in re.finditer(r"if\s*\(\s*ok\s*\)\s*\{", start):
            body, _ = extract_block(start, m.end() - 1)
            if body and "gServerOptions = use;" in body:
                okblk = body
        if okblk is None:
            fails.append(("healInstance",
                          "my_startWithOptions 的 if (ok) 块没落 gServerOptions = use："
                          "自愈重放拿不到最近一次成功启动的 options，密码头/端口 fallback "
                          "全丢，重放出来的服务网页端直接 401"))

    # ---------- 偏好开关 ----------
    if prefs is not None and 'd[@"ServiceHeal"]' not in prefs:
        fails.append(("healSwitch",
                      "LoadPrefs 没读 ServiceHeal 偏好：自愈开关只存在于代码默认值，"
                      "plist 里写 false 关不掉（失控时用户没有止损手段）"))

    # ---------- 声明先于使用 ----------
    # OkaSelfHealService 调用点（my_applicationDidBecomeActive，文件中前部）
    # 早于定义（自愈段，文件后部）—— 同 v1.6.6 的 CI 教训第三次：缺前向
    # 声明时 clang 先报 call to undeclared function，再报 static
    # declaration follows non-static declaration。
    for _fname in ("OkaSelfHealService", "OkaPortAlive", "OkaStartHealTimer"):
        _def_re = re.compile(r"^\s*static\s+\w+\s+%s\s*\([^;]*\)\s*\{" % _fname)
        _proto_re = re.compile(r"^\s*static\s+\w+\s+%s\s*\([^;]*\)\s*;" % _fname)
        _call_re = re.compile(r"\b%s\s*\(" % _fname)
        _def_line = _proto_line = _first_call = None
        for _i, _ln in enumerate(src.split("\n"), 1):
            if _def_re.match(_ln):
                _def_line = _i
            elif _proto_re.match(_ln):
                _proto_line = _i
            elif _call_re.search(_ln) and _first_call is None:
                _first_call = _i
        if _def_line is None:
            continue  # 定义缺失已由 healFn 报，不重复
        if _first_call is not None and _first_call < _def_line:
            if _proto_line is None or _proto_line > _first_call:
                fails.append(("declBeforeUse",
                              "%s 的调用点(行 %d)早于定义(行 %d) 且没有更早的前向声明："
                              "C 规则声明先于使用，缺了 CI clang 直接报 "
                              "call to undeclared function + "
                              "static declaration follows non-static declaration"
                              % (_fname, _first_call, _def_line)))

    # ---------- 纪律：运行期路径不吐日志 ----------
    for _nm, _body in (("OkaPortAlive", alive), ("OkaSelfHealService", heal),
                       ("OkaStartHealTimer", timer)):
        if _body and "NSLog" in _body:
            fails.append(("healNoLog",
                          "%s 出现 NSLog：自愈是运行期路径，只做事不说话" % _nm))

    if fails:
        for k, msg in fails:
            sys.stderr.write("  ✗ [%s] %s\n" % (k, msg))
        sys.stderr.write("  共 %d 条判据未通过\n" % len(fails))
        return 1
    print("  ✓ 探活走本地回环 CFSocketConnectToAddress（127.0.0.1，不碰 isRunning）；"
          "CFSocketCreate 七参齐全（callout=NULL）")
    print("  ✓ 自愈重放 startWithOptions:error: 原路（suspend=NO/密码头/端口 fallback 自动补齐）")
    print("  ✓ 防抖 90s；实例与最近 options 在 my_startWithOptions 落袋")
    print("  ✓ 三个触发面：回前台 / 45s 定时器（含 resume）/ 启动即挂")
    print("  ✓ ServiceHeal 偏好可读；自愈段无 NSLog；声明先于使用")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("用法: %s <Tweak.xm>\n" % sys.argv[0])
        sys.exit(2)
    sys.exit(main(sys.argv[1]))