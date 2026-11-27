#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-service-heal.py 的反证：每个破坏样本必须精确触发预期判据键。

用法：python3 devkit/selftest-service-heal.py
退出码 0 = 闸门可信；1 = 有破坏样本漏网、误伤或脚本自身 SETUP FAIL。

同名方法论见 selftest-icon-gate.py：
  · 破坏样本 = 把真实源码改回「曾经出过事」或「逻辑上会让自愈失效」的形态；
  · 断言是**双向**的：闸门必须 rc=1 且触发键集合精确等于预期（少了是漏报，
    多了是误伤——误报比漏报更能把人带偏，所以键集合用集合相等而不是包含）；
  · 良性样本必须 rc=0（证明闸门不是什么都拦）；
  · 磁盘上的 Tweak.xm  sha256 前后一致、复原复跑干净。
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TWEAK = os.path.join(os.path.dirname(HERE), "Tweak.xm")
GATE = os.path.join(HERE, "check-service-heal.py")
KEY_RE = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")

with open(TWEAK, encoding="utf-8") as f:
    REAL = f.read()


def run_gate(text):
    fd, path = tempfile.mkstemp(suffix=".xm")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        p = subprocess.run([sys.executable, GATE, path],
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr
    finally:
        os.remove(path)


def grab(pattern, what):
    if REAL.count(pattern) != 1:
        print("[SETUP FAIL] 从 Tweak.xm 取不到唯一 %s （count=%d）—— 写法变了，"
              "本脚本要跟着改" % (what, REAL.count(pattern)))
        sys.exit(1)
    return pattern


def build_cases():
    alive_stub = grab(
        "    BOOL alive = NO;\n", "OkaPortAlive 的 BOOL alive = NO; 行")
    replay = grab(
        "    NSError *err = nil;\n"
        "    BOOL ok = ((BOOL (*)(id, SEL, NSDictionary *, NSError **))objc_msgSend)(\n"
        "        srv, @selector(startWithOptions:error:), gServerOptions ?: @{}, &err);\n",
        "自愈重放两行")
    debounce = grab(
        "    if (gLastHealAt && now - gLastHealAt < 90.0) return;\n",
        "防抖行")
    trigger = grab(
        "\n    OkaSelfHealService();", "回前台自愈调用行")
    pref = grab(
        '    if (d[@"ServiceHeal"])       gEnableServiceHeal = [d[@"ServiceHeal"] boolValue];\n',
        "ServiceHeal 偏好读取行")
    loopback = grab(
        "    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);   // 只探本机，不碰外网\n",
        "回环地址行")
    resume = grab(
        "    dispatch_resume(t);\n", "定时器 resume 行")
    instance = grab(
        "    gServerInstance = self;\n", "实例落袋行")
    proto = grab(
        "static void OkaSelfHealService(void);\n", "OkaSelfHealService 前置声明")

    cases = [
        # S1 探活改读 isRunning：假死时它恒 YES，探了等于没探（用户实测症状）
        ("S1 探活改读 isRunning",
         alive_stub,
         alive_stub.replace("    BOOL alive = NO;\n",
             "    BOOL alive = NO;\n"
             "    alive = ((BOOL (*)(id, SEL))objc_msgSend)(gServerInstance,"
             " @selector(isRunning));\n"),
         {"healNoIsRunning"}),
        # S2 自愈改私路：不重放 startWithOptions:，只把状态位置掉
        ("S2 自愈改私路不重放",
         replay,
         "    NSError *err = nil;\n    BOOL ok = NO;\n",
         {"healReplay"}),
        # S3 删防抖：连续假死时无限重拉
        ("S3 删防抖行",
         debounce, "", {"healDebounce"}),
        # S4 回前台不探活：用户回来看网页第一时间不修
        ("S4 删回前台自愈调用",
         trigger, "", {"healTrigger"}),
        # S5 删偏好读取：plist 关不掉自愈，失控时没有止损手段
        ("S5 删 ServiceHeal 偏好读取",
         pref, "", {"healSwitch"}),
        # S6 探活地址改公网：不只探本机
        ("S6 回环地址改 INADDR_ANY",
         loopback,
         "    addr.sin_addr.s_addr = htonl(INADDR_ANY);\n",
         {"healAlive"}),
        # S7 删 dispatch_resume：timer 创建了永不触发（传统死法）
        ("S7 删 dispatch_resume",
         resume, "", {"healTimer"}),
        # S8 实例不落袋：自愈重放无处可发
        ("S8 my_startWithOptions 不落实例",
         instance, "", {"healInstance"}),
        # S10 删 callout 参数：v1.6.7 首版 CI run 45... 的编译错原样
        #   （too few arguments, expected 7, have 6）。本地没有 iOS SDK
        #   编不了，只能靠这条判据钉参数个数。
        ("S10 CFSocketCreate 删 callout 参数",
         "                                      kCFSocketNoCallBack, NULL, &ctx);\n",
         "                                      kCFSocketNoCallBack, &ctx);\n",
         {"healSocketCtor"}),
        # S9 删前置声明：回到 v1.6.6 首发 CI 挂掉的形态第三次
        ("S9 删 OkaSelfHealService 前置声明",
         proto, "", {"declBeforeUse"}),
    ]
    for name, old, _new, _exp in cases:
        if REAL.count(old) != 1:
            print("[SETUP FAIL] %s：注入锚点出现 %d 次（应为 1，全文替换会误伤）"
                  % (name, REAL.count(old)))
            sys.exit(1)
    return cases


def benign_cases():
    b_comment = grab(
        "   // 只探本机，不碰外网\n", "回环地址行内注释")
    return [("B1 只改注释文字",
             b_comment,
             "   // 仅本机回环，不触外网\n")]


def main():
    before = hashlib.sha256(open(TWEAK, "rb").read()).hexdigest()

    # 1) 真实源码必须通过
    rc, out = run_gate(REAL)
    if rc != 0:
        print("✗ 真实 Tweak.xm 没过闸门（rc=%d）—— 先修闸门或源码，别谈反证" % rc)
        print(out)
        return 1
    print("  ✓ 真实 Tweak.xm 通过闸门")

    bad = 0

    # 2) 破坏样本：rc=1 且触发键集合精确等于预期
    for name, old, new, expect in build_cases():
        mutated = REAL.replace(old, new)
        if mutated == REAL:
            print("  ✗ %s：注入是空操作（替换前后一致）—— 假测试" % name)
            bad += 1
            continue
        rc, out = run_gate(mutated)
        got = set(KEY_RE.findall(out))
        if rc != 1:
            print("  ✗ %s：闸门 rc=%d（应为 1）—— 漏网" % (name, rc))
            bad += 1
        elif got != expect:
            print("  ✗ %s：触发键 %s ≠ 预期 %s" % (name, sorted(got), sorted(expect)))
            bad += 1
        else:
            print("  ✓ %-24s → %s" % (name, "/".join(sorted(got))))

    # 3) 良性样本：必须 rc=0（闸门不是什么都拦）
    for name, old, new in benign_cases():
        mutated = REAL.replace(old, new)
        if mutated == REAL:
            print("  ✗ %s：注入是空操作 —— 良性样本写错了" % name)
            bad += 1
            continue
        rc, out = run_gate(mutated)
        if rc != 0:
            print("  ✗ %s：良性样本被误伤（rc=%d）\n%s" % (name, rc, out))
            bad += 1
        else:
            print("  ✓ %-24s → 放过" % name)

    after = hashlib.sha256(open(TWEAK, "rb").read()).hexdigest()
    if before != after:
        print("  ✗ 磁盘 Tweak.xm 被改动了（sha256 前后不一致）")
        bad += 1
    else:
        print("  ✓ 磁盘 Tweak.xm 未被改动（sha256 前后一致）")

    if bad:
        print("selftest-service-heal: FAIL（%d 处不符）" % bad)
        return 1
    print("selftest-service-heal: PASS（10 个破坏样本精确触发 + 良性样本放过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())