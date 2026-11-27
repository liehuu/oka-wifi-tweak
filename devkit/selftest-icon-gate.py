#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 devkit/check-icon-switch.py 做反证：这条闸门到底能不能失败。

★ 关键设计（与 selftest-shortcut-gate.py 同一纪律）：
  本脚本**不重写**闸门逻辑，而是用 subprocess 直接运行
  devkit/check-icon-switch.py —— 也就是 build.sh 构建时运行的那一份。
  「破坏样本能不能被抓到」的结论必须直接对生产闸门生效；
  另写一份等价实现只能证明我理解自洽，对真实闸门零约束力。

判据：
  - 真实 Tweak.xm 必须 rc=0
  - 每个破坏样本必须 rc=1，且实际触发的判据键集合**精确等于**预期集合
    （多了是误伤，少了是漏网）
  - 每个样本必须先确认「注入真的改了文件」，否则是空操作（假测试）
  - 良性样本（只改注释）必须 rc=0，证明闸门不误伤
  - dylib 侧变异只发生在临时 .xm 文件里（磁盘 Tweak.xm 前后 sha256 比对）
  - devkit 侧变异发生在临时目录的**真闸门逐字节副本**上
    （副本 sha256 与原件比对，证明跑的就是生产那份）
  - 复原复跑必须干净 rc=0

用法：python3 devkit/selftest-icon-gate.py
退出码 0 = 闸门可信；1 = 有破坏样本漏网、误伤或脚本自身 SETUP FAIL。
"""
import hashlib
import os
import re
import shutil
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
DEVDIR = os.path.join(REPO, "devkit")
GATE = os.path.join(DEVDIR, "check-icon-switch.py")
TWEAK = os.path.join(REPO, "Tweak.xm")
PY = sys.executable

# devkit 侧文件（闸门用 dirname(__file__) 推这些路径，副本目录要同构）。
HELPER_FILES = [
    "include/oka-icon-common.h",
    "iconinject.m",
    "iconinject-core.h",
    "selftest-iconinject.c",
]

REAL = open(TWEAK, encoding="utf-8").read()
GATE_BYTES = open(GATE, "rb").read()
KEY_RE = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")


def run_gate(text):
    """把 text 落成临时 .xm，跑**真实**闸门，返回 (rc, 全部输出)。

    写盘后读回比对：证明 subprocess 拿到的就是这个文本，不是空操作。
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


