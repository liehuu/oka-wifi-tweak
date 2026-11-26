#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行悬停高亮的 CSS 闸门（v1.6.2）。

用法：python3 devkit/check-hover-css.py web/oka-wifi.css
退出码 0 = 全部通过；1 = 有判据不满足（stderr 说明是哪一条）。

为什么单独成文件，而不是写进 build.sh 的 heredoc：
  写在 heredoc 里就只能靠「另写一份等价实现」来验证它对不对，
  而那证明不了真实闸门有效（照抄逻辑的镜像是自说自话）。
  抽成脚本后，verify_build_gates.py 跑的就是 build.sh 跑的那一份，
  破坏样本能不能被抓到，结论直接对生产生效。

五条判据都对应「写错了也不报错、但效果明显不对」的性质：
  ① :hover > td 的顶层生效色 —— Bootstrap table-striped 把条纹画在 td 上
     (.table-striped > tbody > tr:nth-child(odd) > td)，td 不透明就盖住 tr 的背景。
     只写 tr:hover 的结果是奇数行完全不变色（截图实测：#f9f9f9 不变）。
  ② 悬停色 ≠ 选中色 —— 否则用户分不出「鼠标路过」和「已勾选准备操作」。
  ③ 过渡只能 background-color —— 写 all 会让每格的 border/box-shadow/文字色
     一起过渡，几十行同时重绘，手机上明显掉帧。
  ④ @media (hover: none) 必须把悬停色撤成透明 —— 手机点一下后 :hover 会黏住，
     高亮停在那一行不消失，比没有高亮更糟。
  ⑤ 每个状态的色值只能定义一处 —— 重复定义会让反证失效（删掉一处效果不变），
     判据看着能失败、实际抓不到东西。这条是我在反证时踩出来的。
