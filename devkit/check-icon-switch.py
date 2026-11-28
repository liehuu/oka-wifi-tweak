#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面动态图标（v1.6.6）闸门。

用法：python3 devkit/check-icon-switch.py <Tweak.xm>
退出码 0 = 全部通过；1 = 有判据不满足（stderr 逐条说明，带判据键名）。

用户红线（本次功能的验收原话）：
  「要做到功能安全稳定，不能有时生效有时不生效，或者图标没自动切换的现象
    误导我」+「不能导致 app 签名被破坏导致 app 打不开」。
  本闸门把这两条拆成可失败的源码判据：
  · 「不能时灵时不灵」= 四个同步点（启动成功 / 停服 / 冷启动 / 回前台）
    全部在位且顺序正确；helper 侧注入与 dylib 侧切换共用同一份常量头；
  · 「不能误导」= 关态回 primary（=未注入时的显示态），面板 message 里
    有只读信息告诉用户到底启没启用（v1.6.9 起只读信息走 message 不占
    action 行 —— 真机实测动作行超过末 4 行时从栈顶整行消失）；
  · 「不能打不开」= dylib 只调公开 API setAlternateIconName:，不碰
    Info.plist、不写 .app 目录（写盘全在 helper / deb 安装期，另由
    build.sh 二进制指纹与 verify-deb.py 把关）。