def run_gate_devkit(mutate):
    """在临时目录里搭一份**同构** devkit（含真闸门逐字节副本 + 未变异的
    真实 Tweak.xm），按需变异 devkit 侧文件，跑真闸门。

    mutate(tmpdir) 返回被改文件的相对路径列表（用于证明注入非空操作）。
    """
    tmp = tempfile.mkdtemp(prefix="gate-devkit-")
    try:
        gate_copy = os.path.join(tmp, "gate.py")
        shutil.copyfile(GATE, gate_copy)
        # 证明跑的就是生产那份闸门（不是镜像实现，也不是过期副本）。
        if open(gate_copy, "rb").read() != GATE_BYTES:
            return None, "闸门副本与原件不一致"
        shutil.copyfile(TWEAK, os.path.join(tmp, "Tweak.xm"))
        for rel in HELPER_FILES:
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(DEVDIR, rel), dst)
        changed = mutate(tmp)
        if not changed:
            return None, "mutate 没改任何文件（空操作）"
        p = subprocess.run([PY, gate_copy, os.path.join(tmp, "Tweak.xm")],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def patch_file(path, old, new):
    """文件内全文替换；返回是否真的改了（调用方据此判空操作）。"""
    txt = open(path, encoding="utf-8").read()
    if old not in txt:
        return False
    open(path, "w", encoding="utf-8").write(txt.replace(old, new))
    return True


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
    """返回 [(名称, old, new, 预期触发键集合)] —— dylib 侧（临时 .xm）。

    old 全部来自源码真实片段（grab 或原文），且在全文必须恰好出现一次。
    """
    reg_block = (
        "                SEL selActive = @selector(applicationDidBecomeActive:);\n"
        "                if (!class_addMethod(appDel, selActive,\n"
        "                                     (IMP)my_applicationDidBecomeActive, \"v@:@\")) {\n"
        "                    HookInstanceMethod(appDel, selActive,\n"
        "                                       (IMP)my_applicationDidBecomeActive,\n"
        "                                       (void **)&orig_applicationDidBecomeActive,\n"
        "                                       \"-[AppDelegate applicationDidBecomeActive:]\");\n"
        "                }\n"
    )
    stop_pair = grab(r"[ \t]*gServerPort = 0;\n[ \t]*gServerRunning = NO;\n",
                     "my_stop 状态复位两行")
    # 冷启动同步块（★ 注释 + 5 行说明 + 调用行）。
    launch_block = grab(
        r"[ \t]*// ★ v1\.6\.6：按真实服务状态同步桌面图标。\n"
        r"(?:[ \t]*//[^\n]*\n)*"
        r"[ \t]*OkaSetAppIcon\(gServerRunning\);\n",
        "冷启动同步注释块 + 调用行")
    first_ret = grab(r"[ \t]*OkaSetShortcutEnabled\(NO\);\n[ \t]*return r;\n",
                     "自动清分支的 return 两行")
    become_block = grab(
        r"[ \t]*if \(orig_applicationDidBecomeActive\) \{\n"
        r"[ \t]*orig_applicationDidBecomeActive\(self, _cmd, app\);\n"
        r"[ \t]*\}\n"
        r"[ \t]*OkaSetAppIcon\(gServerRunning\);\n",
        "回前台同步块")
    panel_block = grab(
        r"[ \t]*// ★ v1\.6\.6：动态图标是否已实际生效的只读状态行（不可点）。\n"
        r"(?:[ \t]*//[^\n]*\n)*"
        r"[ \t]*BOOL iconReady = OkaIconSwitchReady\(\);\n"
        r"(?:[^\n]*\n)*?"
        r"[ \t]*\[ac addAction:iconRow\];\n",
        "面板状态行整块")
    osguard_block = grab(
        r"[ \t]*if \(!\[\[UIApplication sharedApplication\] respondsToSelector:\n"
        r"[ \t]*@selector\(setAlternateIconName:completionHandler:\)\]\) return;"
        r"(?:  // iOS < 10\.3)?\n",
        "respondsToSelector 系统版本守卫两行")
    constraint_old = grab(
        r"[ \t]*\[btn\.topAnchor constraintEqualToAnchor:view\.safeAreaLayoutGuide\.topAnchor\n"
        r"[ \t]*constant:40\.0\],\n",
        "浮动按钮 topAnchor 约束两行")
    launch_old = grab(
        r"[ \t]*// ★ v1\.6\.6：按真实服务状态同步桌面图标。\n"
        r"(?:[ \t]*//[^\n]*\n)*"
        r"[ \t]*OkaSetAppIcon\(gServerRunning\);\n",
        "冷启动同步调用行（再取一次，M3 用）")

    cases = [
        # M1 删启动同步：启动失败也亮绿（误导），或根本没接启动路径
        ("M1 删启动成功同步",
         grab(r"[ \t]*OkaSetAppIcon\(YES\);\n", "OkaSetAppIcon(YES); 调用行"), "",
         {"startSync"}),
        # M2 删停服同步：停了服务桌面还挂着绿图标（用户实测症状）
        ("M2 删停服同步",
         grab(r"[ \t]*OkaSetAppIcon\(NO\);\n", "OkaSetAppIcon(NO); 调用行"), "",
         {"stopSync"}),
        # M22 停服先图标后状态：与注释声明的「先落状态、再同步图标」相反
        ("M22 停服先图标后状态",
         stop_pair,
         "    gServerPort = 0;\n    OkaSetAppIcon(NO);\n    gServerRunning = NO;\n",
         {"stopSync"}),
        # M3 冷启动同步挪到第一个 return 之后：三个提前出口都不经过它
        ("M3 冷启动同步挪到 return 后",
         ("__TWO_STEP__", launch_old, first_ret), None,
         {"launchSync"}),
        # M4 删回前台同步：外部改图标 / 系统刷新没人纠正
        ("M4 删回前台同步",
         become_block,
         become_block.replace("    OkaSetAppIcon(gServerRunning);\n", ""),
         {"becomeSync"}),
        # M5 注册段只留 SEL 声明：add + hook 全删
        ("M5 注册段删 add+hook",
         reg_block,
         "                SEL selActive = @selector(applicationDidBecomeActive:);\n",
         {"becomeReg"}),
        # M23 注册段套 if (gEnableSwitchIcon)：开关关掉后同步点④不挂载
        ("M23 注册段套开关 gate",
         reg_block,
         "                if (gEnableSwitchIcon) {\n" + reg_block + "                }\n",
         {"becomeNoGate"}),
        # M6 off 态不回 primary：恒绿 = 把关了骗成开着
        ("M6 off 态恒绿",
         "    NSString *name = on ? [NSString stringWithUTF8String:OKA_ICON_NAME_GREEN] : nil;\n",
         "    NSString *name = on ? [NSString stringWithUTF8String:OKA_ICON_NAME_GREEN] :\n"
         "        [NSString stringWithUTF8String:OKA_ICON_NAME_GREEN];\n",
         {"helperSwitch"}),
        # M19 去掉 completionHandler：变参调用旧签名（编译期才炸的那类）
        ("M19 去掉 completionHandler",
         "    [[UIApplication sharedApplication] setAlternateIconName:name\n"
         "                                          completionHandler:^(NSError *err) {\n"
         "        (void)err;   // 切换失败（签名/系统拒绝）：静默保持原图标，语义安全\n"
         "    }];\n",
         "    [[UIApplication sharedApplication] setAlternateIconName:name];\n",
         {"helperSwitch"}),
        # M7 删 App 内开关门控：设置关了行为还在切
        ("M7 删开关门控",
         "    if (!gEnableSwitchIcon)          return;\n", "",
         {"gateSwitch"}),
        # M20 删注入就绪门控：未注入也去切，系统每次都拒（静默失败）
        ("M20 删注入就绪门控",
         "    if (!OkaIconSwitchReady())       return;\n", "",
         {"gateReady"}),
        # M21 删系统版本守卫：iOS < 10.3 unrecognized selector 直接崩
        ("M21 删系统版本守卫",
         osguard_block, "",
         {"osGuard"}),
        # M8 就绪函数读错键：注入状态无从得知
        ("M8 就绪函数读错标记键",
         "OKA_ICON_KEY_INJECTED", "OKA_ICON_KEY_INJECTED_OLD",
         {"readyFn"}),
        # M18 切换函数定义改名：判据签名对不上
        ("M18 切换函数定义改名",
         "static void OkaSetAppIcon(BOOL on) {",
         "static void OkaSetAppIconOld(BOOL on) {",
         {"helperFound"}),
        # M9 按钮文案回退：改名不彻底
        ("M9 按钮文案回退",
         "    [btn setTitle:@\"⚙️ 设置\" forState:UIControlStateNormal];\n",
         "    [btn setTitle:@\"🔒 密码\" forState:UIControlStateNormal];\n",
         {"btnTitle"}),
        # M10 约束回退右下角：又压住 WebDAV 页签
        ("M10 约束回退右下角",
         constraint_old,
         "        [btn.bottomAnchor constraintEqualToAnchor:view.safeAreaLayoutGuide.bottomAnchor\n"
         "                                    constant:-14.0],\n",
         {"btnPos"}),
        # M11 注入被用户否决的角标 API
        ("M11 注入角标 API",
         "    NSString *name = on ? [NSString stringWithUTF8String:OKA_ICON_NAME_GREEN] : nil;\n",
         "    NSString *name = on ? [NSString stringWithUTF8String:OKA_ICON_NAME_GREEN] : nil;\n"
         "    [[UIApplication sharedApplication] setApplicationIconBadgeNumber:1];\n",
         {"noBadgeDylib"}),
        # M12 删面板状态行：桌面没切换时 App 内看不到「未启用」
        ("M12 删面板状态行",
         panel_block, "",
         {"panelRow"}),
        # M13 删偏好读取：plist 写 false 也关不掉
        ("M13 删 IconSwitch 偏好读取",
         "    if (d[@\"IconSwitch\"])        gEnableSwitchIcon  = [d[@\"IconSwitch\"] boolValue];\n",
         "",
         {"prefsRead"}),
        # M26 删前置声明：回到 v1.6.6 首发 CI 挂掉的形态 —— 调用点(panel row)
        #   早于定义，clang 报 implicit declaration + static follows
        #   non-static，三个 scheme 一起死。本地不跑 clang，只能靠这条判据。
        ("M26 删 OkaIconSwitchReady 前置声明",
         "static BOOL OkaIconSwitchReady(void);\n", "",
         {"declBeforeUse"}),
        # M28 按钮常量退回 8.0：用户实测的原始缺陷原样复活（叠加提示标签）。
        #   旧闸门只查用没用 topAnchor，这种回归静默漏网 —— v1.6.7 起钉死下限。
        ("M28 按钮常量退回 8.0",
         constraint_old,
         "        [btn.topAnchor constraintEqualToAnchor:view.safeAreaLayoutGuide.topAnchor\n"
         "                                    constant:8.0],\n",
         {"btnPos"}),
        # M29 ready 回退 infoDictionary：用户实测「卸载重装 deb 仍显示未启用」
        #   的根因形态（启动快照缓存），只查读没读标记键拦不住它。
        ("M29 ready 回退 infoDictionary",
         "    NSString *plistPath = [[NSBundle mainBundle] pathForResource:@\"Info\"\n"
         "                                                          ofType:@\"plist\"];\n"
         "    if (!plistPath) return NO;\n"
         "    NSDictionary *plist = [NSDictionary dictionaryWithContentsOfFile:plistPath];\n"
         "    return plist[[NSString stringWithUTF8String:OKA_ICON_KEY_INJECTED]] != nil;\n",
         "    NSDictionary *plist = [[NSBundle mainBundle] infoDictionary];\n"
         "    return plist[[NSString stringWithUTF8String:OKA_ICON_KEY_INJECTED]] != nil;\n",
         {"readyFn"}),
        # M30 取消 action 挪回第一行：v1.6.6 实测「菜单开关被看漏」的形态
        #   （取消第一、菜单夹后面）。行序回归闸门必须自己炸。
        ("M30 取消 action 挪到菜单行前",
         "    BOOL menuOn = gEnableShortcut;\n",
         "    // 取消放最后（v1.6.7 行序调整）：开关/状态行现在是面板第一眼看到的内容。\n"
         "    [ac addAction:[UIAlertAction actionWithTitle:@\"取消\"\n"
         "                                          style:UIAlertActionStyleCancel\n"
         "                                        handler:nil]];\n\n"
         "    BOOL menuOn = gEnableShortcut;\n",
         {"panelOrder"}),
    ]
    for name, old, _new, _exp in cases:
        if isinstance(old, tuple):   # 两步变异：分别验锚点
            for sub in old[1:]:
                if REAL.count(sub) < 1:
                    print("[SETUP FAIL] %s：注入锚点一次都没出现" % name)
                    sys.exit(1)
            continue
        if REAL.count(old) != 1:
            print("[SETUP FAIL] %s：注入锚点出现 %d 次（应为 1，全文替换会误伤）"
                  % (name, REAL.count(old)))
            sys.exit(1)
    return cases


BENIGN = [
    # 只改注释文字：闸门必须放过（证明它不是什么都拦）。
    ("B1 只改注释文字",
     "        // ★ v1.6.6：启动成功 → 桌面图标切「红染绿」版（OkaIconGreen）。\n",
     None),
]


def mutate_xm(name, old, new):
    """两步变异专用（M3）。返回变异后文本。"""
    _tag, launch_old, first_ret = old
    step1 = REAL.replace(launch_old, "")
    step2 = step1.replace(first_ret,
                          first_ret + "    OkaSetAppIcon(gServerRunning);\n")
    return step2


def devkit_cases():
    """返回 [(名称, mutate_fn, 预期触发键集合)] —— devkit 侧（临时目录副本）。"""
    def m_header(tmp):
        return ["include/oka-icon-common.h"] if patch_file(
            os.path.join(tmp, "include", "oka-icon-common.h"),
            '#define OKA_ICON_NAME_GREEN   "OkaIconGreen"',
            '#define OKA_ICON_NAME_GREEN   "OkaIconGreenX"') else []

    def m_helper_bak(tmp):
        return ["iconinject.m"] if patch_file(
            os.path.join(tmp, "iconinject.m"),
            "com.oka.wifitweak.iconbak",
            "com.oka.wifitweak.iconbakX") else []

    def m_helper_nslog(tmp):
        p = os.path.join(tmp, "iconinject.m")
        txt = open(p, encoding="utf-8").read()
        open(p, "w", encoding="utf-8").write(
            txt + "\nstatic void _oka_dbg(void) { NSLog(@\"x\"); }\n")
        return ["iconinject.m"]

    def m_helper_core(tmp):
        return ["iconinject-core.h"] if patch_file(
            os.path.join(tmp, "iconinject-core.h"),
            "oka_tint_rgba", "oka_tint_rgba_renamed") else []

    def m_helper_selftest(tmp):
        return ["selftest-iconinject.c"] if patch_file(
            os.path.join(tmp, "selftest-iconinject.c"),
            "expect_tint", "expect_tint_renamed") else []

    def m_helper_fw(tmp):
        # 删 MobileCoreServices import：回到 run 43 三个 scheme 挂掉的形态
        return ["iconinject.m"] if patch_file(
            os.path.join(tmp, "iconinject.m"),
            "#import <MobileCoreServices/MobileCoreServices.h>\n", "") else []

    def m_xm_import(tmp):
        return ["Tweak.xm"] if patch_file(
            os.path.join(tmp, "Tweak.xm"),
            '#import "oka-icon-common.h"\n', "") else []

    return [
        ("M14 共源头图标名改名", m_header, {"sharedHeader"}),
        ("M15 helper 备份路径改名", m_helper_bak, {"helperSrc"}),
        ("M16 helper 注入 NSLog", m_helper_nslog, {"helperNoNSLog"}),
        ("M17 dylib 删共源 import", m_xm_import, {"sharedHeader"}),
        ("M24 helper 删 tint 函数", m_helper_core, {"helperSrc"}),
        ("M25 删 host 单测入口", m_helper_selftest, {"helperSrc"}),
        ("M27 helper 删 MobileCoreServices import", m_helper_fw, {"helperFw"}),
    ]


def benign_devkit():
    def m_nslog_comment(tmp):
        # ★ 关键良性样本：注释里**继续保留** NSLog 字样也必须放过 ——
        #   这条锁死「helper 侧检查也要剥注释」的修法（修错就是把注释当代码）。
        p = os.path.join(tmp, "iconinject.m")
        txt = open(p, encoding="utf-8").read()
        old = "// 输出统一走 printf：不用 NSLog（输出会带时间戳/进程前缀，且本项目产物"
        new = "// 输出统一走 printf：本项目纪律禁 NSLog 输出（dpkg 日志要纯文本行）"
        if old not in txt:
            return []
        open(p, "w", encoding="utf-8").write(txt.replace(old, new))
        return ["iconinject.m"]
    return [("B2 helper 注释里保留 NSLog", m_nslog_comment)]


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

    # 2) dylib 侧破坏样本：rc=1 且触发键集合精确等于预期
    for name, old, new, expect in cases:
        if isinstance(old, tuple):
            mutated = mutate_xm(name, old, new)
        else:
            mutated = REAL.replace(old, new)
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

    # 3) devkit 侧破坏样本
    for name, fn, expect in devkit_cases():
        rc, out = run_gate_devkit(fn)
        if rc is None:
            print("  ✗ %s：反向 harness 自身失败：%s" % (name, out))
            bad += 1
            continue
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

    # 4) dylib 侧良性样本：必须 rc=0（闸门不误伤）
    for name, old, _new in BENIGN:
        indent = old[:len(old) - len(old.lstrip())]
        new = indent + "// 良性变更测试：重写这句注释不影响任何判据。\n"
        mutated = REAL.replace(old, new)
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

    # 5) devkit 侧良性样本：必须 rc=0
    for name, fn in benign_devkit():
        rc, out = run_gate_devkit(fn)
        if rc != 0:
            print("  ✗ %s：良性变更被误伤（rc=%d）" % (name, rc))
            print(out)
            bad += 1
            continue
        print("  ✓ %-28s → 放过" % name)

    # 6) 全程不许碰磁盘上的 Tweak.xm
    after = hashlib.sha256(open(TWEAK, "rb").read()).hexdigest()
    if before != after:
        print("  ✗ 磁盘上的 Tweak.xm 被改动了（sha256 前后不一致）")
        bad += 1
    else:
        print("  ✓ 磁盘 Tweak.xm 未被改动（sha256 前后一致）")

    # 7) 复原复跑必须干净
    rc, out = run_gate(REAL)
    if rc != 0:
        print("  ✗ 复原复跑不干净（rc=%d）" % rc)
        print(out)
        bad += 1
    else:
        print("  ✓ 复原复跑干净")

    print("")
    n_dylib = len(cases)
    n_devkit = len(devkit_cases()) + len(benign_devkit())
    if bad:
        print("selftest-icon-gate: FAIL（%d 项不符）" % bad)
        return 1
    print("selftest-icon-gate: PASS（%d 个 dylib 侧破坏样本 + %d 个 devkit 侧样本"
          "全部符合预期，良性样本全部放过）" % (n_dylib, n_devkit))
    return 0


if __name__ == "__main__":
    sys.exit(main())