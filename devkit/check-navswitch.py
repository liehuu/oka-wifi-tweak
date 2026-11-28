#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.6.11：文件列表页导航栏 WiFi 传输开关 —— 闸门。

用户需求（配截图）：
  「进 App 后状态栏（左侧三条横线右边那块空白）加一个 WiFi 传输开关，
    实时反映当前传输状态（未开启 / 已连接 / 已停止等），点一下能开能关。」

这个控件的失效形态全是**静默**的，所以必须有闸门：
  ① 挂在错误的控制器上 → 界面上什么都不出现，无报错（用户只会说「没加」）；
  ② 不判 navigationController 就塞 item → 页面不在导航栈时 item 是孤儿，
     同样什么都不出现；
  ③ 绕过 outlet 守卫直接调 App 的 changeWifiTransferStateAction: →
     页面未上屏时 outlet 为 nil，App 里那七个 brk #1 直接硬崩
     （反汇编 0x100013e24..e40，见 Tweak.xm 的说明）；
  ④ 四个状态文案长度不一 → 胶囊宽度随状态跳动，导航栏左右抖；
  ⑤ 只挂 viewDidLoad 不挂 viewWillAppear: → 用户进出一次选择模式
     （App 会整套重设 leftBarButtonItems）开关就永久消失。

★ 判据纪律（本文件最重要的一节）
  每条判据都必须能失败，且失败要指到「哪个函数、哪一行」。因此：
  · 全部落在**剥注释后**的源码上（注释里必然提到这些标识符，不剥是假 FAIL）；
  · 「某标识符存在」这类判据一律不写 —— 变量在 ≠ 它被检查过，
    函数在 ≠ 它被注册了。必须定位到函数体、检查调用形态或顺序；
  · 顺序性判据（先守卫后调用、先判重后追加）必须比位置，不能比存在。

用法：python3 devkit/check-navswitch.py <Tweak.xm>
退出码 0 = 通过；1 = 有判据不满足（逐条打印判据键名）。
"""
import re
import sys

# 判据键名（selftest 会按集合精确比对，不许名字漂移）
K_MOUNT_CLASS   = "mountClass"       # 必须真的用文件列表页那个类去查类
K_MANGLED       = "mangledName"      # 类名必须是 mangled 形式
K_NAVGUARD      = "stackTopGuard"    # 挂 item 前必须判「本页是不是栈顶」
K_FALLBACK      = "mountFallback"    # 判不成栈顶时必须有非 item 的挂法
K_OUTLET_GUARD  = "outletGuard"      # 调 App 入口前必须过 outlet 守卫
K_IDEMPOTENT    = "idempotent"       # 必须幂等（识别到已有项就早退）
K_LEN_EQUAL     = "labelLenEqual"    # 四个状态文案等长（宽度不跳）
K_BOTH_HOOKS    = "bothHooks"        # viewDidLoad + viewWillAppear: 都注册
K_IVAR_CONN     = "activeConnIvar"   # 用 _activeConnections 真实计数
K_NO_FAKE_ZERO  = "noFakeZero"       # ivar 缺失返回 -1 而不是 0
K_TICKER_STOP   = "tickerGuarded"    # 定时器在控件消失时早退
K_POINT_ONLY    = "pointOnly"        # 点击热区是整个胶囊，不是只有圆点
K_DIAG          = "diagChannel"      # 必须有可查询的挂载自证接口
K_LEAVE_CLEAN   = "leaveCleanup"     # 非 item 挂法必须在页面离开时摘掉

STATES = ["未开启", "待连接", "已连接", "已停止"]

# 允许直接调 App 开关入口的函数。
#   OkaXferCallAppToggle —— v1.6.11 新增的守卫封装，本来就必须在这里调；
#                          它自身的守卫由 _fn_body() + 顺序判据另行校验，
#                          不是「白名单放行」。
#   StartWiFiTransfer    —— v1.0 起就有的长按菜单入口。它靠 MakeHeadlessPage
#                          的 outlet 校验 + gRetained 保活兜底，属预存行为，
#                          不在本闸门的管辖范围（改它等于改既有功能）。
# 判据不是「数一轮数量」：数量判据对既有代码永远误报，对新增代码也说不清
# 到底哪一处没过守卫。按「调用点所在函数」判，才能指到行、说清理由。
TOGGLE_CALL_ALLOWED = {"OkaXferCallAppToggle", "StartWiFiTransfer"}
RE_TOGGLE_CALL = re.compile(r"objc_msgSend\)\s*\(\s*page\s*,\s*toggle\s*,")


def strip_comments(src: str) -> str:
    """剥掉 // 与 /* */ 注释；字符串/字符字面量先占位保护。

    与 check-no-icon-gate.py 同一实现（本工程判据纪律：注释里必然提到
    被检标识符，不剥就是假 FAIL）。
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