"""
import re
import sys


def strip_comments(s):
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


def drop_media(s):
    """删掉所有 @media 块（按花括号配平），只留顶层规则。

    ★ 这一步是闸门能不能失败的关键。@media (hover: none) 里有一条同名选择器
      `#listing tr.row-file:hover > td { background-color: transparent }`。
      若只在全文里搜选择器文本或第一个匹配，主规则被整段删掉后照样命中那一条，
      闸门就形同摆设 —— 第一版实现正是这么写的，反证时 MISSED。
    """
    out, i = [], 0
    while True:
        m = re.search(r"@media[^{]*\{", s[i:])
        if not m:
            out.append(s[i:])
            break
        out.append(s[i:i + m.start()])
        j = i + m.end()
        depth = 1
        while j < len(s) and depth:
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def get_media_blocks(s):
    """取出所有 @media 块的内容（不含 @media 头本身）。"""
    blocks, i = [], 0
    while True:
        m = re.search(r"@media[^{]*\{", s[i:])
        if not m:
            break
        head = s[i + m.start():i + m.end() - 1].strip()
        j = i + m.end()
        start = j
        depth = 1
        while j < len(s) and depth:
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
            j += 1
        blocks.append((head, s[start:j - 1]))
        i = j
    return blocks


def rules(s):
    """粗略拆规则块：返回 [(selector, body)]。不处理嵌套，调用前先 drop_media。"""
    return [(m.group(1).strip(), m.group(2))
            for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", s)]


def norm_color(v):
    """把色值规整成可比形式：去首尾空白、去 `!important`、压空格、转小写。

    ★ 必须去掉 `!important`。选中色那条写的是
      `background-color: #e8f3ff !important;`，若把 `!important` 算进色值，
      同一颜色在两处一处带一处不带就会被判成「两个不同颜色」，
      ⑤ 的计数会假 FAIL。这里只比颜色本身，优先级由 CSS 规范决定、不影响判定。
    """
    v = re.sub(r"!important\s*$", "", v.strip().rstrip(";").strip(), flags=re.I)
    return re.sub(r"\s+", " ", v.strip()).lower()


def main(path):
    raw = open(path, encoding="utf-8").read()
    top = drop_media(strip_comments(raw))
    fails = []

    # ---------- ① :hover > td 的顶层生效色 ----------
    hover_td = None
    for sel, body in rules(top):
        if re.search(r"tr\.row-file:hover\s*>\s*td", sel):
            bg = re.search(r"background-color:\s*([^;}]+)", body)
            if bg:
                hover_td = norm_color(bg.group(1))
                break
    if hover_td is None:
        fails.append("① 顶层缺 `#listing tr.row-file:hover > td { background-color: ... }`"
                     "（条纹行是 td 不透明背景，不打到 td 上奇数行不变色）")
    elif hover_td in ("transparent", "none", "initial", "inherit", "unset"):
        fails.append("① `tr.row-file:hover > td` 的背景色是 %s（等于没上色）" % hover_td)

    # ---------- ② 悬停色 ≠ 选中色 ----------
    hover_row = sel_color = None
    for sel, body in rules(top):
        if hover_row is None and re.search(r"\.row-file:hover", sel):
            bg = re.search(r"background-color:\s*([^;}]+)", body)
            if bg:
                hover_row = norm_color(bg.group(1))
        if sel_color is None and re.search(r"\.row-file\.oka-selected", sel):
            bg = re.search(r"background-color:\s*([^;}]+)", body)
            if bg:
                sel_color = norm_color(bg.group(1))
    if hover_row is None:
        fails.append("② 顶层找不到 `.row-file:hover` 的 background-color")
    if sel_color is None:
        fails.append("② 顶层找不到 `.row-file.oka-selected` 的 background-color")
    if hover_row and sel_color and hover_row == sel_color:
        fails.append("② hover=%s 与 selected=%s 相同，分不出「路过」与「已选中」"
                     % (hover_row, sel_color))
    # hover 的 tr 规则与 > td 规则必须是同一个色，否则一行里出现两种颜色（切成三段）
    if hover_row and hover_td and hover_row != hover_td:
        fails.append("② tr 悬停色 %s 与 td 悬停色 %s 不一致（整行会被切成两段色块）"
                     % (hover_row, hover_td))

    # ---------- ③ 过渡白名单，只查 .row-file 相关规则 ----------
    # 范围必须限定：全文件里 `.oka-progress .progress-bar { transition: width .12s linear }`
    # 是进度条自己的宽度动画，和行悬停无关。第一版扫全文件 → 假 FAIL → 卡住发布。
    n_trans = 0
    bad = []
    for sel, body in rules(strip_comments(raw)):
        if ".row-file" not in sel:
            continue
        for t in re.finditer(r"transition:\s*([^;}]+)", body):
            n_trans += 1
            val = t.group(1).strip()
            if not val.startswith("background-color"):
                bad.append("%s -> %s" % (sel.split("\n")[-1].strip(), val))
    if bad:
        fails.append("③ 行悬停的 transition 不是 background-color：%s" % "; ".join(bad))
    if n_trans == 0:
        fails.append("③ `.row-file` 规则里没有任何 transition（悬停切换会硬跳一下）")

    # ---------- ④ hover:none 降级必须真的撤掉悬停色 ----------
    none_block = None
    for head, body in get_media_blocks(strip_comments(raw)):
        if re.search(r"\(\s*hover\s*:\s*none\s*\)", head):
            none_block = body
            break
    if none_block is None:
        fails.append("④ 缺 @media (hover: none)（手机点一下后高亮会黏住不消失）")
    else:
        ok_td = False
        for sel, body in rules(none_block):
            if not re.search(r"tr\.row-file:hover\s*>\s*td", sel):
                continue
            bg = re.search(r"background-color:\s*([^;}]+)", body)
            if bg and norm_color(bg.group(1)) == "transparent":
                ok_td = True
        if not ok_td:
            fails.append("④ @media (hover: none) 里没有把 `:hover > td` 的 background-color "
                         "设成 transparent（触屏降级实际没生效）")

    # ---------- ⑤ 每个状态的色值只定义一处 ----------
    # 重复定义会让反证失效：删掉其中一处效果不变，判据看着能失败、实际抓不到东西。
    #
    # ★★ 数的是「声明处数」，不是「同一颜色出现的处数」（v1.6.3 改）。
    #   原来写 count_color(sel_color, ...)，只统计与选中色**相同**的值 ——
    #   于是往文件末尾追加一条
    #     #listing tr.row-file.oka-selected > td { background-color: #dde9f8; }
    #   能安然通过：颜色不同，计数仍是 1。而它实际更糟 —— 同优先级、位置更靠后，
    #   直接覆盖掉选中色，选中行的 tr 还是 #bdd4f0、td 变成 #dde9f8，一行两个色。
    #   反证脚本 ⑤ 的样本当场 MISSED 抓到了这个漏网（MISSED 比漏报更值得追）。
    def count_bg(pat):
        """符合条件的规则块里，真正声明了 background-color 的个数。"""
        n = 0
        for sel, body in rules(top):
            if not re.search(pat, sel):
                continue
            if re.search(r"background-color:\s*[^;}]+", body):
                n += 1
        return n

    if sel_color:
        n = count_bg(r"\.row-file\.oka-selected")
        if n != 1:
            fails.append("⑤ `.row-file.oka-selected` 上 background-color 声明了 %d 处"
                         "（必须恰好 1 处，否则后写的会覆盖先写的，"
                         "且反证时删掉一处效果不变）" % n)
    if hover_td:
        # 允许 2 处：tr/li/div 合并那条 + `> td` 那条。两者作用对象不同，不是重复
        # （div/li 没有 td 子元素，只留 `> td` 会让列表项悬停完全失效）。
        # 两处必须同色，由 ② 保证。
        n_hov = count_bg(r"\.row-file:hover")
        if n_hov not in (1, 2):
            fails.append("⑤ `.row-file:hover` 上 background-color 声明了 %d 处"
                         "（应为 1 或 2 处：行规则 + td 规则）" % n_hov)

    if fails:
        for f in fails:
            sys.stderr.write("  ✗ %s\n" % f)
        return 1
    print("  hover=%s selected=%s（可区分）" % (hover_td, sel_color))
    print("  .row-file 过渡 %d 处，全部 background-color" % n_trans)
    print("  @media (hover: none) 已把 :hover>td 撤成 transparent")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("用法: check-hover-css.py <oka-wifi.css>\n")
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv[1]))