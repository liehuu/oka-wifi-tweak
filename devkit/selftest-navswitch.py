#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-navswitch.py 的反证套件（反向注入三段式）。

 ① 良性样本（真实 Tweak.xm）必须 PASS；
 ② 每条判据至少一个破坏样本，且**目标判据键必须精确变红**；
 ③ 还原后复跑仍 PASS，磁盘上的真实 Tweak.xm 字节不变。

★ 为什么断言「具体哪个键红了」而不是「闸门返回 1」
  返回 1 只证明闸门会叫。若 A 判据顺带拦下了 B 判据的样本（误伤），那么
  B 判据本身从未被验证过 —— 拆掉 B 也没人发现。所以每个样本都要求
  目标键 ∈ 失败键集合；顺带红了的其他键单独记录（不判失败，但打印，
  避免「某判据其实没独立验证过」被悄悄掩盖）。

★ 破坏样本的构造纪律
  · payload 一律改成**真会出错的样子**，不做「删掉标识符」这种凑数。
    删标识符类检查本就靠判空，构造容易；真正难拦的是「看起来还在、语义
    已错」——所以重点样本全部走这条路（守卫顺序反了 / 认出已有却不停下 /
    !iv 改 return 0 / 只挂 viewDidLoad）。
  · 锚点取真实 Tweak.xm 里稳定存在的一行；注入后锚仍在，FAIL 只能来自 payload。
  · 全部在内存里替换后写临时文件，绝不改动仓库里的 Tweak.xm。

