#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify-deb.py 的自测（不需要 macOS、不需要真实 dylib）。

为什么必须要有这个
==================
verify-deb.py 的核心是 C7（架构精确比较）与 C8（LC_CODE_SIGNATURE）。
这两条如果写错，后果是「假通过」——包能出来、能装上、但 dylib 不加载，
且系统不报任何错。这种失效在 CI 上是绿的，只有用户装到手机上才发现。

本机是 Windows，没有 xcrun/lipo/otool，无法用真 dylib 验证；
所以这里**自己按 Mach-O 规范拼字节**，造出确定的样本，
再断言 verify-deb.py 的判定方向正确。

★ 关键：正反两个方向都要测。
  只测「合法的能通过」会发现不了永远通过；
  只测「非法的能拦住」发现不了永远失败。
  所以每个检查项都有 should_pass 与 should_fail 两个用例。

用法
====
    python3 devkit/selftest-verify.py

退出码：0 全部符合预期；1 有偏差（打印期望 vs 实际）。
"""

import io
import os
import re
import struct
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.join(HERE, 'verify-deb.py')
MKDEB = os.path.join(HERE, 'mkdeb.sh')
PY = sys.executable

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
LC_CODE_SIGNATURE = 0x1D
LC_SEGMENT_64 = 0x19

def make_macho(cputype=CPU_TYPE_ARM64, cpusubtype=0, with_sig=True, fat=False,
               cstring=None):
    """构造最小 Mach-O 64 位可执行体（只有头 + 1~2 个 load command）。

    cpusubtype 可以带 capability 高位（arm64e 实测 0x80000002），
    这里原样写入，用于验证 verify-deb.py 是否正确地做 & 0x00FFFFFF 掩码。

    cstring 不为 None 时，会追加一个真实的 __TEXT,__cstring 段（带 1 个 section），
    里面放这段字节 —— C11 要按段取内容，没有段就判不出来（这正是它的职责）。
    """
    cmds = []
    payload_off = 0x1000
    if cstring is None:
        # 一个 LC_SEGMENT_64 占位（真实 dylib 必有，解析器要能正确跳过它）
        seg = struct.pack('<II16sQQQQiiII', LC_SEGMENT_64, 72, b'__TEXT',
                          0, 0x1000, 0, 0x1000, 5, 5, 0, 0)
        cmds.append(seg)
        data = b''
    else:
        # 真段：segment_command_64(72) + section_64(80)，nsects=1
        # ★ sectname 必须是 __const 而不是 __cstring：真实的 kOkaWebCSS 是
        #   `static const char kOkaWebCSS[]`，clang 把它放进 __TEXT,__const
        #   （v1.6.1 实测：__const 130251 字节里含 CSS，__cstring 1913 字节不含）。
        #   自测样本若用 __cstring，就与生产判据不同段 → 自测证明不了生产。
        filesize = payload_off + len(cstring)
        seg = struct.pack('<II16sQQQQiiII', LC_SEGMENT_64, 72 + 80, b'__TEXT',
                          0, filesize, 0, filesize, 5, 5, 1, 0)
        # section_64 = sectname(16) segname(16) addr(8) size(8) offset(4)
        #              align(4) reloff(4) nreloc(4) flags(4) r1(4) r2(4) r3(4) = 80
        sect = struct.pack('<16s16sQQIIIIIIII',
                           b'__const', b'__TEXT',
                           0,                 # addr
                           len(cstring),      # size
                           payload_off,       # offset
                           2,                 # align
                           0,                 # reloff
                           0,                 # nreloc
                           0,                 # flags
                           0,                 # reserved1
                           0,                 # reserved2
                           0)                 # reserved3
        cmds.append(seg + sect)
        data = cstring
    if with_sig:
        # LC_CODE_SIGNATURE: cmd, cmdsize, dataoff, datasize
        cmds.append(struct.pack('<IIII', LC_CODE_SIGNATURE, 16, 0x1000, 0x40))

    body = b''.join(cmds)
    ncmds = len(cmds)
    hdr = struct.pack('<IIIIIIII',
                      MH_MAGIC_64, cputype, cpusubtype,
                      6,            # MH_DYLIB
                      ncmds, len(body),
                      0x00100085,   # flags（随便一个含 DYLD_LINK 的组合）
                      0)
    blob = hdr + body
    if data:
        # 把 __cstring 内容垫到 payload_off（段里声明的 offset）
        blob = blob + b'\x00' * (payload_off - len(blob)) + data
    if fat:
        # 简单包一层 fat 头：magic + nfat_arch + 一个 arch 描述
        nfat = 1
        fat_hdr = struct.pack('>II', 0xCAFEBABE, nfat)
        arch = struct.pack('>iiIII', CPU_TYPE_ARM64, 0, 4096, len(blob), 12)
        blob = fat_hdr + arch + blob
    return blob


# C11 只在产物里校验**性质**（有 :hover>td 规则 / 色值具体 / 对白底对比度
# ≥1.20 / 与选中色不同 / 选中更深），不校验具体用哪个蓝。所以这些自测样本
# 也不该写死某一个版本的颜色 —— 下面每个变体只表达一种设计状态。
def hover_css(hover='#cfe0f5', sel='#bdd4f0',
              selector='#listing tr.row-file:hover > td',
              transition='transition: background-color 120ms ease-out;\n',
              media_first=False):
    """拼一段结构等价于 oka-wifi.css 的悬停/选中 CSS 片段。"""
    main = '%s { background-color: %s; }\n' % (selector, hover)
    selrule = ('#listing tr.row-file.oka-selected,\n'
               '#listing tr.row-file.oka-selected > td {\n'
               '  background-color: %s !important;\n}\n'
               '#listing tr.row-file.oka-selected > td { border-top-color: #93bde8; }\n'
               % sel)
    media = ('@media (hover: none) {\n'
             '  #listing tr.row-file:hover > td { background-color: transparent; }\n'
             '}\n')
    body = main + transition + selrule
    return (media + body) if media_first else (body + media)


# 正向样本：与 oka-wifi.css 当前设计一致的三层蓝色
HOVER_CSS = hover_css().encode()


def extract_hook(name):
    """从 mkdeb.sh 抽出指定装期钩子（postinst / prerm）的 heredoc 原文。

    ★ 合法样本必须与**真包同一来源**：另抄一份「看起来对」的钩子脚本，
      一旦 mkdeb.sh 改了形态（v1.6.13 就把 fork grep 改成 while-read），
      抄的那份不会跟着变，C13 这类新判据立刻把合法样本判死 ——
      那是假 FAIL，比漏报更能把人带偏。单一事实来源在这里。
    """
    src = open(MKDEB, encoding='utf-8').read()
    marker = "cat > \"$STAGE/DEBIAN/%s\" <<'EOF'" % name
    i = src.find(marker)
    if i < 0:
        raise SystemExit('mkdeb.sh 里找不到 %s 的 heredoc 头' % marker)
    j = src.find("\n", src.find("<<'EOF'", i) + 1)   # 首行 EOF 行本身跳过
    k = src.find("\nEOF\n", j)
    if k < 0:
        raise SystemExit('mkdeb.sh 里 %s 的 heredoc 没有结束行' % name)
    body = src[j + 1:k + 1]
    if not body.strip():
        raise SystemExit('mkdeb.sh 的 %s heredoc 是空的' % name)
    return body


def make_control_tar(arch='iphoneos-arm', postinst_mode=0o755, with_postinst=True,
                     with_prerm=True,
                     postinst_body=None, prerm_body=None):
    buf = io.BytesIO()
    tf = tarfile.open(fileobj=buf, mode='w', format=tarfile.GNU_FORMAT)
    control = (
        'Package: com.oka.wifitweak\n'
        'Name: OkaWiFi\n'
        'Version: 9.9.9\n'
        'Architecture: %s\n'
        'Maintainer: test <t@t.t>\n'
        'Description: selftest\n' % arch
    ).encode()
    ti = tarfile.TarInfo('./control')
    ti.size = len(control)
    ti.mode = 0o644
    ti.mtime = 1700000000
    tf.addfile(ti, io.BytesIO(control))

    if with_postinst:
        # v1.6.13 起与 mkdeb.sh 产物同源（见 extract_hook 注释）；
        # postinst_body 参数仅供 C13 反向样本改写成坏形态。
        pi = (postinst_body if postinst_body is not None
              else extract_hook('postinst')).encode()
        ti = tarfile.TarInfo('./postinst')
        ti.size = len(pi)
        ti.mode = postinst_mode
        ti.mtime = 1700000000
        tf.addfile(ti, io.BytesIO(pi))

    if with_prerm:
        # v1.6.10：prerm 负责清理旧版（v1.6.6-v1.6.9.2）桌面动态图标注入残留
        #（删 OkaIconInjected 标记键 + 两张 OkaIconGreen PNG）。
        # 缺了它：卸载后旧版注入的桌面图标残留还原不掉。
        pr = (prerm_body if prerm_body is not None
              else extract_hook('prerm')).encode()
        ti = tarfile.TarInfo('./prerm')
        ti.size = len(pr)
        ti.mode = 0o755
        ti.mtime = 1700000000
        tf.addfile(ti, io.BytesIO(pr))
    tf.close()
    return buf.getvalue()


def make_data_tar(dylib_blob, prefix='', plist_mode=0o644, dylib_mode=0o755,
                  extra_members=(), deiban_member=False, pax=False,
                  bad_helper_blob=None, bad_helper_mode=0o755):
    """构造 data.tar。

    bad_helper_blob 非 None 时按旧版（v1.6.6-v1.6.9.2）布局把 oka-iconinject
    塞进 <prefix>usr/bin/ —— 仅供 C12 反向用例制造「helper 回流」样本，
    合法包绝不允许出现它。

    ★ pax=True 时必须让 mtime 带小数或名字超长，否则**不会**产生 'x' 扩展头：
      tarfile 的 PAX_FORMAT 只在「GNU/ustar 表达不了」时才写扩展头
      （实测：整数 mtime + 短名字 → typeflags=['0']；小数 mtime → ['x','0']）。
      这正是本工程当初踩的坑：Python 3.8+ 默认 PAX_FORMAT，
      而 mtime 一旦带小数秒，每个成员前面就多一个 'x' 头，
      dpkg 报 unsupported PAX tar header type 'x'，整包装不上。
    """
    buf = io.BytesIO()
    fmt = tarfile.PAX_FORMAT if pax else tarfile.GNU_FORMAT
    tf = tarfile.open(fileobj=buf, mode='w', format=fmt)

    # pax 用例用小数 mtime，强制触发扩展头；正常用例用整数，保持 GNU_FORMAT
    mtime = 1700000000.123456 if pax else 1700000000

    # 目录项去重：dylib 路径与 helper 路径在 rootless 下有共同祖先
    # （./var/jb）。真实 mkdeb.sh 从 staged 文件树 tar 出来不会有重复成员，
    # 这个夹具自己拼，必须显式去重，否则每个 rootless 正向包都会被
    # C10 拦下 —— 那是夹具缺陷，不是判据缺陷。
    seen_dirs = set()

    def add_dir(rel):
        if rel in seen_dirs:
            return
        seen_dirs.add(rel)
        ti = tarfile.TarInfo(rel)
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o755
        ti.mtime = mtime
        tf.addfile(ti)

    parts = ([prefix] if prefix else []) + [
        'Library', 'MobileSubstrate', 'DynamicLibraries']
    path = './' + '/'.join(parts)
    for i in range(1, len(parts) + 1):
        add_dir('./' + '/'.join(parts[:i]))

    ti = tarfile.TarInfo(path + '/OkaWiFi.dylib')
    ti.size = len(dylib_blob)
    ti.mode = dylib_mode
    ti.mtime = mtime
    tf.addfile(ti, io.BytesIO(dylib_blob))

    plist = b'<?xml version="1.0"?><plist></plist>\n'
    ti = tarfile.TarInfo(path + '/OkaWiFi.plist')
    ti.size = len(plist)
    ti.mode = plist_mode
    ti.mtime = mtime
    tf.addfile(ti, io.BytesIO(plist))

    if bad_helper_blob is not None:
        hparts = ([prefix] if prefix else []) + ['usr', 'bin']
        hpath = './' + '/'.join(hparts)
        for i in range(1, len(hparts) + 1):
            add_dir('./' + '/'.join(hparts[:i]))
        ti = tarfile.TarInfo(hpath + '/oka-iconinject')
        ti.size = len(bad_helper_blob)
        ti.mode = bad_helper_mode
        ti.mtime = mtime
        tf.addfile(ti, io.BytesIO(bad_helper_blob))

    for name, content, mode in extra_members:
        ti = tarfile.TarInfo(name)
        ti.size = len(content)
        ti.mode = mode
        ti.mtime = mtime
        tf.addfile(ti, io.BytesIO(content))

    if deiban_member:
        c = b'#!/bin/sh\n'
        ti = tarfile.TarInfo('./DEBIAN/postinst')
        ti.size = len(c)
        ti.mode = 0o755
        ti.mtime = mtime
        tf.addfile(ti, io.BytesIO(c))

    tf.close()
    return buf.getvalue()


def ar_member(name, body):
    """构造一个 ar 成员（60 字节头 + body + 奇数补 '\n'）。"""
    hdr = (
        name.ljust(16).encode() +
        b'0'.ljust(12) +          # mtime
        b'0'.ljust(6) +           # uid
        b'0'.ljust(6) +           # gid
        b'100644'.ljust(8) +      # mode
        str(len(body)).encode().ljust(10) +
        b'`\n'
    )
    assert len(hdr) == 60, len(hdr)
    out = hdr + body
    if len(body) % 2:
        out += b'\n'
    return out


def build_deb(dylib_blob, arch='iphoneos-arm', prefix='', **kw):
    """拼一个完整 deb。

    v1.6.10：合法包**永远不带** oka-iconinject（桌面动态图标功能已移除，
    helper 已删）。只有 C12 的反向用例才显式传 bad_helper_blob
    制造「helper 回流」样本。
    """
    ctl = make_control_tar(arch=arch,
                           postinst_mode=kw.pop('postinst_mode', 0o755),
                           with_postinst=kw.pop('with_postinst', True),
                           with_prerm=kw.pop('with_prerm', True),
                           postinst_body=kw.pop('postinst_body', None),
                           prerm_body=kw.pop('prerm_body', None))
    data = make_data_tar(dylib_blob, prefix=prefix, **kw)

    import gzip
    import lzma
    return (b'!<arch>\n'
            + ar_member('debian-binary', b'2.0\n')
            + ar_member('control.tar.gz', gzip.compress(ctl, mtime=0))
            + ar_member('data.tar.xz', lzma.compress(data)))


def run_verify(deb_bytes, scheme, arch, cpu):
    with tempfile.NamedTemporaryFile(suffix='.deb', delete=False) as fh:
        fh.write(deb_bytes)
        path = fh.name
    try:
        p = subprocess.run(
            [PY, VERIFY, path, scheme, arch, '--expect-cpu', cpu],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        return p.returncode, p.stdout + p.stderr
    finally:
        os.unlink(path)


CASES = []


def case(name, should_pass, deb_bytes, scheme, arch, cpu, want_in_output=None):
    """注册一个用例。

    ★ want_in_output 支持 str 或 tuple（全部都要出现）。
    ★★ 传 '[Cxx]' 时会被归一化成 '::error::[Cxx]' —— 这一条很关键：
      verify-deb.py 每段开头都会 print('\n[C11] ...')，所以 `'[C11]' in out`
      永远成立。第一版就是这么写的，于是「反向用例是否真的被这条判据拦住」
      完全没验到：任何一处失败都能让 want 满足，用例看着很严格其实恒真。
      改成匹配 `::error::` 前缀（只有 ck.fail 才会打）才是真判据。
    """
    CASES.append((name, should_pass, deb_bytes, scheme, arch, cpu, want_in_output))


def build_cases():
    # ★ 正向样本必须带真实的 __cstring 段并塞入悬停 CSS ——
    #   C11 判的是「这段 CSS 在不在运行时能取到的段里」，没有段就判失败。
    #   第一版这些样本只有空的 LC_SEGMENT_64 占位，加 C11 后三个合法包全红，
    #   那是自测样本不完整，不是判据错。样本补齐才是正确修法。
    ok_arm64 = make_macho(CPU_TYPE_ARM64, 0, with_sig=True, cstring=HOVER_CSS)
    # arm64e 的 cpusubtype 真实值带 capability 高位
    ok_arm64e = make_macho(CPU_TYPE_ARM64, 0x80000002, with_sig=True, cstring=HOVER_CSS)
    no_sig = make_macho(CPU_TYPE_ARM64, 0, with_sig=False, cstring=HOVER_CSS)
    fat = make_macho(CPU_TYPE_ARM64, 0, with_sig=True, fat=True, cstring=HOVER_CSS)
    # C11 反向样本
    no_cstr = make_macho(CPU_TYPE_ARM64, 0, with_sig=True)          # 完全没有 __const
    cstr_truncated = make_macho(CPU_TYPE_ARM64, 0, with_sig=True,
                                cstring=b'#cfe0f5 only\n')          # 有色值缺选择器/过渡
    # ★ 这几条才是 v1.6.3 改写 C11 的真正目的：
    #   「颜色改了」不该让判据红，「颜色浅到看不出」必须让判据红。
    faint = make_macho(CPU_TYPE_ARM64, 0, with_sig=True,
                       cstring=hover_css(hover='#f0f6fd').encode())   # v1.6.2 的色
    same_as_sel = make_macho(CPU_TYPE_ARM64, 0, with_sig=True,
                             cstring=hover_css(sel='#cfe0f5').encode())
    sel_lighter = make_macho(CPU_TYPE_ARM64, 0, with_sig=True,
                             cstring=hover_css(hover='#bdd4f0',
                                               sel='#cfe0f5').encode())
    only_transparent = make_macho(CPU_TYPE_ARM64, 0, with_sig=True,
                                  cstring=hover_css(hover='transparent').encode())

    # ---- 正向：三个 scheme 各自的合法包 ----
    case('rootful 合法包', True,
         build_deb(ok_arm64, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64')

    case('rootless 合法包', True,
         build_deb(ok_arm64, 'iphoneos-arm64', 'var/jb'),
         'rootless', 'iphoneos-arm64', 'arm64')

    case('roothide 合法包（arm64e）', True,
         build_deb(ok_arm64e, 'iphoneos-arm64e', ''),
         'roothide', 'iphoneos-arm64e', 'arm64e')

    # ---- 反向：每一条都要能拦住 ----

    # C7 的核心假闸门场景：arm64e 冒充 arm64
    case('arm64e 二进制谎称 arm64', False,
         build_deb(ok_arm64e, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C7]')

    case('arm64 二进制谎称 arm64e', False,
         build_deb(ok_arm64, 'iphoneos-arm64e', ''),
         'roothide', 'iphoneos-arm64e', 'arm64e', '[C7]')

    case('fat 二进制（不是单切片）', False,
         build_deb(fat, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C7]')

    # C8
    case('缺 LC_CODE_SIGNATURE', False,
         build_deb(no_sig, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C8]')

    # C2
    case('Architecture 字段错', False,
         build_deb(ok_arm64, 'iphoneos-arm64', ''),   # 内容写 arm64
         'rootful', 'iphoneos-arm', 'arm64', '[C2]')

    # C3 / C4：rootless 却装到 /Library
    case('rootless 载荷缺 var/jb 前缀', False,
         build_deb(ok_arm64, 'iphoneos-arm64', ''),   # prefix 空
         'rootless', 'iphoneos-arm64', 'arm64', '[C3]')

    case('rootless 混入 ./Library 越界成员', False,
         build_deb(ok_arm64, 'iphoneos-arm64', 'var/jb',
                   extra_members=[('./Library/x', b'1', 0o644)]),
         'rootless', 'iphoneos-arm64', 'arm64', '[C4]')

    # C5
    case('DEBIAN 混进 data.tar', False,
         build_deb(ok_arm64, 'iphoneos-arm', '', deiban_member=True),
         'rootful', 'iphoneos-arm', 'arm64', '[C5]')

    # C6
    case('PAX 格式 tar', False,
         build_deb(ok_arm64, 'iphoneos-arm', '', pax=True),
         'rootful', 'iphoneos-arm', 'arm64', '[C6]')

    # C9 权限
    case('dylib 权限 644', False,
         build_deb(ok_arm64, 'iphoneos-arm', '', dylib_mode=0o644),
         'rootful', 'iphoneos-arm', 'arm64', '[C9]')

    case('postinst 权限 644', False,
         build_deb(ok_arm64, 'iphoneos-arm', '', postinst_mode=0o644),
         'rootful', 'iphoneos-arm', 'arm64', '[C9]')

    case('plist 权限 755', False,
         build_deb(ok_arm64, 'iphoneos-arm', '', plist_mode=0o755),
         'rootful', 'iphoneos-arm', 'arm64', '[C9]')

    # ---- C11：行悬停 CSS 必须真的在 __cstring（运行时常量）里 ----
    case('无 __const 段（CSS 没进运行时）', False,
         build_deb(no_cstr, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C11]')

    case('__const 里 CSS 被截断（只剩色值）', False,
         build_deb(cstr_truncated, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C11]')

    # ★ v1.6.3：色值不再是判据常量，所以必须证明「判据真的会跟着设计变红」。
    #   下面四条都是**结构完全正确**、只有颜色不对 —— 如果判据还写死某个色值，
    #   第一条会红（假红：颜色只是换了个蓝），后三条会绿（假绿：色差根本不够）。
    case('悬停色太浅（v1.6.2 的 #f0f6fd，对比 1.09）', False,
         build_deb(faint, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C11]')

    case('悬停色 == 选中色（分不出路过和选中）', False,
         build_deb(same_as_sel, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C11]')

    case('选中色比悬停色浅（层次反了）', False,
         build_deb(sel_lighter, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C11]')

    case('悬停色只有 transparent（没真上色）', False,
         build_deb(only_transparent, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C11]')

    # ★ 正向反向验证：把同一份 CSS 里的 @media 块挪到最前面，
    #   判据必须仍然通过 —— 否则说明它取的是「第一条匹配」而不是
    #   「第一条能解析成颜色的匹配」，那样任何人调整书写顺序都会假红。
    media_first = make_macho(CPU_TYPE_ARM64, 0, with_sig=True,
                             cstring=hover_css(media_first=True).encode())
    case('@media (hover:none) 写在主规则前面（顺序无关）', True,
         build_deb(media_first, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64')

    # ---- C12：桌面动态图标注入链清零（v1.6.10 负向） ----
    # 功能整体移除后，「helper 回流进包」「dylib 符号没删干净」
    # 「清理钩子缺失」都必须能失败；良性包不得被误伤。
    icon_syms = make_macho(CPU_TYPE_ARM64, 0, with_sig=True,
                           cstring=HOVER_CSS + b'\x00'
                                   b'OkaIconGreen\x00OkaIconInjected\x00'
                                   b'CFBundleAlternateIcons\x00')
    alt_sym = make_macho(CPU_TYPE_ARM64, 0, with_sig=True,
                         cstring=HOVER_CSS + b'\x00setAlternateIconName\x00')

    case('deb 混进 oka-iconinject（helper 回流）', False,
         build_deb(ok_arm64, 'iphoneos-arm', '',
                   bad_helper_blob=make_macho(CPU_TYPE_ARM64, 0, with_sig=True)),
         'rootful', 'iphoneos-arm', 'arm64', '[C12]')

    case('rootless 包 var/jb 下混进 oka-iconinject', False,
         build_deb(ok_arm64, 'iphoneos-arm64', 'var/jb',
                   bad_helper_blob=make_macho(CPU_TYPE_ARM64, 0, with_sig=True)),
         'rootless', 'iphoneos-arm64', 'arm64', '[C12]')

    case('dylib 含 OkaIconGreen/OkaIconInjected/CFBundleAlternateIcons', False,
         build_deb(icon_syms, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C12]')

    case('dylib 含 setAlternateIconName（切换链残留）', False,
         build_deb(alt_sym, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64', '[C12]')

    case('缺 prerm（旧版残留还原不掉）', False,
         build_deb(ok_arm64, 'iphoneos-arm', '', with_prerm=False),
         'rootful', 'iphoneos-arm', 'arm64', '[C12]')

    case('缺 postinst（残留清理钩子缺失）', False,
         build_deb(ok_arm64, 'iphoneos-arm', '', with_postinst=False),
         'rootful', 'iphoneos-arm', 'arm64', '[C12]')

    # 正向：合法包不带 helper、dylib 无图标符号，负向判据不得误伤
    case('合法包无 oka-iconinject 且 dylib 无图标符号', True,
         build_deb(ok_arm64, 'iphoneos-arm', ''),
         'rootful', 'iphoneos-arm', 'arm64')

    # ---- C13（v1.6.13）：装期脚本零 fork 扫描 --------------------------
    # 反向 1：postinst/prerm 退回 fork 外部 grep。
    #   v1.6.10-v1.6.12 的「安装慢」根因：逐 app `grep -aq` 读 Info.plist，
    #   iOS 单次 fork 5-15ms × 100+ 个 App = 秒级，装后脚本还是前台同步跑。
    #   注意 postinst/prerm **同时**改：真实钩子是成对同源的，只改一边
    #   造出的样本另一半仍是好形态，闸门放行是对的、样本却是废的。
    grep_scan = (
        '#!/bin/sh\n'
        'set +e\n'
        'for __APPDIR in /var/containers/Bundle/Application/*; do\n'
        '  [ -d "$__APPDIR" ] || continue\n'
        '  for __APP in "$__APPDIR"/*.app; do\n'
        '    grep -aq com.blinker.documents "$__APP/Info.plist" '
        '2>/dev/null || continue\n'
        '  done\n'
        'done\n'
        'exit 0\n'
    )
    case('装期钩子退回 fork grep（安装慢的根因）', False,
         build_deb(ok_arm64, 'iphoneos-arm', '',
                   postinst_body=grep_scan, prerm_body=grep_scan),
         'rootful', 'iphoneos-arm', 'arm64', '[C13]')

    # 反向 2：为「过闸」把清理任务整个删掉。
    #   比 fork grep 更坏：grep 只是慢，删任务是旧版残留还原不掉。
    #   所以 C13 判据二是「必须有 while-read」，不是只查 grep 不在。
    empty_scan = '#!/bin/sh\nexit 0\n'
    case('装期钩子把扫描任务整个删掉（规避 grep）', False,
         build_deb(ok_arm64, 'iphoneos-arm', '',
                   postinst_body=empty_scan, prerm_body=empty_scan),
         'rootful', 'iphoneos-arm', 'arm64', '[C13]')


def main():
    build_cases()
    bad = 0
    print('=' * 70)
    print('verify-deb.py 自测：%d 个用例' % len(CASES))
    print('=' * 70)

    for name, should_pass, deb, scheme, arch, cpu, want in CASES:
        rc, out = run_verify(deb, scheme, arch, cpu)
        got_pass = (rc == 0)
        ok = (got_pass == should_pass)
        if ok and want and (not should_pass):
            # 失败方向还要确认是**这一条**判据拦住的，不是别处先失败。
            # want 归一化成 ::error::[Cxx] —— 段标题 'Cxx' 每次都打，用它判恒真。
            wants = want if isinstance(want, (list, tuple)) else [want]
            errs = [ln for ln in out.splitlines() if '::error::' in ln]
            missing = []
            for w in wants:
                key = '::error::' + w if re.fullmatch(r'\[C\d+\]', w) else w
                if not any(key in ln for ln in errs):
                    missing.append(w)
            if missing:
                ok = False
                reason = ('期望 ::error:: 含 %s，实际 %d 条错误：%s'
                          % ('/'.join(missing), len(errs),
                             ' | '.join(e.strip() for e in errs) or '(无)'))
            else:
                reason = ''
        else:
            reason = '' if ok else 'rc=%d 期望%s' % (
                rc, '通过' if should_pass else '失败')

        if ok:
            print('  ✓ %-32s -> %s' % (name, 'PASS' if got_pass else 'FAIL'))
        else:
            bad += 1
            print('  ✗ %-32s -> %s   %s' % (
                name, 'PASS' if got_pass else 'FAIL', reason))
            # 打失败输出里的 ::error:: 行，便于定位
            for line in out.splitlines():
                if '::error::' in line:
                    print('        %s' % line.strip())

    print('-' * 70)
    if bad:
        print('✗ %d 个用例不符合预期' % bad)
        return 1
    print('✓ 全部 %d 个用例符合预期（正向 %d，反向 %d）'
          % (len(CASES),
             sum(1 for c in CASES if c[1]),
             sum(1 for c in CASES if not c[1])))
    return 0


if __name__ == '__main__':
    sys.exit(main())