def _fn_body(code: str, name: str):
    """取函数体正文（从 `name(...)` 后的第一个 { 到配对 }），找不到返回 None。

    只按名字取第一个定义处 —— 本文件所有被检函数在源码里都只有一处定义，
    且定义早于调用；若将来出现重载需重新审视这个假设。
    """
    m = re.search(r"\b%s\s*\(" % re.escape(name), code)
    if not m:
        return None
    i = code.find("{", m.end())
    if i < 0:
        return None
    depth, j = 0, i
    while j < len(code):
        if code[j] == "{":
            depth += 1
        elif code[j] == "}":
            depth -= 1
            if depth == 0:
                return code[i:j + 1]
        j += 1
    return None


def _enclosing_fn(code: str, pos: int):
    """pos 处所在的最内层函数名（括号配平验证，不靠「最近一次匹配」猜）。"""
    best = None
    for m in re.finditer(r"(?m)^[\w \*]*\b([A-Za-z_]\w*)\s*\([^;{]*\)[^;{]*\{", code[:pos]):
        body_start = m.end() - 1
        depth, j = 0, body_start
        while j < len(code):
            if code[j] == "{":
                depth += 1
            elif code[j] == "}":
                depth -= 1
                if depth == 0:
                    if body_start < pos < j:
                        best = m.group(1)
                    break
            j += 1
    return best


