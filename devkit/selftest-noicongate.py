#!/usr/bin/env python3
# check-no-icon-gate.py 的反证（反向注入三段式）：
#   ① 良性样本（真实 Tweak.xm）必须 PASS；
#   ② 7 个破坏样本（逐个把被删功能的标识符加回代码）必须逐个 FAIL；
#   ③ 还原后复跑仍 PASS。
#
# 为什么每条破坏样本单独注入：一次全塞进去只能证明「闸门会叫」，
#   证明不了「每一条都能单独拦住」—— 漏拦的标识符才是真漏洞。
#   样板都构造自 Tweak.xm 原 v1.6.6-v1.6.9.2 的真实形态（函数定义行 /
#   调用行 / selector / 常量 / 头文件 import / 偏好键），不是生造字符串。
#
# 用法：python3 selftest-noicongate.py <Tweak.xm 路径>
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "check-no-icon-gate.py")

# 每条：(名称, 注入位置锚, 破坏代码) —— 锚都取良性样本里**稳定存在**的行，
# 注入后锚仍在（append 到锚行尾 / 替换锚行），保证 FAIL 只能来自标识符。
INJECTIONS = [
    ("函数定义回流",
     "static void LoadPrefs(void) {",
     "static void OkaSetAppIcon(BOOL on) { (void)on; }"),
    ("调用点回流",
     "static void LoadPrefs(void) {",
     "OkaSetAppIcon(gServerRunning);"),
    ("注入判定回流",
     "static void LoadPrefs(void) {",
     "static BOOL OkaIconSwitchReady(void) { return NO; }"),
    ("装态判定回流",
     "static void LoadPrefs(void) {",
     "if (OkaHostIsAppStore()) { }"),
    ("开关变量回流",
     "static void LoadPrefs(void) {",
     "static BOOL gEnableSwitchIcon = YES;"),
    ("selector 回流",
     "static void LoadPrefs(void) {",
     "SEL s = @selector(setAlternateIconName:completionHandler:);"),
    ("偏好键回流",
     "static void LoadPrefs(void) {",
     'if (d[@"IconSwitch"]) gOn = YES;'),
]


def run_gate(path: str) -> bool:
    r = subprocess.run([sys.executable, GATE, path],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stdout


def main() -> int:
    src_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, "..", "Tweak.xm")
    src = open(src_path, encoding="utf-8", errors="replace").read()

    # ① 良性
    ok, out = run_gate(src_path)
    if not ok:
        print("✗ 良性样本未通过闸门（真实 Tweak.xm 应干净）：")
        print(out)
        return 1
    print("✓ 良性样本 PASS")

    tmpdir = tempfile.mkdtemp(prefix="noicongate-")
    failures = []
    for name, anchor, payload in INJECTIONS:
        assert anchor in src, f"锚点不在样本里: {anchor}"
        bad = src.replace(anchor, anchor + "\n" + payload, 1)
        bad_path = os.path.join(tmpdir, "bad.xm")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write(bad)
        ok, out = run_gate(bad_path)
        verdict = "PASS" if ok else "FAIL"
        print(f"  破坏样本「{name}」→ 闸门 {verdict}（期望 FAIL）")
        if ok:
            failures.append(name)
    for f in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, f))
    os.rmdir(tmpdir)

    # ③ 还原（样本从未被改，这里是对「真实文件仍干净」的落点复核）
    ok, out = run_gate(src_path)
    if not ok:
        print("✗ 还原后复跑未过：")
        print(out)
        return 1
    print("✓ 还原后复跑 PASS")

    if failures:
        print("✗ 以下破坏样本未被拦住（闸门有漏洞）：", "、".join(failures))
        return 1
    print("✓ 反证完成：7/7 破坏样本均被拦截，良性/还原均干净")
    return 0


if __name__ == "__main__":
    sys.exit(main())