判据全部跑在**剥注释后**的源码上（check-shortcut-clear.py 同款理由：
注释里的同名关键字会污染定位）。分支内断言用花括号配对取 body。
函数级判据用「函数签名 → 函数体第一个 { → extract_block」定位，
不用「签名后 N 字符」窗口（窗口越界会抓到下一个函数的同形代码）。
"""
import os
import re
import sys

# 注册段边界锚点（原文里就是这两行注释）。
REG_A = "// ---- 1: 快捷菜单 ----"
REG_B = "// ---- 3: 电脑网页端增强"

HERE = os.path.dirname(os.path.abspath(__file__))


def strip_comments(s):
    """剥掉 // 与 /* */ 注释，保留换行与字符串字面量内容。

    状态机必须认识 @"..." / "..." / '...'：
      · 字符串里的 // 与 /* 不是注释；
      · 注释里的引号不是字符串起点。
    // 注释消费到换行前（换行符本身不消费），/* */ 按其中换行数补回空行
    —— 剥离后行号与原文一一对应，行序判据（先落状态再同步图标）才可靠。
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
    """按函数签定位函数体：签名 → 其后第一个 '{' → 花括号配对。

    返回 None 表示签名不存在（调用方负责报「找不到函数」）。
    不按「签名后第几个字符」猜函数体起点，避免行内注释 / 前导属性干扰。

    前向声明（签名与下一个 '{' 之间先遇到 ';'）一律跳过，从下一个签名
    匹配继续找 —— v1.6.6 上线 prototype 后的实测教训：声明行写在调用点
    之前，find 命中声明、再往后找 '{' 抓到的是别处的块，函数体判据
    （readyFn 等）会拿错文本假红。
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
        "a = 1; // if (!gEnableSwitchIcon)          return;\n"
        "b = 2; /* app.shortcutItems = keep; */\n"
        "c = \"keep this // and /* too */ string\";\n"
    )
    su = strip_comments(unit)
    if "if (!gEnableSwitchIcon)" in su:
        fails.append(("stripUnit", "剥注释不干净：// 注释内容仍留在剥后文本里"))
    if "app.shortcutItems = keep;" in su:
        fails.append(("stripUnit", "剥注释不干净：/* */ 注释内容仍留在剥后文本里"))
    if "keep this // and /* too */ string" not in su:
        fails.append(("stripUnit", "剥过头了：字符串字面量被误删"))
    # 行号保真：剥后第 3 行必须还是那个字符串（行序判据依赖它）。
    if len(su.split("\n")) != len(unit.split("\n")):
        fails.append(("stripUnit", "剥注释没保住行结构：剥后行数与原文不一致，行序判据会失效"))

    # ---------- 共源常量头（dylib 与 helper 的唯一约定） ----------
    hdr_path = os.path.join(HERE, "include", "oka-icon-common.h")
    try:
        hdr = open(hdr_path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        hdr = ""
        fails.append(("sharedHeader", "读不到 %s：%s" % (hdr_path, e)))
    for key, val in (
        ("OKA_ICON_NAME_GREEN", "OkaIconGreen"),
        ("OKA_ICON_KEY_INJECTED", "OkaIconInjected"),
        ("OKA_TARGET_BUNDLE_ID", "com.blinker.documents"),
    ):
        if not re.search(r"#define\s+%s\s+\"%s\"" % (key, re.escape(val)), hdr):
            fails.append(("sharedHeader",
                          "共源常量头缺 #define %s \"%s\"：切换方与注入方对不上名字，"
                          "症状是「注入成功但切换失效」，运行时零报错" % (key, val)))
    if '#import "oka-icon-common.h"' not in src:
        fails.append(("sharedHeader",
                      "Tweak.xm 没 #import \"oka-icon-common.h\"：dylib 拿不到共源常量，"
                      "只能硬编码字面量，helper 一改名就静默分叉"))
    # v1.6.9.2：装态门控判据也必须共源。两边各写一份 = 口径漂移，症状是
    #   「面板说一套、安装期做另一套」（面板判可恢复、安装期却跳过注入），
    #   运行时零报错 —— 旧口径就是这么漂移出事儿的（见 host_fn 判据）。
    if '#import "iconinject-core.h"' not in src:
        fails.append(("sharedHeader",
                      "Tweak.xm 没 #import \"iconinject-core.h\"：dylib 拿不到共享门控判据"
                      "oka_install_form，只能自己写一份口径"))

    # ---------- helper 侧源码在位（check 单文件化，缺一个都算本功能残） ----------
    # ★ 一律用正则 + 词边界 / 否定展望，**禁止裸子串 in**：
    #   「加后缀改名」是最常见的改名形态（iconbakX / INJECTED_OLD / _renamed），
    #   子串判断会把它当成原件放过（selftest-icon-gate.py 反向实测抓到的
    #   漏网类：'iconbak' in 'com.oka.wifitweak.iconbakX' == True）。
    for rel, pat, why in (
        ("iconinject.m", r"oka-icon-common\.h", "helper 不引共源常量头"),
        ("iconinject.m", r"com\.oka\.wifitweak\.iconbak(?![A-Za-z0-9_])",
         "helper 缺 Info.plist 备份路径（写坏 plist 时无从还原）"),
        ("iconinject.m", r"\brestore\b", "helper 缺 restore 子命令（卸载后图标声明与 PNG 永驻）"),
        ("iconinject-core.h", r"\boka_tint_rgba\b", "红→绿纯函数不在位：像素映射无法单测"),
        ("selftest-iconinject.c", r"\bexpect_tint\b", "红→绿 host 单测不在位：阈值改动无人发现"),
        # v1.6.9.2：装态门控共源四件套（判据 / 放行 / helper 调用点 / 回归输入）
        ("iconinject-core.h", r"\boka_install_form\b",
         "装态门控判据不在位：helper 与 dylib 口径无法共用，"
         "会漂移成「面板说一套、安装期做另一套」"),
        ("iconinject-core.h", r"\boka_form_allows_inject\b",
         "放行判据不在位：v1.6.9.2 口径（iTunesMetadata 缺失不否决）无人把守"),
        ("iconinject.m", r"\bInstallFormOf\b",
         "helper 没走共享装态判据：安装期门控与面板口径各写一份"),
        ("selftest-iconinject.c", r"oka_install_form\(0, 0\)",
         "host 单测没覆盖「无 iTunesMetadata + 无 prov」输入："
         "v1.6.9.2 修复的回归点没被钉住（旧口径正是误拒这个输入）"),
    ):
        p = os.path.join(HERE, rel)
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except OSError as e:
            fails.append(("helperSrc", "读不到 %s：%s" % (p, e)))
            continue
        if not re.search(pat, txt):
            fails.append(("helperSrc", "%s: %s" % (rel, why)))
    p_hlp = os.path.join(HERE, "iconinject.m")
    if os.path.exists(p_hlp):
        hlp = strip_comments(
            open(p_hlp, encoding="utf-8", errors="replace").read())
        if "NSLog" in hlp:
            fails.append(("helperNoNSLog",
                          "helper 里出现 NSLog：安装期 postinst 调它，输出经 dpkg 日志"
                          "可能乱码；本文件约定只用 printf"))
        # ---------- SDK 常量 ↔ 框架头映射（run 43 实测教训） ----------
        # kUTTypePNG 声明在 MobileCoreServices/UTCoreTypes.h，ImageIO.h 不会
        # 间接带上它：host 单测走 macOS 系统 SDK 编译看得见，helper 走
        # iPhoneOS sysroot 直接 use of undeclared identifier。本机无 macOS
        # SDK、本地编译不可见，这类错只有 CI 能抓 —— 闸门必须自己看「常量
        # 在用时，对应框架头在不在」。
        for _tok, _fw in (("kUTType", "MobileCoreServices"),):
            if _tok in hlp and ("#import <" + _fw + "/") not in hlp:
                fails.append(("helperFw",
                              "helper 用了 %s* 常量但没 import <%s/...>："
                              "该常量声明在 %s 框架头里，ImageIO.h 不间接带上；"
                              "host 单测（macOS 系统 SDK）看得见、helper（iOS "
                              "sysroot）看不见 —— v1.6.6 run 43 三个 scheme 正是"
                              "死在 kUTTypePNG use of undeclared identifier"
                              % (_tok, _fw, _fw)))

    # ---------- 切换函数本体 ----------
    setter = fn_body(src, "static void OkaSetAppIcon(BOOL on)")
    if setter is None:
        fails.append(("helperFound", "找不到 static void OkaSetAppIcon(BOOL on) —— 切换函数被改名或删除"))
    else:
        # on=绿 / off=nil 成链：nil 回 primary（=未注入时的显示态），
        # 这正是「未启用与关同图、不会把没生效骗成已生效」的落点。
        # ★ 三点都要正则，不许裸子串：
        #   · 两参调用必须成链出现（`setAlternateIconName:name` 紧跟
        #     `completionHandler:`）——只查 "setAlternateIconName:" 会同时
        #     命中 respondsToSelector 里的 selector 串，把「调用退化成单参」
        #     放过去（单参在 ObjC 里是另一个不存在的 selector，运行时才炸）。
        if not re.search(r"setAlternateIconName:name\s+completionHandler:", setter):
            fails.append(("helperSwitch",
                          "OkaSetAppIcon 没走公开 API setAlternateIconName:name + "
                          "completionHandler: 两参调用 —— 私有 API / 单参调用 / 直接改 "
                          "SpringBoard 才是「时灵时不灵」的来源"))
        if "OKA_ICON_NAME_GREEN] : nil" not in setter:
            fails.append(("helperSwitch",
                          "切换链不是 `on ? ...OKA_ICON_NAME_GREEN : nil`：off 态必须回 "
                          "primary 原图标（与未注入同图），恒绿会把关了骗成开着"))
        if not re.search(r"if\s*\(\s*!gEnableSwitchIcon\s*\)\s*return", setter):
            fails.append(("gateSwitch",
                          "OkaSetAppIcon 缺 `if (!gEnableSwitchIcon) return`：App 内关掉开关后"
                          "图标还在偷偷切，设置与行为不一致"))
        if not re.search(r"if\s*\(\s*!OkaIconSwitchReady\(\)\s*\)\s*return", setter):
            fails.append(("gateReady",
                          "OkaSetAppIcon 缺 `if (!OkaIconSwitchReady()) return`：注入没完成时"
                          "也去切 alternate icon，系统每次都拒绝（静默失败），用户看到的就是"
                          "「开关开了但图标不动」"))
        if (not re.search(r"\brespondsToSelector:", setter)
                or "@selector(setAlternateIconName:completionHandler:)" not in setter):
            fails.append(("osGuard",
                          "缺 respondsToSelector:@selector(setAlternateIconName:completionHandler:)"
                          "：iOS < 10.3 上 unrecognized selector 直接崩，「打不开」红线"))

    ready = fn_body(src, "static BOOL OkaIconSwitchReady(void)")
    if ready is None:
        fails.append(("readyFn", "找不到 static BOOL OkaIconSwitchReady(void) 定义"))
    elif not re.search(r"\bOKA_ICON_KEY_INJECTED\b", ready):
        fails.append(("readyFn",
                      "OkaIconSwitchReady 没读共源标记键 OKA_ICON_KEY_INJECTED："
                      "helper 注入成功与否 dylib 无从得知，未启用时也会尝试切换"))
    else:
        # v1.6.7：用户实测「卸载插件重装 deb 后仍显示未启用」根因之一 ——
        # [NSBundle mainBundle].infoDictionary 是 App 启动那一刻的快照，
        # helper 在安装期往 .app/Info.plist 写注入标记后，不重启 App 缓存
        # 里永远没有这个键。必须直接读 plist 文件。
        if "infoDictionary" in ready:
            fails.append(("readyFn",
                          "OkaIconSwitchReady 读 infoDictionary：那是启动快照，"
                          "helper 安装期写盘后不重启 App 读不到——用户实测"
                          "「卸载重装 deb 也没生效」正是这个缓存"))
        if not re.search(r"pathForResource:@\"Info\"\s*\n\s*ofType:@\"plist\"", ready):
            fails.append(("readyFn",
                          "OkaIconSwitchReady 没走 pathForResource:@\"Info\" ofType:@\"plist\""
                          " 读文件：拿不到安装期写入 Info.plist 的注入标记"))

    # ---------- 声明先于使用（C 规则，v1.6.6 CI 实测教训） ----------
    # v1.6.6 首发 CI 三个 scheme 全挂在同一个编译错：OkaIconSwitchReady 的
    # 面板状态行调用点(Tweak.xm:1225)早于 static 定义(:1417)，clang 一次报
    # 两个错 —— call to undeclared function + static declaration follows
    # non-static declaration。本地只跑文本闸门不跑 clang，这类错只有 CI 能
    # 抓，所以闸门必须自己看行序：任何调用点早于定义时，必须存在更早的
    # 前向声明（以分号结尾、无函数体的那行）。
    for _fname in ("OkaIconSwitchReady", "OkaSetAppIcon", "OkaSelfHealService",
                   "OkaHostIsAppStore"):
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
            continue  # 定义缺失由 helperFound / readyFn 两条判据负责，不重复报
        if _first_call is not None and _first_call < _def_line:
            if _proto_line is None or _proto_line > _first_call:
                fails.append(("declBeforeUse",
                              "%s 的调用点(行 %d)早于定义(行 %d) 且没有更早的前向声明："
                              "C 规则声明先于使用，缺了 CI clang 直接报 "
                              "call to undeclared function + "
                              "static declaration follows non-static declaration "
                              "两个错（v1.6.6 首发三个 scheme 正是死在这里）"
                              % (_fname, _first_call, _def_line)))

    # ---------- 同步点①：启动成功才亮绿 ----------
    st = fn_body(src, "static BOOL my_startWithOptions(id self, SEL _cmd, NSDictionary *options, NSError **error)")
    if st is None:
        fails.append(("startFn", "找不到 my_startWithOptions 定义"))
    else:
        okblk = None
        for m in re.finditer(r"if\s*\(\s*ok\s*\)\s*\{", st):
            body, _ = extract_block(st, m.end() - 1)
            if body and "OkaSetAppIcon(YES)" in body:
                okblk = body
        if okblk is None:
            fails.append(("startSync",
                          "my_startWithOptions 的 if (ok) 块里没有 OkaSetAppIcon(YES)："
                          "启动失败也会亮绿图标（误导），或根本没接启动路径"))
        elif "gServerRunning = YES;" in okblk and \
                okblk.find("gServerRunning = YES;") > okblk.find("OkaSetAppIcon(YES)"):
            fails.append(("startSync", "if (ok) 块里先同步图标后落状态：与 my_stop 的「先状态后图标」不一致"))

    # ---------- 同步点②：停服回原图 ----------
    stp = fn_body(src, "static void my_stop(id self, SEL _cmd)")
    if stp is None:
        fails.append(("stopFn", "找不到 my_stop 定义"))
    elif "OkaSetAppIcon(NO)" not in stp:
        fails.append(("stopSync",
                      "my_stop 里没有 OkaSetAppIcon(NO)：用户停服后桌面还挂着绿图标，"
                      "正是「图标没自动切换」的实测症状"))
    elif "gServerRunning = NO;" in stp and \
            stp.find("gServerRunning = NO;") > stp.find("OkaSetAppIcon(NO)"):
        fails.append(("stopSync",
                      "my_stop 里先同步图标后落状态：gServerRunning 还是 YES 时图标已动，"
                      "顺序反了（先落状态、再同步图标）"))

    # ---------- 同步点③：冷启动兜底（必须在所有 return 之前） ----------
    lch = fn_body(src, "static BOOL my_didFinishLaunching(id self, SEL _cmd, UIApplication *app, NSDictionary *opts)")
    if lch is None:
        fails.append(("launchFn", "找不到 my_didFinishLaunching 定义"))
    else:
        i_icon = lch.find("OkaSetAppIcon(gServerRunning)")
        m_ret = re.search(r"\breturn\b", lch)
        if i_icon < 0:
            fails.append(("launchSync",
                          "my_didFinishLaunching 没按 gServerRunning 同步图标："
                          "App 被系统杀掉后重进，桌面停留在上次的绿图标 = 误导"))
        elif m_ret and i_icon > m_ret.start():
            fails.append(("launchSync",
                          "冷启动同步排在第一个 return 之后：三个提前出口（重装自动清 / "
                          "关开关摘除 / 正常注入）都不经过它，同步是装饰代码"))

    # ---------- 同步点④：回前台纠正 ----------
    bac = fn_body(src, "static void my_applicationDidBecomeActive(id self, SEL _cmd, UIApplication *app)")
    if bac is None:
        fails.append(("becomeFn", "找不到 my_applicationDidBecomeActive 定义"))
    elif "OkaSetAppIcon(gServerRunning)" not in bac:
        fails.append(("becomeSync",
                      "回前台回调没同步图标：App 被杀后重进之外的路径（外部改图标 / "
                      "系统刷新）没人纠正，图标会长期停在错误态"))

    # ---------- 注册段：回前台同步必须无条件挂载 ----------
    a = raw.find(REG_A)
    b = raw.find(REG_B)
    if a < 0 or b < 0 or b <= a:
        fails.append(("regFound", "找不到注册段锚点 %r / %r" % (REG_A, REG_B)))
    else:
        reg = strip_comments(raw[a:b])
        i_hook = reg.find("SEL selActive")
        if i_hook < 0:
            fails.append(("becomeReg",
                          "注册段没注册 applicationDidBecomeActive:：同步点④根本挂不上"))
        else:
            if "class_addMethod(appDel, selActive" not in reg:
                fails.append(("becomeReg",
                              "回前台注册没走 class_addMethod(appDel, selActive："
                              "该方法是 UIApplicationDelegate 的**可选**方法，App 没实现时 "
                              "HookInstanceMethod 静默 return，hook 挂不上且无任何报错"))
            if "HookInstanceMethod(appDel, selActive" not in reg:
                fails.append(("becomeReg",
                              "class_addMethod 返回 NO（App 已实现）后没有 HookInstanceMethod 兜底："
                              "已实现该方法的 App 上同步逻辑整段失效"))
            if "(void **)&orig_applicationDidBecomeActive" not in reg:
                fails.append(("becomeReg", "缺 &orig_applicationDidBecomeActive：原实现没被接力，App 原有回前台逻辑丢失"))
            if "(IMP)my_applicationDidBecomeActive" not in reg:
                fails.append(("becomeReg", "注册的不是 my_applicationDidBecomeActive"))
            if '"v@:@"' not in reg:
                fails.append(("becomeReg", "class_addMethod 类型串不是 \"v@:@\"：方法签名错，调用即崩"))
            # 无条件：selActive 注册之前不许出现任何功能开关 gate。
            seg = reg[:i_hook]
            if re.search(r"if\s*\(\s*gEnable\w+\s*\)", seg):
                fails.append(("becomeNoGate",
                              "SEL selActive 注册之前出现了 if (gEnable...)：开关关掉后同步点④"
                              "不挂载，而它的症状正是用户报的「图标没自动切换」"))

    # ---------- 面板（「不能误导」的 App 内可见性 + v1.6.9 渲染层修复） ----------
    pnl = fn_body(src, "static void OkaPresentWebAuthSettings(")
    if pnl is None:
        fails.append(("panelFn", "找不到 OkaPresentWebAuthSettings 定义"))
    else:
        # v1.6.9 渲染层修复源码不变量 —— 三条全部来自真机实测，不是审美：
        #   ① actionCap：动作行 ≤ 4 行。实测（v1.6.7/v1.6.8 两张反馈截图，
        #      密码框获焦、键盘弹起）：动作行超过末 4 行时，超出行从栈顶开始
        #      整行消失 —— v1.6.7 丢第 1 行「长按快捷菜单」、v1.6.8 再丢
        #      「桌面动态图标」，两代可见行恒为最末 4 行。
        #   ② msgInfo：只读信息（图标三态 + 构建版本）必须走 alert message ——
        #      message 是文本、可控高、不参与动作行淘汰，文本行实测从不丢。
        #   ③ keyboardDismiss：present 前必须收键盘（高度溢出直接诱因），
        #      并延迟到键盘退场动画做完再弹，否则 alert 在键盘仍占位的
        #      窗口期里定型。
        #   v1.6.7 行序判据不变：菜单行第一个 action、取消垫尾。
        i_menu = pnl.find('@"长按快捷菜单：')
        i_close = pnl.find('@"关闭密码"')
        i_save = pnl.find('@"保存"')
        i_cancel = pnl.find('@"取消"')
        if i_menu < 0:
            fails.append(("panelRow",
                          "面板找不到「长按快捷菜单」行标题：用户没有 App 内自清开关入口"))
        else:
            if "menuOn" not in pnl:
                fails.append(("panelRow",
                              "菜单行标题不带实时状态（menuOn）：用户要点进去才知道"
                              "开关是开是关，面板第一眼信息量为零"))
            if "OkaToggleShortcutMenu();" not in pnl:
                fails.append(("panelRow",
                              "菜单行 handler 没调 OkaToggleShortcutMenu()：开关入口是"
                              "装饰，点下去不切换（check-shortcut-clear.py 只查全局有"
                              "没有这行调用，查不到挂在别的 handler 上的偷梁换柱）"))
            if "OkaIconSwitchReady()" not in pnl:
                fails.append(("iconEnv",
                              "面板没读 OkaIconSwitchReady()：显示的启用状态与实际注入"
                              "状态无关，是纯粹的误导"))
            for _nm, _i in (("保存", i_save), ("取消", i_cancel)):
                if _i < 0:
                    fails.append(("panelOrder",
                                  "面板找不到「%s」行：四行交互结构"
                                  "（菜单/关闭密码/保存/取消）残缺" % _nm))
            for _nm, _i in (("保存", i_save), ("取消", i_cancel), ("关闭密码", i_close)):
                if _i >= 0 and _i < i_menu:
                    fails.append(("panelOrder",
                                  "「长按快捷菜单」行排在「%s」之后：开关行必须是面板第一个 "
                                  "action、第一眼可见——用户实测埋在后面会被看漏，"
                                  "误以为开关被删" % _nm))
            for _nm, _i in (("保存", i_save), ("关闭密码", i_close)):
                if i_cancel >= 0 and _i >= 0 and i_cancel < _i:
                    fails.append(("panelOrder",
                                  "「取消」排在「%s」之前：取消必须是最后一个 action，"
                                  "否则用户第一眼先看到取消，顺手划掉，看不到下面的开关" % _nm))

        # ① 动作栈容量：addAction 计数 ≤ 4（菜单/关闭密码/保存/取消）。
        #   超量的直接后果就是 v1.6.7/v1.6.8 实测的「开关行整行消失」。
        n_act = pnl.count("addAction:")
        if n_act > 4:
            fails.append(("actionCap",
                          "面板动作行 %d 个 > 4：真机实测动作行超过末 4 行时，超出行"
                          "从栈顶开始整行消失（v1.6.7 丢「长按快捷菜单」、v1.6.8 再丢"
                          "「桌面动态图标」，可见行恒为最末 4 行）。只读信息一律走 "
                          "message" % n_act))

        # ② 只读信息在 message，不在动作栈。
        if "message:infoMsg" not in pnl:
            fails.append(("msgInfo",
                          "alert 的 message 没传 infoMsg：只读信息（图标三态 + 构建"
                          "版本）没进消息区 = v1.6.9 修复被架空"))
        if "插件版本：%@" not in pnl:
            fails.append(("msgInfo",
                          "message 里没有「插件版本：%@」：用户报「开关不见了」时"
                          "失去第一现场证据（无法当场核对设备实跑构建）"))
        if "桌面动态图标：%@" not in pnl:
            fails.append(("msgInfo",
                          "message 里没有「桌面动态图标：%@」：桌面没切换时用户在 "
                          "App 内看不到「未启用」，只能猜自己哪里点错了"))
        if "OKA_BUILD_TAG" not in pnl:
            fails.append(("versionRow",
                          "版本信息没引用 OKA_BUILD_TAG：写死字符串的话改版本号"
                          "只会改一半，面板显示的和真实构建对不上"))
        if "iconState" not in pnl:
            fails.append(("msgInfo",
                          "message 没引 iconState：图标三态没真正进消息区"))
        for _bad in ("addAction:iconRow", "addAction:verRow"):
            if _bad in pnl:
                fails.append(("msgInfo",
                              "出现 %s：只读行又搬回动作栈 —— v1.6.8 实测只读行动作行"
                              "超量时从栈顶整行消失，版本/图标行必须留在 message" % _bad))

        # ③ present 前收键盘 + 延迟到键盘退场后再弹。
        i_end = pnl.find("[vc.view endEditing:YES]")
        i_pres = pnl.find("presentViewController:")
        if i_end < 0 or i_pres < 0:
            fails.append(("keyboardDismiss",
                          "面板 present 前没有 [vc.view endEditing:YES]：密码框获焦时"
                          "键盘占掉约 290pt，alert 高度预算被压爆正是动作行成片消失的"
                          "直接诱因（v1.6.7/v1.6.8 实测）"))
        elif i_end > i_pres:
            fails.append(("keyboardDismiss",
                          "endEditing 排在 present 之后：收了键盘也白收，present 时键盘"
                          "还占着高度"))
        else:
            _d = re.search(r"\(int64_t\)\(([0-9.]+) \* NSEC_PER_SEC\)", pnl)
            if _d is None:
                fails.append(("keyboardDismiss",
                              "present 没走 dispatch_after 延时：键盘退场动画约 0.25s，"
                              "退场完成前弹面板，alert 会在键盘仍占位的窗口期里定型"))
            elif float(_d.group(1)) < 0.3:
                fails.append(("keyboardDismiss",
                              "present 延时 %ss < 0.3s：等不到键盘退场（约 0.25s）做完"
                              % _d.group(1)))
        # v1.6.9.1：present 的 completion 必须收掉 alert 自己 text field 的
        #   焦点。装机实测：0.35s 延时只等掉了宿主键盘，UIAlertController
        #   present 完成后自动让其密码 field 成为 first responder，密码键盘
        #   重起、高度预算再被压爆，底部「关闭密码/保存/取消」只露一半。
        i_comp = pnl.find("completion:")
        i_res = pnl.find("resignFirstResponder")
        if i_res < 0 or i_comp < 0 or i_res < i_comp:
            fails.append(("keyboardDismiss",
                          "present 的 completion 里没有 resignFirstResponder（或排在 "
                          "completion 之前）：UIAlertController 呈现后会自动让 text field "
                          "成为 first responder，键盘重起又把面板高度预算压爆 —— "
                          "v1.6.9.1 装机实测底部「关闭密码/保存/取消」被挤得只露一半"))

        # v1.6.8：图标状态行三态文案。真机实测（卸载重装 deb 仍显示未启用）：
        #   宿主是自签/侧载装态时 helper 按安全门控跳过，「重装本插件可恢复」
        #   对这类用户是假承诺。判据：panel 必须按宿主装态分支，且
        #   「重装本插件 deb 可恢复」只能出现在 Apple 签名形态分支里（全文件
        #   仅一处）。
        #   v1.6.9.2：第三态口径收紧为「只有 embedded.mobileprovision」（第三方
        #   签名），见下面 host_fn 判据与文案判据。
        host_fn = fn_body(src, "static BOOL OkaHostIsAppStore(void)")
        if host_fn is None:
            fails.append(("iconEnv",
                          "找不到 OkaHostIsAppStore 定义：面板无法区分宿主装态，"
                          "只能对所有未启用用户喊「重装可恢复」——自签环境用户"
                          "实测重装无效，这句是误导"))
        else:
            # v1.6.9.2 口径：只有 embedded.mobileprovision 才是硬否决项，
            #   iTunesMetadata 缺失不再否决。三条判据，每条都能失败：
            #   ① 必须查 prov 且走共享判据（否则面板与安装期口径漂移）；
            #   ② meta/prov 两个事实必须**原样**喂进 oka_install_form
            #      （少喂一个 = 比如 prov 恒传 0，第三方签名宿主也判可恢复，
            #       红线失效）；
            #   ③ 不许把 metadata 缺失当否决项（旧口径在此误拒过 App Store
            #      安装 —— 用户 2026-11-28 实测「App Store 装的却被判非原版」）。
            if "mobileprovision" not in host_fn or "oka_install_form" not in host_fn:
                fails.append(("iconEnv",
                              "OkaHostIsAppStore 没查 embedded.mobileprovision 或没走共享判据 "
                              "oka_install_form：面板判据与安装期 helper 口径漂移，"
                              "会出现「面板说可恢复、安装期却跳过」的矛盾文案"))
            _m = re.search(r"oka_install_form\(([^)]*)\)", host_fn)
            if not _m or _m.group(1).replace(" ", "") != "meta!=nil,prov!=nil":
                fails.append(("iconEnv",
                              "OkaHostIsAppStore 没把 meta/prov 两个事实原样喂给 oka_install_form"
                              "（期望 oka_install_form(meta != nil, prov != nil)）：少喂一个事实"
                              "（如 prov 恒传 0）会让第三方签名宿主也判『可恢复』，红线失效"))
            if re.search(r"if\s*\(\s*!meta\b", host_fn) or "meta == nil" in host_fn:
                fails.append(("iconEnv",
                              "OkaHostIsAppStore 把「iTunesMetadata 缺失」当否决项：v1.6.9.2 "
                              "真机实测 App Store 安装也可能没有该文件，旧口径据此把注入"
                              "白白跳过、用户看到「非原版装态」文案（用户 2026-11-28 反馈）"))
        if "OkaHostIsAppStore()" not in pnl:
            fails.append(("iconEnv",
                          "面板没调 OkaHostIsAppStore()：未启用文案不分宿主装态，"
                          "「重装本插件可恢复」会被说给重装也没用的用户"))
        # v1.6.9.2：走到第三态的只剩「带 embedded.mobileprovision」一种情况，
        #   文案从「非 App Store 原版装态」改为「带第三方签名」——旧文案是旧
        #   口径的产物，会把 metadata 缺失的真 App Store 用户骗到这里。
        if "宿主带第三方签名" not in pnl:
            fails.append(("iconEnv",
                          "面板没有「宿主带第三方签名」文案：第三方签名用户"
                          "看不到真实原因（门控跳过）"))
        if "inject --force" not in pnl:
            fails.append(("iconEnv",
                          "面板没给 inject --force 放行路径：门控跳过的用户"
                          "没有任何自救指引，只能回来找作者"))
        if "重装本插件 deb 可恢复" not in pnl:
            fails.append(("iconEnv",
                          "面板没有 Apple 签名分支的恢复文案：App 更新后丢失时"
                          "用户不知道该重装 deb"))
        if pnl.count("重装本插件 deb 可恢复") > 1 or \
                src.count("重装本插件 deb 可恢复") > 1:
            fails.append(("iconEnv",
                          "「重装本插件 deb 可恢复」出现多于一处：它只能属于 "
                          "App Store 分支，回退成无条件文案就是 v1.6.7 的误导形态"))

    # ---------- 按钮改名与换位 ----------
    if '"⚙️ 设置"' not in src:
        fails.append(("btnTitle", "按钮标题不是 \"⚙️ 设置\"：面板功能已不止密码，标题必须随功能改名"))
    for old in ('@"🔒 密码"', 'initWithTitle:@"🔒"'):
        if old in src:
            fails.append(("btnTitle", "仍存在旧标题字面量 %s：改名不彻底" % old))
    if "btn.topAnchor" not in src:
        fails.append(("btnPos", "浮动按钮没用 btn.topAnchor：还压在 WebDAV 页签上"))
    else:
        # v1.6.7：用户实测 constant:8.0 时浮动按钮正好压在 WiFi 页顶部提示
        # 标签的文字上，标题和按钮糊成一团；挪到 40（约两行文字高）才错开。
        # v1.6.8：真机复测 40 仍稍微压住提示文字（提示区比两行略高），
        # 再降到 56。只判「用没用 topAnchor」挡不住把常量改回去——那种回归
        # 必须让闸门自己炸，所以这里把偏移量下限钉死。
        _cm = re.search(
            r"\[btn\.topAnchor constraintEqualToAnchor:"
            r"view\.safeAreaLayoutGuide\.topAnchor\s*\n\s*constant:([0-9.]+)\]",
            src)
        if _cm is None:
            fails.append(("btnPos",
                          "btn.topAnchor 约束行找不到 constant: 数值：约束被改写，"
                          "偏移量无从验证，等于放任按钮压回提示标签（用户实测缺陷）"))
        elif float(_cm.group(1)) < 56.0:
            fails.append(("btnPos",
                          "浮动按钮 topAnchor 常量 %s < 56.0：用户实测 40.0 时按钮"
                          "仍稍微叠加在页面顶部提示标签文字上，偏移必须足够大"
                          % _cm.group(1)))
    if "btn.bottomAnchor" in src:
        fails.append(("btnPos",
                      "浮动按钮仍用 btn.bottomAnchor：底部约束会遮挡 WebDAV 传输页签"
                      "（用户实测反馈）"))

    # ---------- 偏好读取 ----------
    prefs = fn_body(src, "static void LoadPrefs(void)")
    if prefs is None:
        fails.append(("prefsFn", "找不到 LoadPrefs 定义"))
    elif 'd[@"IconSwitch"]' not in prefs:
        fails.append(("prefsRead",
                      "LoadPrefs 没读 IconSwitch 偏好：plist 里写 false 也关不掉图标切换，"
                      "开关只存在于 NSUserDefaults 一条路"))

    # ---------- 负向：用户明确否决过的方案不许回流 ----------
    if "setApplicationIconBadgeNumber" in src:
        fails.append(("noBadgeDylib",
                      "出现 setApplicationIconBadgeNumber：角标方案已被用户明确否决"
                      "（「看着小红点我就想给点掉」），本功能只走 alternate icon"))

    if fails:
        for k, msg in fails:
            sys.stderr.write("  ✗ [%s] %s\n" % (k, msg))
        sys.stderr.write("  共 %d 条判据未通过\n" % len(fails))
        return 1
    print("  ✓ 共源常量头三键齐全，dylib / helper 同源；helper 与 host 单测在位，helper 只用 printf")
    print("  ✓ 切换只走公开 API setAlternateIconName:（on=绿 / off=primary），门控齐全（开关/注入标记/系统版本）")
    print("  ✓ 四同步点：启动成功亮绿（先落状态）、停服回原图、冷启动兜底（首个 return 之前）、回前台无条件纠正")
    print("  ✓ 回前台注册：class_addMethod 补实现 + 已实现时 HookInstanceMethod 接力，注册前无任何开关 gate")
    print("  ✓ 声明先于使用：OkaIconSwitchReady / OkaSetAppIcon / OkaSelfHealService / "
          "OkaHostIsAppStore 调用点早于定义时必有更早前向声明"
          "（v1.6.6/v1.6.7 CI 编译红的同型教训）")
    print("  ✓ 面板 v1.6.9 渲染层修复：动作栈 ≤ 4 行交互行（菜单/关闭密码/保存/取消），"
          "只读信息（图标三态 + 构建版本）走 message 不进行动栈；present 前 "
          "endEditing 收键盘 + 延时 ≥ 0.3s 等键盘退场、completion 里再收一次 "
          "alert 自动获焦（v1.6.9/v1.6.9.1 两轮装机实测）")
    print("  ✓ 面板只读状态：图标三态按宿主装态分支（已启用 / Apple 签名形态丢失可重装恢复 / "
          "第三方签名门控跳过讲清放行路径，重装承诺只属于 Apple 签名分支）；"
          "「长按快捷菜单」开关行是第一个 action、取消垫尾；构建版本引 OKA_BUILD_TAG 且在 message 内；"
          "按钮改名 ⚙️ 设置，topAnchor 偏移 ≥ 56 避开顶部提示标签；IconSwitch 偏好可读")
    print("  ✓ v1.6.9.2 安装形态口径：dylib / helper 共用 iconinject-core.h 判据，"
          "只有 embedded.mobileprovision 才否决；iTunesMetadata 缺失不否决"
          "（回归点有 host 单测断言 + 反向样本 M37/M38）")
    print("  ✓ 无 setApplicationIconBadgeNumber 回流（角标方案已被否决）")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("用法: check-icon-switch.py <Tweak.xm>\n")
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv[1]))