def check(src: str):
    """返回 (失败键集合, 明细行列表)。"""
    code = strip_comments(src)
    fails = set()
    notes = []

    def bad(key, msg):
        fails.add(key)
        notes.append(msg)

    # ---- ① 必须真的用文件列表页那个宏去查类 ---------------------------
    # 「宏定义了」不等于「宏被用了」；查错类 = 界面上什么都不出现。
    if not re.search(r"LookupClass\s*\(\s*FILESBROWSER_CLASS_MANGLED\b", code):
        bad(K_MOUNT_CLASS,
            "没有用 FILESBROWSER_CLASS_MANGLED 去查类：开关挂不到文件列表页上，"
            "界面上不会出现任何东西，也不会报错。")

    # ---- ② 类名必须是 mangled 形式 -------------------------------------
    # Swift 类在 ObjC 运行时的注册名就是 mangled；Blinker.FilesBrowserController
    # 这种写法 NSClassFromString 取不到（返回 Nil），hook 静默不生效。
    if not re.search(
            r'#define\s+FILESBROWSER_CLASS_MANGLED\s+@"_TtC7Blinker22FilesBrowserController"',
            code):
        bad(K_MANGLED,
            "FILESBROWSER_CLASS_MANGLED 的值不是 _TtC7Blinker22FilesBrowserController："
            "Swift 类在 ObjC 运行时的注册名就是 mangled 形式，"
            "用 Blinker.FilesBrowserController 查不到类，hook 静默不生效。")

    # ---- ③ 挂 item 前必须判「本页是不是栈顶」，且必须有回退挂法 ----
    # ★ v1.6.12 修正：v1.6.11 的判据是 `if (!vc.navigationController) return;`，
    #   而这条判据本身是错的 —— 子控制器沿 parent 链也能拿到非 nil 的
    #   navigationController，于是 item 被装进一个**永远不被渲染**的
    #   navigationItem：不显示、不崩溃、不留日志。用户实测「装了什么都不出现」。
    #   真正决定「item 会不会被画」的是 topViewController，不是非 nil。
    inst = _fn_body(code, "OkaInstallXferSwitch")
    if inst is None:
        bad(K_NAVGUARD, "找不到 OkaInstallXferSwitch 函数体：挂载逻辑没落地。")
        bad(K_FALLBACK, "找不到 OkaInstallXferSwitch 函数体：无法确认回退挂法。")
        bad(K_IDEMPOTENT, "找不到 OkaInstallXferSwitch 函数体：无法确认幂等。")
    else:
        # 判据必须精确到「拿 topViewController 跟**本页 vc** 比」。
        # ★ 写成 `\.topViewController\s*==\s*\w+` 是错的：右侧随便什么标识符
        #   都算过，`nav.topViewController == nav.topViewController` 这种
        #   恒真写法照样能蒙过去 —— 那等于判据不存在。必须锁到 vc。
        if not re.search(r"\.topViewController\s*==\s*vc\b", inst):
            bad(K_NAVGUARD,
                "挂 item 前没有判 `navigationController.topViewController == vc`："
                "子控制器沿 parent 链也能拿到非 nil 的 navigationController，"
                "只判非 nil（或跟别的对象比）会把 item 装进永不渲染的 "
                "navigationItem —— 界面上什么都不出现，也不报错"
                "（v1.6.11 的实际故障）。")

        # 回退挂法：判不成栈顶时必须还有别的落点。
        # ★ 判据纪律：不能只查「addSubview: 这个词出现过」——
        #   把回退分支写成 `if (0) { ...addSubview... }` 照样能让那个词出现，
        #   而运行时永远走不到。也不能只查「调用了 OkaXferAttachToNavBar」——
        #   被调函数体里若没有 addSubview，标识符在也挂不上去。
        #   三个条件同时成立才算回退有效：
        #     ① 挂载函数里真的调了某条回退（不是只有分支骨架）；
        #     ② 那个调用不在恒假条件里（可达）；
        #     ③ 被调的挂载助手函数体里真的有 addSubview + 约束。
        def _mounts_into(fn_name, host):
            """助手函数体里是否真的把 ctl 挂到 host 上。

            两种 ObjC 写法都要认，不能只认一种：
                [host addSubview:ctl];     ← 方括号发送，宿主在方法名前面
                addSubview: host            ← 极少见，但语法上也存在
            旧写法只匹配「addSubview: 后面跟宿主」，而真实源码是
            [bar addSubview:ctl] —— 宿主在方法名**前面**，
            于是这条判据对正确实现也报 FAIL（假红），
            正是「判据错了不是代码错了」那一类。
            """
            body_ = _fn_body(code, fn_name) or ""
            return (re.search(r"\[\s*%s\s+addSubview\s*:" % host, body_) is not None
                    or re.search(r"addSubview\s*:\s*%s\b" % host, body_) is not None)

        def _call_reachable(callee):
            """挂载函数里对 callee 的调用是否不在恒假条件内。"""
            for m in re.finditer(r"\b%s\s*\(" % re.escape(callee), inst):
                # 往回找最近的 if (...) 看是不是恒假
                head = inst[max(0, m.start() - 200):m.start()]
                dead = re.findall(r"if\s*\(\s*(?:0|false|NO)\s*\)\s*\{[^{}]*$", head)
                if not dead:
                    return True
            return False

        # B 分支：调 AttachToNavBar，且它真的往 bar 上挂东西
        b_ok = (_call_reachable("OkaXferAttachToNavBar")
                and _mounts_into("OkaXferAttachToNavBar", "bar"))
        # C 分支：调 AttachToView，且它真的往 host 上挂、并约束到 safeArea
        c_body = _fn_body(code, "OkaXferAttachToView") or ""
        c_ok = (_call_reachable("OkaXferAttachToView")
                and _mounts_into("OkaXferAttachToView", "host")
                and "safeAreaLayoutGuide" in c_body)

        if not (b_ok or c_ok):
            bad(K_FALLBACK,
                "判不成栈顶之后没有**可达且真的挂上去**的回退："
                "B 分支要真的调 OkaXferAttachToNavBar 往 navigationBar addSubview，"
                "C 分支要真的调 OkaXferAttachToView 往 host addSubview 并约束到 "
                "safeArea，且调用不能被包在恒假条件里。"
                "只把标识符留在源码里、运行时走不到，等于没有回退 —— "
                "这一页若不是栈顶，开关仍然不出现。")

    # ---- ⑫ 必须有可查询的挂载自证接口 -------------------------------
    # v1.6.11 的教训：挂错了没有任何痕迹可查，用户只说「没出现」，
    # 本机又没有编译器和越狱设备，只能靠静态反汇编猜，猜错一次一个版本。
    # ★ 三个条件都要成立，缺一不可：
    #   ① 函数**有定义**（不是只有调用点 —— 只查调用点的话，
    #      把定义改名成 OkaXferDiagResponseUnused 后，block 里那句
    #      OkaXferDiagResponse() 调用点仍能让判据误以为通过）；
    #   ② 它被注册成 GET /oka-xfer 路由（否则查不到）；
    #   ③ 定义体里真的产出了内容（不是空壳 return nil）。
    diag_def = re.search(r"static\s+id\s+OkaXferDiagResponse\s*\(\s*void\s*\)\s*\{",
                         code)
    if not diag_def:
        bad(K_DIAG,
            "没有挂载自证接口 OkaXferDiagResponse 的**定义**："
            "开关不出现时只能靠猜，无法确认走的是哪条挂载路径。")
    else:
        diag_body = _fn_body(code, "OkaXferDiagResponse") or ""
        if not re.search(r'forKey\s*:\s*@"route"', diag_body):
            bad(K_DIAG,
                "自证接口没有输出 route 字段："
                "查出来也不知道走的是 A/B/C 哪条挂载路径。")
        if not re.search(r'@"GET"\s*,\s*@"/oka-xfer"', code):
            bad(K_DIAG,
                '自证接口没有注册成 GET /oka-xfer 路由：函数写了但查不到。')

    # ---- ⑬ 非 item 挂法必须在页面离开时摘掉 ---------------------------
    # navigationBar / vc.view 挂上去的胶囊不归导航栏管，页面切走不会自己消失，
    # 会顶在下一页的顶栏上。
    # ★ 同样用词边界：「函数改名」不等于「清理实现了」，必须查注册处。
    if not re.search(r"\bmy_fbcViewWillDisappear\s*\(", code):
        bad(K_LEAVE_CLEAN,
            "没有 viewWillDisappear: 清理：挂到 navigationBar / view 上的胶囊"
            "会在页面切走后留在屏上，顶到下一页顶栏。")
    else:
        # 定义了还必须真注册（class_addMethod 或 HookInstanceMethod 都算，
        # 因为本类原本没实现这个方法，两种注册方式都是有效的）。
        reg = re.search(r"(?:class_addMethod|HookInstanceMethod)\s*\(\s*fbc\s*,\s*"
                        r"(?:selDis|@selector\s*\(\s*viewWillDisappear:\s*\))"
                        r"[\s\S]{0,200}?my_fbcViewWillDisappear", code)
        if not reg:
            bad(K_LEAVE_CLEAN,
                "viewWillDisappear: 清理函数没有被注册到 fbc 上："
                "函数写了但挂不上去，页面离开时不会摘胶囊。")

    # ---- ④ 调 App 入口前必须过 outlet 守卫 -----------------------------
    # App 的 changeWifiTransferStateAction: 在 outlet 为 nil 时走 brk #1，
    # 不是抛异常，@try 兜不住。必须先在单独的函数里做早退判定。
    ob = _fn_body(code, "OkaXferOutletsReady")
    if ob is None:
        bad(K_OUTLET_GUARD,
            "缺 OkaXferOutletsReady 守卫：页面未上屏时 outlet 为 nil，"
            "调 changeWifiTransferStateAction: 会撞 App 里的 brk #1 硬崩。")
    else:
        # 守卫必须真的覆盖四个 outlet（少一个就可能在那个位置崩）
        for outlet in ("connectionView", "qrCodeView", "startBtn", "ipAddress"):
            if '"%s"' % outlet not in ob:
                bad(K_OUTLET_GUARD,
                    "outlet 守卫漏了 %s：那一个为 nil 时仍会崩。" % outlet)
        # 守卫必须能拦住 —— 早退分支存在
        if not re.search(r"!\s*OkaXferOutletsReady\s*\(", code):
            bad(K_OUTLET_GUARD,
                "找不到 `!OkaXferOutletsReady(...)` 的早退用法："
                "守卫函数写了但没被用上。")

    # 调用点：每一处调 App 入口，其所在函数必须在白名单里
    call_sites = [m.start() for m in RE_TOGGLE_CALL.finditer(code)]
    if not call_sites:
        bad(K_OUTLET_GUARD, "找不到调 App 开关入口的调用点：开关点了不会有任何反应。")
    for pos in call_sites:
        line = code.count("\n", 0, pos) + 1
        fn = _enclosing_fn(code, pos)
        if fn not in TOGGLE_CALL_ALLOWED:
            bad(K_OUTLET_GUARD,
                "第 %d 行的调用点在 %s 里，不在受守卫封装的名单内："
                "页面未上屏时会撞 brk #1 硬崩。"
                % (line, fn or "（无法识别所在函数）"))

    # 守卫封装的内部顺序：守卫在前、调用在后
    body = _fn_body(code, "OkaXferCallAppToggle")
    if body is None:
        bad(K_OUTLET_GUARD, "找不到 OkaXferCallAppToggle 的函数体：守卫封装没落地。")
    else:
        i_guard = body.find("OkaXferOutletsReady")
        m_call = RE_TOGGLE_CALL.search(body)
        if i_guard < 0:
            bad(K_OUTLET_GUARD,
                "OkaXferCallAppToggle 里没有调 OkaXferOutletsReady："
                "守卫写了但封装没用上。")
        elif m_call and i_guard > m_call.start():
            bad(K_OUTLET_GUARD,
                "OkaXferCallAppToggle 里守卫在调用之后：顺序反了等于没守。")
        if not re.search(r"!\s*OkaXferOutletsReady\s*\(", body):
            bad(K_OUTLET_GUARD,
                "OkaXferCallAppToggle 缺 `!OkaXferOutletsReady(...)` 早退："
                "守卫结果可能没被真正用于拦截。")

    # ---- ⑤ 幂等：识别到已有项就必须早退 -------------------------------
    # v1.6.12 起判重分散在两处：
    #   · 路径 A 在 leftBarButtonItems 里按 customView 的类找；
    #   · 路径 B/C 在宿主 subviews 里按类找（OkaXferReuseIn）。
    # 两处都必须「认出 → 复用/早退」，不能认出来还继续往下追加。
    if inst is not None:
        # A：item 数组里按类判，且**认出后必须停下**
        if "OkaXferBarControl class" not in inst:
            bad(K_IDEMPOTENT,
                "挂载函数里没有按类（OkaXferBarControl）判已有项："
                "无法识别「我上次装的还在不在」，viewWillAppear 每进一次就叠一个。")
        else:
            # ★ 「认出来了」不等于「停下了」。缺 return 会继续往下走到
            #   addObject，每次进页面多叠一颗。必须比位置，不能只比存在。
            i_scan = inst.find("OkaXferBarControl class")
            i_append = inst.find("addObject")
            tail = inst[i_scan:i_append] if i_append > i_scan else inst[i_scan:]
            if i_append > i_scan and not re.search(r"\breturn\b", tail):
                bad(K_IDEMPOTENT,
                    "路径 A 识别到已有胶囊后没有 return 早退：会继续往下追加，"
                    "viewWillAppear 每进一次就多叠一颗。")
        # B/C：宿主 subviews 里按类判，且必须有复用函数
        reuse = _fn_body(code, "OkaXferReuseIn")
        if reuse is None:
            bad(K_IDEMPOTENT,
                "找不到 OkaXferReuseIn：挂到 navigationBar / view 上的路径没有判重，"
                "每次 viewWillAppear 都会再挂一颗。")
        else:
            if "isKindOfClass" not in reuse:
                bad(K_IDEMPOTENT,
                    "OkaXferReuseIn 里没有按类识别已有胶囊：无法复用，只会叠加。")
            # 认出别人的胶囊要清掉（导航栏跨页复用，上一页的会留下来）
            if not re.search(r"removeFromSuperview", reuse):
                bad(K_IDEMPOTENT,
                    "OkaXferReuseIn 没有清掉**不属于本页**的胶囊："
                    "navigationBar 是跨页复用的同一个 view，"
                    "上一页挂上去的会显示在别的页顶栏上。")
        if "leftBarButtonItems" not in inst:
            bad(K_IDEMPOTENT,
                "挂载函数里没读 leftBarButtonItems："
                "不先看现有项就会把 App 自己的按钮顶掉。")

    # ---- ⑥ 四个状态文案必须等长 ---------------------------------------
    # 等长才保证胶囊宽度恒定；否则状态一变导航栏就左右抖一下。
    # ★ 长度必须从**源码实际赋的文案**里取，不能用本文件里的 STATES 常量去比 ——
    #   那是拿自己跟自己比，永远相等，等于没有检查。
    found = re.findall(r'text\s*=\s*@"([^"]*)"', code)
    if not found:
        bad(K_LEN_EQUAL,
            '源码里找不到 text = @"…" 形态的状态文案赋值：无法确认四状态等长。')
    else:
        missing = [s for s in STATES if s not in found]
        if missing:
            bad(K_LEN_EQUAL,
                "缺状态文案 %s：用户要的「未开启/已连接/已停止等」没说全。"
                % "、".join(missing))
        lens = {s: len(s) for s in found}
        if len(set(lens.values())) > 1:
            bad(K_LEN_EQUAL,
                "源码里的状态文案长度不一致 %r：胶囊宽度会随状态跳动"
                "（宽度按 kOkaXferTextW 固定，文字超宽会被截断）。" % lens)

    # ---- ⑦ 两个 hook 都要真注册 ---------------------------------------
    # 只挂 viewDidLoad 的话，进出一次选择模式（App 整套重设按钮）就没了。
    # ★ 「函数定义了」不等于「注册了」——必须查 HookInstanceMethod 的 selector。
    regs = set(re.findall(
        r"HookInstanceMethod\s*\(\s*fbc\s*,\s*@selector\s*\(\s*([A-Za-z_][\w:]*)\s*\)",
        code))
    for sel, why in (
            ("viewDidLoad", ""),
            ("viewWillAppear:", "App 在进出选择模式时会重设 leftBarButtonItems，"
                                "只挂 viewDidLoad 的话开关会永久消失。")):
        if sel not in regs:
            bad(K_BOTH_HOOKS,
                "没有注册 -[FilesBrowserController %s]（%s）" % (sel, why or "首次上屏不装。"))

    # ---- ⑧ 状态必须来自真实计数 ---------------------------------------
    ac = _fn_body(code, "OkaXferActiveConnections")
    if ac is None:
        bad(K_IVAR_CONN,
            "找不到 OkaXferActiveConnections：状态没有数据源。")
        bad(K_NO_FAKE_ZERO,
            "找不到 OkaXferActiveConnections：读不到连接数时的返回约定无从检查。")
    else:
        if not re.search(r'class_getInstanceVariable\s*\(\s*\w+\s*,\s*"_activeConnections"\s*\)', ac):
            bad(K_IVAR_CONN,
                "没读 GCDWebServer._activeConnections："
                "「已连接」只能靠猜，不是真实连接数。")
        # 类型校验：ivar 存在但类型变了（比如从 q 变成 @）时直读会读错位
        if not re.search(r"ivar_getTypeEncoding\s*\(", ac):
            bad(K_IVAR_CONN,
                "没校验 ivar 类型编码：GCDWebServer 换实现（q → @）时会读错位，"
                "读到的是对象指针不是计数。")

        # ---- ⑨ ivar 缺失不许伪造 0 -----------------------------------
        # 读不到 ≠ 没有连接。返回 0 会把「不知道」显示成「待连接」，
        # 是假信息；必须返回负数表示未知。
        # ★ 判据不能是「全文里有 return -1」—— 别的分支也有 return -1，
        #   把这一处改成 0 照样能蒙过去。必须精确锁到 !iv 这条分支。
        m = re.search(r"if\s*\(\s*!\s*iv\s*\)\s*return\s+(-?\d+)\s*;", ac)
        if not m:
            bad(K_NO_FAKE_ZERO,
                "找不到 `if (!iv) return -1;` 这条分支：ivar 取不到时的返回值"
                "无法确认，可能被写成 0 而把「未知」显示成「待连接」。")
        elif int(m.group(1)) >= 0:
            bad(K_NO_FAKE_ZERO,
                "ivar 取不到时返回 %s（应返回 -1）："
                "会把「不知道」渲染成「待连接」，是假状态。" % m.group(1))

    # ---- ⑩ 定时器必须有失效守卫 ---------------------------------------
    # 控件被回收后定时器还在跑 = 空转烧电。
    # ★ 「gXferControl 这个变量存在」不等于「有人检查它」。
    tick = _fn_body(code, "OkaXferStartTicker")
    if tick is None:
        bad(K_TICKER_STOP,
            "找不到 OkaXferStartTicker：胶囊状态不会自己刷新，"
            "用户看到的是点下去那一刻的快照。")
    elif not re.search(r"if\s*\(\s*!\s*gXferControl\s*\)\s*return\s*;", tick):
        bad(K_TICKER_STOP,
            "定时器回调里没有 `if (!gXferControl) return;` 早退："
            "页面走了还在每秒刷新，空转烧电。")

    # ---- ⑪ 热区是整个胶囊 ---------------------------------------------
    # 圆点直径只有 7pt，把点击只挂在小圆点上等于不可点。
    if re.search(r"_dot\s*addGestureRecognizer", code):
        bad(K_POINT_ONLY,
            "手势挂在了小圆点（_dot）上：圆点直径 7pt，点中是碰运气；"
            "应挂在整颗胶囊上。")
    if not re.search(r"(?:\[\s*self\s+addGestureRecognizer"
                     r"|\[\s*ctl\s+addGestureRecognizer)", code):
        bad(K_POINT_ONLY,
            "找不到把手势挂到胶囊自身（self / ctl）的调用：热区没落在胶囊上。")

    return fails, notes


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: check-navswitch.py <Tweak.xm>")
        return 2
    src = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    fails, notes = check(src)
    total = 14
    if fails:
        print("✗ 导航栏 WiFi 传输开关（v1.6.11）判据不满足：%s"
              % ",".join(sorted(fails)))
        for n in notes:
            print("   ·", n)
        return 1
    print("✓ 导航栏 WiFi 传输开关：%d 条判据全部满足" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())