用法：python3 devkit/selftest-navswitch.py [Tweak.xm 路径]
"""
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "check-navswitch.py")


def _load_gate():
    """按文件路径加载闸门模块，拿判据键常量（避免手抄键名漂移）。"""
    spec = importlib.util.spec_from_file_location("check_navswitch", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load_gate()

# (名称, 目标判据键, 旧片段, 新片段)
INJECTIONS = [
    # ① 挂错类：宏还在，但查的是别的控制器 —— 「宏定义了」骗不过去了
    ("挂到错误的控制器上",
     G.K_MOUNT_CLASS,
     "Class fbc = LookupClass(FILESBROWSER_CLASS_MANGLED,",
     "Class fbc = LookupClass(@\"_TtC7Blinker22SettingsController\","),

    # ② 退回非 mangled 名：Swift 类的 ObjC 注册名就是 mangled，这种写法取不到类
    ("类名退回 Blinker.FilesBrowserController",
     G.K_MANGLED,
     '#define FILESBROWSER_CLASS_MANGLED @"_TtC7Blinker22FilesBrowserController"',
     '#define FILESBROWSER_CLASS_MANGLED @"Blinker.FilesBrowserController"'),

    # ③a 退回 v1.6.11 的错误判据：只判 navigationController 非 nil
    # ★ 这条样本就是 v1.6.11 的真身。它当年能过闸门（因为闸门就是这么写的），
    #   结果用户装完「什么都没出现」。留作永久反证：判据一放松到这个形态，
    #   必须立刻变红。
    ("守卫退回「只判 navigationController 非 nil」（v1.6.11 原错）",
     G.K_NAVGUARD,
     "    if (nav && nav.topViewController == vc) {",
     "    if (nav) {"),

    # ③b 栈顶判据写成恒真：拿 topViewController 跟自己比，永远成立
    # ★ 目标键是 K_NAVGUARD —— 这就是「栈顶判据本身失效」，
    #   不是回退的问题。判据必须能识别「比的不是本页 vc」。
    ("栈顶判据写成恒真（topViewController == nav.topViewController）",
     G.K_NAVGUARD,
     "    if (nav && nav.topViewController == vc) {",
     "    if (nav && nav.topViewController == nav.topViewController) {"),

    # ③c 两条回退**同时**退化成空壳：调用点都还在，但都不再 addSubview。
    # ★ 必须一条样本同时打两处。只掏空一条时另一条仍能挂上去，回退整体
    #   有效，闸门放行是正确行为 —— 那时判「未拦住」是样本错了，不是闸门有洞。
    ("两条回退助手同时退化成空壳",
     G.K_FALLBACK,
     ("    ctl.translatesAutoresizingMaskIntoConstraints = NO;\n"
      "    [host addSubview:ctl];",
      "    ctl.translatesAutoresizingMaskIntoConstraints = NO;\n"
      "    [bar addSubview:ctl];"),
     ("    ctl.translatesAutoresizingMaskIntoConstraints = NO;\n"
      "    (void)host;   // 假装挂上去了",
      "    ctl.translatesAutoresizingMaskIntoConstraints = NO;\n"
      "    (void)bar;    // 假装挂上去了")),

    # ③d 自证接口函数被删掉：出故障就没了可查的事实
    ("删掉自证接口函数",
     G.K_DIAG,
     "static id OkaXferDiagResponse(void) {",
     "static id OkaXferDiagResponseUnused(void) {"),

    # ③e 自证接口写了但没注册成路由
    ("自证接口未注册成路由",
     G.K_DIAG,
     '        fn(server, selAdd, @"GET", @"/oka-xfer", reqCls, (id)xferBlock);',
     '        (void)xferBlock;'),

    # ③f 没有离开清理
    ("没有 viewWillDisappear: 清理",
     G.K_LEAVE_CLEAN,
     "static void my_fbcViewWillDisappear(id self, SEL _cmd, BOOL animated) {",
     "static void my_fbcViewWillDisappearUnused(id self, SEL _cmd, BOOL animated) {"),

    # ④a 守卫封装里顺序颠倒：先调用后守卫
    ("守卫封装里守卫排在调用之后",
     G.K_OUTLET_GUARD,
     "    UIViewController *page = OkaXferResolvePage();\n"
     "    if (!OkaXferOutletsReady(page)) return NO;",
     "    UIViewController *page = OkaXferResolvePage();\n"
     "    if (page == nil) return NO;"),

    # ④b 守卫漏一个 outlet（ipAddress 那一处 brk 就守不住了）
    ("outlet 守卫漏掉 ipAddress",
     G.K_OUTLET_GUARD,
     '"qrCodeView", "startBtn", "ipAddress" }',
     '"qrCodeView", "startBtn" }'),

    # ④c 新增一处裸调：绕过封装直接进那段会 brk 的代码
    ("新函数里裸调 App 开关入口",
     G.K_OUTLET_GUARD,
     "static BOOL OkaXferStart(void) {",
     "static BOOL OkaXferStart(void) {\n"
     "    { UIViewController *page = OkaXferResolvePage();\n"
     "      SEL toggle = NSSelectorFromString(@\"changeWifiTransferStateAction:\");\n"
     "      ((void (*)(id, SEL, id))objc_msgSend)(page, toggle, nil); }"),

    # ⑤a 认出已有胶囊但不 return：会继续往下追加
    ("识别到已有胶囊却没 return",
     G.K_IDEMPOTENT,
     "                    gXferPage = vc;\n"
     "                    OkaXferNote(@\"A-navitem\", vc.navigationItem);\n"
     "                    OkaXferStartTicker();\n"
     "                    OkaXferRefreshAsync();\n"
     "                    return;",
     "                    gXferPage = vc;\n"
     "                    OkaXferNote(@\"A-navitem\", vc.navigationItem);\n"
     "                    OkaXferStartTicker();\n"
     "                    OkaXferRefreshAsync();"),

    # ⑤b 复用函数里认出已有胶囊却不复用、也不清别人的
    ("复用函数不清掉不属于本页的胶囊",
     G.K_IDEMPOTENT,
     "        else [c removeFromSuperview];      // 别人的：上一页遗留，清掉",
     "        else (void)c;                      // 别人的：留着"),

    # ⑥a 状态文案不等长：胶囊宽度会跳
    ("状态文案不等长（已连接 → 已连接中）",
     G.K_LEN_EQUAL,
     'text = @"已连接";',
     'text = @"已连接中";'),

    # ⑥b 少一个状态文案：用户要的「已停止」没了
    ("缺「已停止」状态文案",
     G.K_LEN_EQUAL,
     '        case OkaXferStateStopped:\n'
     '            text = @"已停止";',
     '        case OkaXferStateStopped:'),

    # ⑦a 只挂 viewDidLoad：不注册 viewWillAppear:
    ("不注册 viewWillAppear:（只留 viewDidLoad）",
     G.K_BOTH_HOOKS,
     "        HookInstanceMethod(fbc, @selector(viewWillAppear:),",
     "        HookInstanceMethod(fbc, @selector(viewWillAppearX:),"),

    # ⑦b hook 注册整个消失（函数定义了但没注册）
    ("hook 未注册（只有函数定义）",
     G.K_BOTH_HOOKS,
     "        HookInstanceMethod(fbc, @selector(viewDidLoad),",
     "        (void)0; if (0) HookInstanceMethod(fbc, @selector(viewDidLoadX),"),

    # ⑧a 换一个 ivar 名：读不到真实连接数
    ("连接数改用不存在的 ivar",
     G.K_IVAR_CONN,
     'class_getInstanceVariable(cls, "_activeConnections")',
     'class_getInstanceVariable(cls, "_connectionsCount")'),

    # ⑧b 不校验类型编码：换实现时会读错位
    ("不校验 ivar 类型编码",
     G.K_IVAR_CONN,
     "    const char *type = ivar_getTypeEncoding(iv);",
     "    const char *type = (const char *)0;"),

    # ⑨ ivar 缺失时返回 0：把「未知」显示成「待连接」
    ("ivar 缺失时返回 0（伪造状态）",
     G.K_NO_FAKE_ZERO,
     "    if (!iv) return -1;",
     "    if (!iv) return 0;"),

    # ⑩ 定时器不回早退：页面走了仍在每秒刷新
    ("定时器缺 gXferControl 早退",
     G.K_TICKER_STOP,
     "            if (!gXferControl) return;      // 页面走了：本次什么都不做",
     "            (void)gXferControl;            // 页面走了也照刷"),

    # ⑪a 热区挂到 7pt 小圆点上
    #     锚点带上 initWithTarget:ctl：Tweak.xm 的根因注释里抄了同一行
    #     旧代码（initWithTarget:tap ...），只写 `[ctl addGestureRecognizer`
    #     会先在注释行命中，替换打在注释上、真实调用没动（见注入器的
    #     剥注释校验）。
    ("手势挂到小圆点上",
     G.K_POINT_ONLY,
     "initWithTarget:ctl action:@selector(okaXferTap:)];\n"
     "    [ctl addGestureRecognizer:g];",
     "initWithTarget:ctl action:@selector(okaXferTap:)];\n"
     "    [ctl addGestureRecognizer:g];\n"
     "    [_dot addGestureRecognizer:g];"),

    # ⑪b 手势整个没了
    ("胶囊没有任何手势",
     G.K_POINT_ONLY,
     "initWithTarget:ctl action:@selector(okaXferTap:)];\n"
     "    [ctl addGestureRecognizer:g];",
     "initWithTarget:ctl action:@selector(okaXferTap:)];\n"
     "    (void)g;"),
]

# 良性扰动：改动不涉及任何被检语义，闸门**必须仍 PASS**。
# 防的是「判据过严，把正常改动当违规」——那种闸门会被人绕开或关掉。
BENIGN_EDITS = [
    ("加一条无关注释",
     "static NSInteger OkaXferStateNow(BOOL probePort) {",
     "// 这里的注释不影响任何判据\nstatic NSInteger OkaXferStateNow(BOOL probePort) {"),
    ("改一个局部变量名",
     "    NSInteger cur = OkaXferStateNow(YES);",
     "    NSInteger stateNow = OkaXferStateNow(YES);\n    NSInteger cur = stateNow;"),
]


def run_gate(path: str):
    r = subprocess.run([sys.executable, GATE, path],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def parse_failed_keys(out: str):
    m = re.search(r"判据不满足：([^\n]+)", out)
    if not m:
        return set()
    return set(k.strip() for k in m.group(1).split(",") if k.strip())


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    src_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, "..", "Tweak.xm")
    src_path = os.path.abspath(src_path)
    src = open(src_path, encoding="utf-8", errors="replace").read()
    disk_hash = sha256(src_path)

    # 判据键常量必须都在：selftest 自己也要能失败（键被改名时立刻暴露）
    all_keys = {v for k, v in vars(G).items() if k.startswith("K_")}
    covered = {key for _, key, _, _ in INJECTIONS}
    uncovered = all_keys - covered
    if uncovered:
        print("✗ 以下判据没有反证样本（该判据实际上没被验证过）：%s"
              % "、".join(sorted(uncovered)))
        return 1
    print("✓ 判据覆盖完整：%d 个判据键 / %d 个破坏样本"
          % (len(all_keys), len(INJECTIONS)))

    # ---- ① 良性 ---------------------------------------------------------
    rc, out = run_gate(src_path)
    if rc != 0:
        print("✗ 良性样本未通过闸门（真实 Tweak.xm 应干净）：")
        print(out)
        return 1
    print("✓ 良性样本 PASS")

    tmpdir = tempfile.mkdtemp(prefix="navswitch-")
    bad_path = os.path.join(tmpdir, "bad.xm")
    missed, wrong_key, collateral = [], [], []
    try:
        # ---- ② 逐条破坏 --------------------------------------------------
        # ★ old/new 允许是**元组**（多点同时替换）。有些判据的语义是
        #   「所有同类实现都不能坏」，只打一处另一处仍成立时闸门放行是
        #   正确行为 —— 那种判据必须一条样本同时打掉全部同类实现，
        #   否则「未拦住」的结论是样本错了，不是闸门有洞。
        for name, key, old, new in INJECTIONS:
            olds = old if isinstance(old, tuple) else (old,)
            news = new if isinstance(new, tuple) else (new,)
            if len(olds) != len(news):
                print("✗ 样本「%s」的 old/new 数量不一致：构造有误。" % name)
                return 1
            bad = src
            for o, n in zip(olds, news):
                if o not in bad:
                    print("✗ 样本「%s」的锚点不在当前 Tweak.xm 里（样本过期，需更新）："
                          % name)
                    print("    锚点: %r" % o[:100])
                    return 1
                bad = bad.replace(o, n, 1)
            if bad == src:
                print("✗ 样本「%s」注入后内容未变：构造有误。" % name)
                return 1
            # ★ 改动必须落在**代码**上：锚点若同时是某条注释的子串，
            #   replace(..., 1) 会打中排在前面的注释行，真实调用原封不动
            #   —— 闸门 PASS 是正确行为，但样本什么都没测到。这种
            #   「测了但没测」比漏报更危险：报告会说「未拦住」，
            #   而真实场景里闸门是拦得住的。剥注释后必须真的变了。
            if G.strip_comments(bad) == G.strip_comments(src):
                print("✗ 样本「%s」的替换只发生在注释里（锚点撞上了注释行，"
                      "replace 只改第一处）：真实代码没被破坏，这个样本"
                      "是假的。请把锚点改成带上下文的唯一串。" % name)
                return 1
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write(bad)

            rc, out = run_gate(bad_path)
            keys = parse_failed_keys(out)
            if rc == 0:
                missed.append(name)
                print("  ✗ 破坏样本「%s」→ 闸门 PASS（期望 %s 变红，未被拦住）"
                      % (name, key))
            elif key not in keys:
                wrong_key.append((name, key, sorted(keys)))
                print("  ✗ 破坏样本「%s」→ FAIL，但红的是 %s，不是 %s"
                      % (name, ",".join(sorted(keys)) or "（未解析出键）", key))
            else:
                extra = keys - {key}
                if extra:
                    collateral.append((name, key, sorted(extra)))
                print("  ✓ 破坏样本「%s」→ %s 变红" % (name, key))

        # ---- 良性扰动（不该报错） ----------------------------------------
        for name, old, new in BENIGN_EDITS:
            if old not in src:
                print("✗ 良性扰动的锚点过期：%r" % old[:80])
                return 1
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write(src.replace(old, new, 1))
            rc, out = run_gate(bad_path)
            if rc != 0:
                print("  ✗ 良性扰动「%s」导致 FAIL（判据过严，会被人绕开关掉）：" % name)
                print(out)
                return 1
            print("  ✓ 良性扰动「%s」不误报" % name)
    finally:
        if os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ---- ③ 还原 ---------------------------------------------------------
    rc, out = run_gate(src_path)
    if rc != 0:
        print("✗ 还原后复跑未过：")
        print(out)
        return 1
    if sha256(src_path) != disk_hash:
        print("✗ 磁盘上的 Tweak.xm 在自检过程中被改动（自检不得改被测文件）")
        return 1
    print("✓ 还原后复跑 PASS（Tweak.xm sha256 前后一致：%s…）" % disk_hash[:16])

    if collateral:
        # 不判失败：单点破坏顺带触发邻近判据是合理的（守卫删掉，顺序判据
        # 也一起红）。但要打印，避免「某判据其实没独立验证过」被掩盖。
        print("  ! 以下样本除目标键外还红了其他键（记录，不判失败）：")
        for name, key, extra in collateral:
            print("    · %s：目标 %s，另外红了 %s" % (name, key, ",".join(extra)))
    if missed:
        print("✗ 以下破坏样本完全未被拦住（闸门有漏洞）：", "、".join(missed))
        return 1
    if wrong_key:
        print("✗ 以下破坏样本红了错误的判据（该判据实际未被验证）：")
        for name, key, got in wrong_key:
            print("    · %s：期望 %s，实际 %s" % (name, key, ",".join(got)))
        return 1
    print("✓ 反证完成：%d/%d 破坏样本精确命中目标判据；良性 / 扰动 / 还原均干净"
          % (len(INJECTIONS), len(INJECTIONS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())