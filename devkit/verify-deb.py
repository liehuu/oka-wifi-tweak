#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deb 产物校验器（纯标准库，Windows / macOS / Linux 通用）。

为什么要有这个文件
==================
原先这套校验是**内嵌在 .github/workflows/build.yml 的 heredoc 里**的，带来两个死结：

1. **YAML 与 Python 缩进要求互相冲突。**
   YAML 的 `run: |` block scalar 以「第一个非空行的缩进」为基准，
   内容一旦顶格，块就立即结束 → ScannerError（已实测）。
   可 Python heredoc 体又不能带缩进，否则 python3 报 IndentationError（也已实测）。
   两者不能同时满足。当初写内嵌版时 CI 一直卡在更早的编译步骤，
   所以这个矛盾从来没暴露过。

2. **本地零约束力。**
   内嵌版依赖 xcrun / lipo / otool / codesign —— 只有 macOS 有。
   在 Windows 上完全跑不了，等于「只有 CI 挂了才知道校验写错了」。

抽出来以后：workflow 只留一行 `python3 devkit/verify-deb.py`，
本地也能拿 `.build/out/*.deb` 直接跑，判据与 CI 完全一致。

★ 这里用纯标准库自己解析 Mach-O，不调 lipo/otool：
  lipo -info 的输出是句子，`grep -q arm64` 对 arm64e 会假通过
  （"arm64e" 字符串**包含** "arm64"）。自己读 cputype/cpusubtype 才能
  做整值比较，这是本项目最致命的一类假闸门。

检查项
======
C1  ar 归档结构（!<arch> 魔数 + 三个必需成员齐全）
C2  control.tar 里 Architecture 字段 == 期望值
C3  data.tar 载荷路径符合 scheme（rootless 走 ./var/jb/）
C4  rootless 的 data.tar 里**不得**出现 ./Library/...（/ 只读，包会装不上）
C5  data.tar 里不得混入 ./DEBIAN/...（dpkg 只从 control.tar 读）
C6  data.tar 无 PAX 扩展头（typeflag 'x'）—— dpkg 不认 PAX
C7  dylib 的 Mach-O cputype/cpusubtype 与预期架构精确相等（不做子串比较）
C8  dylib 带 LC_CODE_SIGNATURE
C9  权限：.dylib / 脚本 755，plist 644
C10 不出现重复成员名（tar 里同名会覆盖，dpkg 行为不定）
C11 v1.6.3 行悬停的 CSS 真的被内联进 dylib 的 __const 段，且色差够看得出
    （kOkaWebCSS 是 const 数组，落在 __TEXT,__const；不能在 __debug_str 里
    —— 那说明它只被调试信息引用，运行时拿不到这段 CSS，悬停等于没做）
C12 v1.6.10 桌面动态图标（红染绿）功能已整体移除——负向钉死：
    deb 里不得有 oka-iconinject（任何 scheme 前缀）、dylib 二进制不得含
    图标链路符号（OkaIconGreen / OkaIconInjected / CFBundleAlternateIcons /
    setAlternateIconName）；外加 postinst 与 prerm **存在性**（v1.6.10 起
    它们负责清理旧版注入残留，不再注入；C9 只查 mode，文件整个缺失时静默通过）

用法
====
    # 校验整个 deb（CI 用这个）
    python3 devkit/verify-deb.py <deb> <scheme> <arch> [--expect-cpu arm64]

    # 只校验裸 dylib 的架构与签名（build.sh 编译完立刻跑，早于打包）
    python3 devkit/verify-deb.py --check-macho <dylib> --expect-cpu arm64

    scheme: rootful | rootless | roothide
    arch  : iphoneos-arm | iphoneos-arm64 | iphoneos-arm64e

退出码：0 全通过；1 有失败（逐条打印 FAIL，最后汇总）。
"""

import io
import os
import re
import struct
import sys
import tarfile

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# ar 全局魔数
AR_MAGIC = b'!<arch>\n'
AR_HDR_SIZE = 60

# scheme -> (期望 Architecture, 载荷前缀)
# rootful / roothide 都是空前缀：
#   rootful 是真 rootfs 根
#   roothide 的 jbroot 内部结构就是 ./Library/...（不写 /var/jb）
SCHEME_SPEC = {
    'rootful':  ('iphoneos-arm',      ''),
    'rootless': ('iphoneos-arm64',    'var/jb'),
    'roothide': ('iphoneos-arm64e',   ''),
}

# Mach-O cputype / cpusubtype
CPU_TYPE_ARM64 = 0x0100000C
# cpusubtype 低 8 位是真实子类型；高位是 capability（arm64e 实测 0x80000002）
CPU_SUBTYPE_MASK = 0x00FFFFFF
CPU_SUB_ARM64 = 0
CPU_SUB_ARM64E = 2

EXPECT_SUBTYPE = {
    'arm64':  CPU_SUB_ARM64,
    'arm64e': CPU_SUB_ARM64E,
}

CPU_NAMES = {
    (CPU_TYPE_ARM64, CPU_SUB_ARM64):  'arm64',
    (CPU_TYPE_ARM64, CPU_SUB_ARM64E): 'arm64e',
}

LC_CODE_SIGNATURE = 0x1D


class Checker:
    """收集检查结果。刻意不 fail-fast：一次跑完把所有问题列出来，
    否则「修一个红一个」要来回好几轮 CI。"""

    def __init__(self):
        self.fails = []
        self.passes = []

    def ok(self, tag, msg):
        self.passes.append((tag, msg))
        print('  \u2713 [%s] %s' % (tag, msg))

    def fail(self, tag, msg):
        self.fails.append((tag, msg))
        print('  \u2717 [%s] %s' % (tag, msg))

    def check(self, cond, tag, ok_msg, fail_msg):
        if cond:
            self.ok(tag, ok_msg)
        else:
            self.fail(tag, fail_msg)
        return bool(cond)


# ---------------------------------------------------------------------------
# C1: ar 归档解析
# ---------------------------------------------------------------------------

def parse_ar(raw):
    """解析 ar 归档，返回 [(name, body_bytes)]。

    deb 是**未加符号表的** ar 归档，成员顺序固定为
    debian-binary / control.tar.* / data.tar.*，但这里不假设顺序，
    按名字识别，避免排序变化时误判。
    """
    if not raw.startswith(AR_MAGIC):
        raise ValueError('不是 ar 归档：魔数是 %r，期望 %r' % (raw[:8], AR_MAGIC))

    members = []
    off = len(AR_MAGIC)
    while off + AR_HDR_SIZE <= len(raw):
        hdr = raw[off:off + AR_HDR_SIZE]
        if hdr[58:60] != b'`\n':
            raise ValueError('ar 成员头结束符异常，偏移 %d: %r' % (off, hdr[58:60]))

        # 名字字段固定 16 字节，gnu 风格以 '/' 结尾（本工程不写 '/'，两种都剥）
        name = hdr[0:16].decode('ascii', 'replace').strip().rstrip('/')
        try:
            size = int(hdr[48:58].decode('ascii').strip())
        except ValueError:
            raise ValueError('ar 成员 size 字段非法，偏移 %d: %r' % (off, hdr[48:58]))

        body = raw[off + AR_HDR_SIZE: off + AR_HDR_SIZE + size]
        members.append((name, body))

        # 成员之间按偶数对齐：奇数长度补一个 '\n'
        off += AR_HDR_SIZE + size + (size % 2)

    return members


def decompress_member(name, body):
    """按扩展名选解压器。不写死 lzma：mkdeb.sh 换压缩方式时这里要能跟上。"""
    if name.endswith('.xz'):
        import lzma
        return lzma.decompress(body)
    if name.endswith('.gz'):
        import gzip
        return gzip.decompress(body)
    if name.endswith('.bz2'):
        import bz2
        return bz2.decompress(body)
    return body


def read_tar(blob, label):
    """读取 tar，返回 (names, members_by_name, tarfile)。

    ★ 用 mode='r:' 显式要求未压缩流：blob 已经解压过了，
      交给 tarfile 自动探测反而可能因内部压缩标志误判。
    """
    tf = tarfile.open(fileobj=io.BytesIO(blob), mode='r:')
    members = tf.getmembers()
    names = [m.name for m in members]
    by_name = {m.name: m for m in members}
    return names, by_name, tf


# ---------------------------------------------------------------------------
# C6: PAX 扩展头检测
# ---------------------------------------------------------------------------

def find_pax_headers(blob):
    """按 tar 真实结构走一遍，统计 typeflag='x' / 'g' 的扩展头。

    ★ 不能用「盲扫 'PaxHeader' 字符串」的办法：
      普通文件名里也可能出现这几个字母，会误报。
      这里老老实实按 512 字节块 + header 的 size 字段跳块。
    """
    hits = []
    off = 0
    total = len(blob)
    while off + 512 <= total:
        hdr = blob[off:off + 512]
        # 全零块 = 归档结束
        if hdr == b'\x00' * 512:
            break

        name = hdr[0:100].rstrip(b'\x00').decode('utf-8', 'replace')
        try:
            size = int(hdr[124:136].rstrip(b'\x00 \x00').decode('ascii').strip() or '0', 8)
        except ValueError:
            size = 0
        typeflag = hdr[156:157]

        if typeflag in (b'x', b'g'):
            hits.append({'offset': off, 'name': name,
                         'typeflag': typeflag.decode(), 'size': size})

        data_blocks = (size + 511) // 512
        off += 512 + data_blocks * 512
    return hits


# ---------------------------------------------------------------------------
# C7/C8: 纯标准库解析 Mach-O
# ---------------------------------------------------------------------------

def parse_macho(blob):
    """解析 Mach-O 头 + load commands。

    返回 dict：magic / cputype / cpusubtype / subtype_raw / has_sig / is_fat
    只处理 64 位（本工程全是 arm64 / arm64e，不需要 32 位分支）。
    """
    if len(blob) < 32:
        raise ValueError('文件太小，不是 Mach-O（%d 字节）' % len(blob))

    magic = struct.unpack('>I', blob[0:4])[0]

    # 0xCAFEBABE 是大端 fat 魔数；0xBEBAFECA 是小端 fat
    if magic in (0xCAFEBABE, 0xBEBAFECA):
        return {'is_fat': True, 'magic': magic}

    # 64 位小端 MH_MAGIC_64 = 0xFEEDFACF，字节序上读出来是 0xCFFAEDFE
    if magic == 0xCFFAEDFE:
        e = '<'
    elif magic == 0xFEEDFACF:
        e = '>'
    else:
        raise ValueError('未知 Mach-O 魔数 0x%08X' % magic)

    # ★ cputype / cpusubtype 都是 **uint32**，必须用 'I' 不能用 'i'：
    #   CPU_TYPE_ARM64 = 0x0100000C，按有符号读会变成负数，
    #   格式化出来是 "0x-3DB4707" 这种鬼东西，查错时极易被误导。
    cputype, cpu_subtype, _filetype, ncmds, sizeofcmds = struct.unpack(
        e + 'IIIII', blob[4:24])
    # 头部实际布局：magic(4) cputype(4) cpusubtype(4) filetype(4) ncmds(4)
    #             sizeofcmds(4) flags(4) reserved(4) = 32 字节
    _flags = struct.unpack(e + 'I', blob[24:28])[0]

    has_sig = False
    off = 32
    end = 32 + sizeofcmds
    # load commands 从偏移 32 开始
    while off + 8 <= end and ncmds > 0:
        cmd, cmdsize = struct.unpack(e + 'II', blob[off:off + 8])
        if cmdsize < 8 or off + cmdsize > len(blob):
            # 命令长度异常，停止解析但不抛：cpusubtype 已经拿到了，
            # 签名检查单独判失败更有信息量。
            break
        if cmd == LC_CODE_SIGNATURE:
            has_sig = True
        off += cmdsize
        ncmds -= 1
        if ncmds == 0:
            break

    return {
        'is_fat': False,
        'magic': magic,
        'cputype': cputype,
        # 低 24 位才是真实 subtype，高位是 capability（arm64e = 0x80000002）
        'cpusubtype': cpu_subtype & CPU_SUBTYPE_MASK,
        'subtype_raw': cpu_subtype,
        'has_sig': has_sig,
    }


def describe_macho(info):
    if info.get('is_fat'):
        return 'FAT (universal) 0x%08X' % info['magic']
    name = CPU_NAMES.get((info['cputype'], info['cpusubtype']),
                         'cputype=0x%X subtype=0x%X'
                         % (info['cputype'], info['cpusubtype']))
    return '%s (subtype_raw=0x%08X)' % (name, info['subtype_raw'])


LC_SEGMENT_64 = 0x19


def sections_by_name(blob):
    """按段名取出各 __TEXT/__DATA 段的**内容字节**，返回 {sectname: bytes}。

    ★ 为什么必须按段取，不能在全文件搜字节：
      编译器会把源文件里的字符串常量同时写进 __cstring（运行时常量）
      和调试信息（__DWARF 的 .debug_str）。全文件搜到的可能是后者 ——
      那说明字符串只存在于调试信息里，运行时拿不到，功能等于没做。
      这个坑在 v1.6.1 的产物核验里踩过一次（按 UTF-8 全文件搜中文命中 0，
      实际那些中文在 __ustring 里是 UTF-16LE）。

    只处理 64 位、非 fat 的单切片（本工程三种变体都是这样）。
    """
    if len(blob) < 32:
        return {}
    magic = struct.unpack('>I', blob[0:4])[0]
    if magic == 0xCFFAEDFE:
        e = '<'
    elif magic == 0xFEEDFACF:
        e = '>'
    else:
        return {}
    ncmds, sizeofcmds = struct.unpack(e + 'II', blob[16:24])

    out = {}
    off, left = 32, ncmds
    end = 32 + sizeofcmds
    while left > 0 and off + 8 <= end:
        cmd, cmdsize = struct.unpack(e + 'II', blob[off:off + 8])
        if cmdsize < 8 or off + cmdsize > len(blob):
            break
        if cmd == LC_SEGMENT_64:
            # segment_command_64: cmd(4) cmdsize(4) segname(16) vmaddr(8) vmsize(8)
            #   fileoff(8) filesize(8) maxprot(4) initprot(4) nsects(4) flags(4) = 72
            nsects = struct.unpack(e + 'I', blob[off + 64:off + 68])[0]
            so = off + 72
            for _ in range(nsects):
                # section_64: sectname(16) segname(16) addr(8) size(8) offset(4) ...
                if so + 72 > len(blob):
                    break
                sectname = blob[so:so + 16].rstrip(b'\x00').decode('ascii', 'replace')
                size = struct.unpack(e + 'Q', blob[so + 40:so + 48])[0]
                foff = struct.unpack(e + 'I', blob[so + 48:so + 52])[0]
                if foff and size and foff + size <= len(blob):
                    out.setdefault(sectname, b'')
                    out[sectname] += blob[foff:foff + size]
                so += 80   # sizeof(section_64)
        off += cmdsize
        left -= 1
    return out


# ---------------------------------------------------------------------------
# CSS 色值：只判性质，不判具体用哪个蓝
# ---------------------------------------------------------------------------
#
# ★ 为什么不硬编码色值：v1.6.2 的 C11 写死 b'#f0f6fd'，用户一说"高亮太浅、
#   加大色差"，CSS 改完这里就必红。那不是产物坏了，是判据过期 ——
#   而它长得跟真 FAIL 一模一样，会把人带去查产物。
#   色值是设计决定，判据只该管「有没有真的上色、能不能区分、够不够深」。

def css_first_concrete(css_text, pattern):
    """返回 (原始值, rgb) —— 取**第一条能解析成具体颜色**的匹配。

    ★ 为什么不是取第一条匹配就完事：
      同一个选择器会出现两次 —— 主规则给 #cfe0f5，`@media (hover: none)`
      里给 transparent（触屏撤掉悬停）。取第一条只是碰巧对：一旦有人把
      media 块挪到前面，判据就会拿着 transparent 去算对比度，报一个假 FAIL。
      跳过解析不出的值，既能容忍顺序变化，又不放松「必须真上色」这一条
      —— 全都解析不出来的话返回 (None, None)，判据照样红。
    """
    for m in re.finditer(pattern, css_text, re.S):
        val = m.group(1).strip()
        val = val.replace('!important', '').strip().rstrip(';').strip()
        rgb = css_hex_rgb(val)
        if rgb:
            return val, rgb
    return None, None


def css_hex_rgb(val):
    """把 #rgb / #rrggbb / rgb(r,g,b) 解析成 (r,g,b)；其它（transparent 等）返回 None。

    ★ 不做「宽松猜测」：解析不出来就返回 None 让上层 fail ——
    猜一个默认色会让「色值写错」被当成「色值正确」。
    """
    if not val:
        return None
    s = val.strip().lower()
    m = re.fullmatch(r'#([0-9a-f]{3})', s)
    if m:
        return tuple(int(c * 2, 16) for c in m.group(1))
    m = re.fullmatch(r'#([0-9a-f]{6})', s)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    m = re.fullmatch(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,[^)]*)?\)', s)
    if m:
        return tuple(int(m.group(i)) for i in (1, 2, 3))
    return None


def _wcag_lum(rgb):
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (f(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_vs_white(rgb):
    """对白底 #fff 的 WCAG 对比度。1.0 = 完全看不出，越大越明显。"""
    return (1.0 + 0.05) / (_wcag_lum(rgb) + 0.05)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def mode_of(ti):
    """tar 成员的权限位。dir 用 mode，普通文件也用 mode —— tar 里两者同字段。"""
    return ti.mode & 0o777


def check_macho_only(path, expect_cpu=None):
    """独立模式：只校验一个裸 dylib 的架构与签名。

    build.sh 在编译 + codesign 之后立刻调用它，早于打包 ——
    这样「架构错了」在编译阶段就红，不用等打完 deb、跑完 Verify 才发现。
    判据与 deb 模式共用 parse_macho / EXPECT_SUBTYPE，不会两处漂移。
    """
    print('=' * 68)
    print('裸 dylib 校验: %s  (期望 %s)' % (path, expect_cpu or '不校验架构'))
    print('=' * 68)

    ck = Checker()

    if not os.path.isfile(path):
        ck.fail('M1', '文件不存在: %s' % path)
        return finish(ck)

    blob = open(path, 'rb').read()
    print('大小: %d 字节' % len(blob))

    try:
        info = parse_macho(blob)
    except ValueError as exc:
        ck.fail('M1', 'Mach-O 解析失败: %s（文件可能是空的/被截断/不是 Mach-O）' % exc)
        return finish(ck)

    print('Mach-O: %s' % describe_macho(info))

    ck.check(not info['is_fat'], 'M1',
             '单切片（非 fat）', '是 fat 二进制；本工程要求每变体单切片')

    if expect_cpu:
        got = CPU_NAMES.get((info['cputype'], info['cpusubtype']))
        ck.check(got == expect_cpu, 'M2',
                 '架构精确 == %s（cpusubtype 低 24 位 == %d）'
                 % (expect_cpu, EXPECT_SUBTYPE[expect_cpu]),
                 '架构是 %r 而期望 %r —— cpusubtype 不匹配时 '
                 'ellekit/RootHide 会静默跳过，表现为「装了完全没反应」'
                 % (got, expect_cpu))

    ck.check(info['has_sig'], 'M3',
             '存在 LC_CODE_SIGNATURE',
             '没有 LC_CODE_SIGNATURE —— codesign 那步没生效，'
             'ellekit 会静默跳过')

    return finish(ck)


def main(argv):
    # 独立模式要在参数个数检查之前分流：--check-macho 只需要 2 个参数
    if '--check-macho' in argv:
        idx = argv.index('--check-macho')
        if idx + 1 >= len(argv):
            print('✗ --check-macho 后面缺文件路径')
            return 2
        macho_path = argv[idx + 1]
        expect_cpu = None
        if '--expect-cpu' in argv:
            eidx = argv.index('--expect-cpu')
            if eidx + 1 < len(argv):
                expect_cpu = argv[eidx + 1]
        return check_macho_only(macho_path, expect_cpu)

    if len(argv) < 4:
        print(__doc__)
        return 2

    deb_path = argv[1]
    scheme = argv[2]
    arch = argv[3]

    expect_cpu = None
    if '--expect-cpu' in argv:
        expect_cpu = argv[argv.index('--expect-cpu') + 1]
    else:
        # 从 arch 反推 CPU 名：iphoneos-arm64e -> arm64e，其余 -> arm64
        expect_cpu = 'arm64e' if arch.endswith('arm64e') else 'arm64'

    if scheme not in SCHEME_SPEC:
        print('✗ 未知 scheme: %s（支持 %s）' % (scheme, ', '.join(SCHEME_SPEC)))
        return 2
    if expect_cpu not in EXPECT_SUBTYPE:
        print('✗ 未知 CPU: %s（支持 arm64 / arm64e）' % expect_cpu)
        return 2

    expect_arch, prefix = SCHEME_SPEC[scheme]

    print('=' * 68)
    print('deb   : %s' % deb_path)
    print('scheme: %s  -> Architecture=%s  载荷前缀=%s'
          % (scheme, expect_arch, ('/' + prefix) if prefix else '（无，直接在根）'))
    print('cpu   : %s' % expect_cpu)
    print('=' * 68)

    raw = open(deb_path, 'rb').read()
    print('文件大小: %d 字节\n' % len(raw))

    ck = Checker()

    # ---------------- C1: ar 结构 ----------------
    print('[C1] ar 归档结构')
    try:
        members = parse_ar(raw)
    except ValueError as exc:
        ck.fail('C1', str(exc))
        return finish(ck)
    names = [n for n, _ in members]
    print('  成员: %s' % names)
    ck.check('debian-binary' in names, 'C1',
             'debian-binary 存在', '缺 debian-binary')
    control_m = [m for m in members if m[0].startswith('control.tar')]
    data_m = [m for m in members if m[0].startswith('data.tar')]
    ck.check(len(control_m) == 1, 'C1',
             'control.tar 恰好 1 个', 'control.tar 个数=%d' % len(control_m))
    ck.check(len(data_m) == 1, 'C1',
             'data.tar 恰好 1 个', 'data.tar 个数=%d' % len(data_m))
    if ck.fails:
        return finish(ck)

    # debian-binary 内容必须是 "2.0\n"
    db = dict(members)['debian-binary']
    ck.check(db == b'2.0\n', 'C1',
             'debian-binary == "2.0\\n"', 'debian-binary 内容异常: %r' % db)

    # ---------------- C2: Architecture ----------------
    print('\n[C2] control 元数据')
    ctl_blob = decompress_member(control_m[0][0], control_m[0][1])
    ctl_names, ctl_by_name, ctl_tf = read_tar(ctl_blob, 'control.tar')
    print('  控制文件: %s' % ctl_names)
    try:
        ctl_member = ctl_by_name['./control']
    except KeyError:
        try:
            ctl_member = ctl_by_name['control']
        except KeyError:
            ck.fail('C2', 'control.tar 里没有 control 文件')
            return finish(ck)

    ctl_text = ctl_tf.extractfile(ctl_member).read().decode('utf-8', 'replace')
    fields = {}
    for line in ctl_text.splitlines():
        if ':' in line and not line.startswith(' '):
            k, v = line.split(':', 1)
            fields[k.strip()] = v.strip()
    print('  Package=%s Version=%s Architecture=%s'
          % (fields.get('Package'), fields.get('Version'),
             fields.get('Architecture')))
    ck.check(fields.get('Architecture') == expect_arch, 'C2',
             'Architecture == %s' % expect_arch,
             'Architecture 是 %r，期望 %r（装不上/装错位置）'
             % (fields.get('Architecture'), expect_arch))
    for req in ('Package', 'Version', 'Maintainer', 'Description'):
        ck.check(bool(fields.get(req)), 'C2',
                 '%s 非空' % req, '%s 缺失或为空（dpkg 会拒装）' % req)

    # ---------------- C3/C4/C5: data.tar 路径 ----------------
    print('\n[C3] data.tar 载荷路径')
    data_blob = decompress_member(data_m[0][0], data_m[0][1])
    data_names, data_by_name, data_tf = read_tar(data_blob, 'data.tar')
    for n in data_names:
        print('    %s' % n)

    dylib_name = None
    for n in data_names:
        if n.endswith('OkaWiFi.dylib'):
            dylib_name = n
            break

    if prefix:
        want = './%s/Library/MobileSubstrate/DynamicLibraries/OkaWiFi.dylib' % prefix
    else:
        want = './Library/MobileSubstrate/DynamicLibraries/OkaWiFi.dylib'
    ck.check(dylib_name == want, 'C3',
             'dylib 路径 == %s' % want,
             'dylib 路径是 %r，期望 %r' % (dylib_name, want))

    # C4: rootless 绝不能出现 ./Library/...（/ 只读，dpkg 装不上）
    print('\n[C4] rootless 路径前缀纯净性')
    if prefix:
        parts = prefix.split('/')
        ancestors = {'./' + '/'.join(parts[:i]) for i in range(1, len(parts) + 1)}
        allowed = './%s/' % prefix
        bad = [n for n in data_names if not (n in ancestors or n.startswith(allowed))]
        ck.check(not bad, 'C4',
                 '所有成员都在 ./%s/ 之下（祖先目录已豁免）' % prefix,
                 '发现 %d 个越界成员（rootless 上 / 只读，整包会装不上）: %s'
                 % (len(bad), bad[:6]))
    else:
        bad = [n for n in data_names
               if n == './var/jb' or n.startswith('./var/jb/')]
        ck.check(not bad, 'C4',
                 '无 ./var/jb 残留', '出现 %d 个 ./var/jb 成员: %s' % (len(bad), bad[:6]))

    # C5: DEBIAN 不能进 data.tar
    print('\n[C5] DEBIAN 隔离')
    bad = [n for n in data_names if n.startswith('./DEBIAN') or n.startswith('DEBIAN')]
    ck.check(not bad, 'C5',
             'data.tar 无 DEBIAN 成员',
             'DEBIAN 混进 data.tar（会解到 /DEBIAN，dpkg 读错）: %s' % bad[:6])

    # ---------------- C6: PAX 头 ----------------
    print('\n[C6] tar 格式（PAX 头）')
    for label, blob in (('control.tar', ctl_blob), ('data.tar', data_blob)):
        pax = find_pax_headers(blob)
        ck.check(not pax, 'C6',
                 '%s 无 PAX 扩展头（GNU_FORMAT）' % label,
                 '%s 有 %d 个 PAX 扩展头（dpkg 报 unsupported PAX tar header）: %s'
                 % (label, len(pax),
                    [(p['name'], p['typeflag']) for p in pax[:4]]))

    # C10: 重复成员
    print('\n[C10] 成员唯一性')
    dup = sorted({n for n in data_names if data_names.count(n) > 1})
    ck.check(not dup, 'C10',
             'data.tar 无重复成员名', 'data.tar 有重复成员名: %s' % dup[:6])

    # ---------------- C7/C8: Mach-O ----------------
    print('\n[C7] dylib 架构（Mach-O cputype/cpusubtype 精确比较）')
    blob = b''
    if dylib_name:
        blob = data_tf.extractfile(data_by_name[dylib_name]).read()
    ck.check(bool(blob), 'C7', '取到 dylib（%d 字节）' % len(blob), '取不到 dylib')

    if blob:
        try:
            info = parse_macho(blob)
            print('  Mach-O: %s' % describe_macho(info))
            ck.check(not info['is_fat'], 'C7',
                     '单切片（非 fat）', '是 fat 二进制，期望单切片')
            # ★ fat 时 parse_macho 只返回 {is_fat, magic}，没有 cputype/subtype。
            #   第一版直接 info['cputype'] → KeyError → 整个脚本 traceback 退出，
            #   finish() 不执行、::error:: 不打印，CI 上只看得到崩溃栈。
            #   那就等于 C7 的「fat 拦截」形同虚设：包确实被拦了（rc≠0），
            #   但拦它的是一个异常，不是一条判据，日志里说不出为什么。
            if info['is_fat']:
                ck.fail('C7', 'fat 二进制没有单一 cputype，无法做架构精确比较 —— '
                              '被拦下是对的，但原因是「架构不唯一」，'
                              '不是「架构写错」')
            else:
                got = CPU_NAMES.get((info['cputype'], info['cpusubtype']))
                ck.check(got == expect_cpu, 'C7',
                         '架构精确 == %s' % expect_cpu,
                         '架构是 %r，期望 %r（cpusubtype 不匹配时 dylib 会被静默跳过）'
                         % (got, expect_cpu))
        except ValueError as exc:
            ck.fail('C7', 'Mach-O 解析失败: %s' % exc)
            info = None
    else:
        info = None

    print('\n[C8] 代码签名')
    if info and not info.get('is_fat'):
        ck.check(info['has_sig'], 'C8',
                 '存在 LC_CODE_SIGNATURE',
                 '没有 LC_CODE_SIGNATURE —— ellekit/RootHide 会静默跳过，'
                 '表现是「装了完全没反应」')

    # ---------------- C9: 权限 ----------------
    print('\n[C9] 文件权限')
    if dylib_name:
        m = mode_of(data_by_name[dylib_name])
        ck.check(m == 0o755, 'C9',
                 'dylib 权限 755', 'dylib 权限是 %o，期望 755' % m)

    for n in ctl_names:
        if n.startswith('./postinst') or n.startswith('./postrm') \
                or n.startswith('./preinst') or n.startswith('./prerm'):
            m = mode_of(ctl_by_name[n])
            ck.check(m == 0o755, 'C9',
                     '%s 权限 755' % n, '%s 权限是 %o，期望 755' % (n, m))

    for n in data_names:
        if n.endswith('.plist'):
            m = mode_of(data_by_name[n])
            ck.check(m == 0o644, 'C9',
                     '%s 权限 644' % n, '%s 权限是 %o，期望 644' % (n, m))

    # ---------------- C11: 行悬停 CSS 真的进了运行时，且色差够看得见 ----------
    # ★ 判据盯的是「这段 CSS 在不在运行时能取到的段里」，不是「源码里写没写」。
    #   gen-webassets.py 把它编成 kOkaWebCSS，Tweak.xm 在 epilogue 里追加到页面
    #   —— 少任何一环，真机上就是没效果。
    #
    # ★★ 段名必须查 __const，不是 __cstring。
    #   kOkaWebCSS 是 `static const char kOkaWebCSS[]`，clang 把常量数组放进
    #   __TEXT,__const。实测 v1.6.1 产物：__const 130251 字节里含 #e8f3ff 与
    #   tr.row-file.oka-selected，而 __cstring 只有 1913 字节、不含任何 CSS。
    #   第一版写成查 __cstring，会在每个包上假 FAIL —— 判据找错地方比不查更糟，
    #   它会让人去怀疑产物，而真正的错在判据。
    #
    # ★ 为什么不能在全文件搜字节：__DWARF/__debug_str 里也有源文件的字符串，
    #   搜到了只说明「调试信息里有」，运行时拿不到。必须按段取。
    #
    # ★★ 具体色值不再硬编码（v1.6.3 起）。
    #   原来这里写死 b'#f0f6fd'：用户一句「高亮太浅，加大色差」，CSS 改了颜色，
    #   这里就必红 —— 26 项照常通过、C11 三项全红，看着像产物坏了，实际是
    #   判据过期。判据跟着色值走，等于每次调色都要改测试，这种判据迟早会被
    #   顺手删掉。现在只校验**性质**，色值本身交给设计决定：
    #     (a) :hover > td 那条规则存在，且色值不是透明系（真上色了）
    #     (b) 悬停色与选中色不同（同时出现时能区分「路过」和「已选中」）
    #     (c) 悬停色对白底的对比度 >= 1.20（`能看出色差` 的量化下限）
    #     (d) 选中色比悬停色更深（层次不能反）
    #   (c) 是可失败的关键一条：#f0f6fd 对白底只有 1.09，会被它直接拦下 ——
    #   也就是用户这次反馈的那句「区别不明显」，现在有判据守着了。
    print('\n[C11] 行悬停 CSS 已内联进运行时，色差达标')
    if blob:
        try:
            secs = sections_by_name(blob)
        except Exception as exc:                       # noqa: BLE001
            secs = {}
            ck.fail('C11', '段解析异常: %s' % exc)
        cst = secs.get('__const', b'')
        ck.check(bool(cst), 'C11',
                 '__const 段存在（%d 字节）' % len(cst),
                 '__const 段缺失或为空，取不到内联 CSS 常量（kOkaWebCSS 是 const 数组）')
        if cst:
            css_text = cst.decode('utf-8', 'replace')

            # 结构两条：选择器与过渡。缺 `> td` 则条纹行根本不变色；
            # 缺 transition 则切换硬跳（不报错，但手感明显不对）。
            for needle, why in (
                ('#listing tr.row-file:hover > td', ':hover > td 选择器（条纹行靠它上色）'),
                ('transition: background-color 120ms ease-out',
                 '过渡声明（缺了切换会硬跳）'),
            ):
                ck.check(needle in css_text, 'C11',
                         '运行时 CSS 含 %s' % why,
                         '运行时 CSS 缺 %s（%r）—— 页面拿不到这段，真机上悬停无效'
                         % (why, needle))

            # 从产物里解析色值，而不是从源码/常量里取。
            hover, hrgb = css_first_concrete(
                css_text,
                r'tr\.row-file:hover\s*>\s*td\s*\{[^}]*?background-color\s*:\s*([^;}]+)')
            sel, srgb = css_first_concrete(
                css_text,
                r'oka-selected[^{}]*\{[^}]*?background-color\s*:\s*([^;}]+)')

            ck.check(hover is not None, 'C11',
                     '悬停色解析到 %s' % (hover or ''),
                     '产物 CSS 里没解析出 tr.row-file:hover > td 的具体 background-color '
                     '—— 要么规则被删了，要么色值写成 transparent/inherit，'
                     '要么写法变了导致取不到（写法变了判据要跟着改）')
            ck.check(sel is not None, 'C11',
                     '选中色解析到 %s' % (sel or ''),
                     '产物 CSS 里没解析出 .oka-selected 的具体 background-color —— '
                     '选中态丢了，悬停和选中会糊成一样')

            if hover is not None and sel is not None:
                hc = contrast_vs_white(hrgb)
                sc = contrast_vs_white(srgb)
                ck.check(hc >= 1.20, 'C11',
                         '悬停色 %s 对白底对比度 %.3f:1 ≥ 1.20（看得出色差）'
                         % (hover, hc),
                         '悬停色 %s 对白底对比度只有 %.3f:1 < 1.20 —— '
                         '色差太小，在低亮度屏/护眼模式下几乎看不出，'
                         '等于这个功能没做（v1.6.2 的 #f0f6fd 就是 1.09）'
                         % (hover, hc))
                ck.check(hover.lower() != sel.lower(), 'C11',
                         '悬停色与选中色不同（%s / %s）' % (hover, sel),
                         '悬停色与选中色相同（都是 %s）—— 悬停和选中无法区分，'
                         '用户会以为悬停就已经选中了' % hover)
                ck.check(sc > hc, 'C11',
                         '选中色 %s 比悬停色深（%.3f > %.3f）' % (sel, sc, hc),
                         '选中色 %s 的对比度 %.3f 不高于悬停色 %.3f —— '
                         '层次反了，选中看起来比路过还浅' % (sel, sc, hc))

            # ★ 负向：选择器不该只活在调试信息里。
            #   若 __const 里没有、__debug_str 里有，说明这份 CSS 没被当成
            #   运行时常量编进去（只被日志/断言引用），线上拿不到。
            dbg = secs.get('__debug_str', b'')
            ck.check(
                not (dbg and b'tr.row-file:hover > td' in dbg
                     and b'tr.row-file:hover > td' not in cst),
                'C11',
                '悬停 CSS 在运行时段内，不只在调试信息里',
                '悬停选择器只出现在 __debug_str（调试信息），没进 __const —— '
                '运行时拿不到这段 CSS，真机上悬停不会生效')
    else:
        ck.fail('C11', '没取到 dylib，无法核验内联 CSS')

    # ---------------- C12: v1.6.10 桌面动态图标（红染绿）已移除 ----------------
    # v1.6.6-v1.6.9.2 曾有「红染绿」桌面动态图标：安装期 helper 注入 +
    # dylib 调 setAlternateIconName。该功能实测从未生效，v1.6.10 整体移除。
    # 这里三条负向 + 一条存在性，钉住「移除要移干净」：
    #   (1) deb 里不得有 oka-iconinject（任何 scheme 前缀都不行）
    #   (2) dylib 二进制不得含图标链路符号（注入残留的字符串指针）
    #   (3) postinst / prerm 必须在 control.tar 里 —— 它们现在负责清理
    #       旧版已注入残留（删 OkaIconInjected + PNG），C9 只查 mode，
    #       文件整个缺失时静默通过，必须单独钉存在性
    print('\n[C12] 桌面动态图标已移除，注入链清零')
    bad_helper = sorted(n for n in data_names
                        if n.rstrip('/').endswith('usr/bin/oka-iconinject'))
    ck.check(not bad_helper, 'C12',
             'deb 里没有 oka-iconinject',
             'deb 里混进了 oka-iconinject：%s —— v1.6.6-v1.6.9.2 的注入 '
             'helper 已随功能移除，不得回流（防呆见 build.sh 4-0d）'
             % bad_helper[:3])

    if blob:
        leaked = [s for s in ('OkaIconGreen', 'OkaIconInjected',
                              'CFBundleAlternateIcons',
                              'setAlternateIconName')
                  if s.encode() in blob]
        ck.check(not leaked, 'C12',
                 'dylib 不含桌面动态图标链路符号',
                 'dylib 含图标链路符号：%s —— 切换链未移除干净，装机后'
                 '仍可能调 setAlternateIconName' % leaked)
    else:
        ck.fail('C12', '取不到 dylib，无法核验图标符号清零')

    # 安装/卸载钩子**存在性**：C9 只查 mode，文件整个缺失时静默通过。
    for hook in ('postinst', 'prerm'):
        has = any(n.startswith('./' + hook) for n in ctl_names)
        ck.check(has, 'C12',
                 'control.tar 有 %s' % hook,
                 'control.tar 缺 %s —— 旧版注入残留的清理钩子不存在，'
                 '从 v1.6.6-v1.6.9.2 升上来的设备还原不掉' % hook)

    # ---------------- C13: 装期脚本不许 fork 外部命令做逐 app 扫描 ---------
    # v1.6.10 起 postinst / prerm 要「遍历全部已装 App、找宿主任一条」。
    # 初版是逐 app `grep -aq` —— 装 100+ 个 App 时 fork 上百次外部进程，
    # Sileo/Zebra 的装后脚本又是前台同步执行，用户看到的就是「安装卡住」
    # （本机仿真树：fork 版净耗时 43 秒量级 vs 零 fork 版 <0.1 秒）。
    # v1.6.13 改为 shell 内建 while-read + case。这里把教训固化成判据：
    #   (1) 钩子里不得出现 fork 外部命令做子串匹配（grep / egrep / fgrep）；
    #   (2) 必须真的在扫（while-read 形态在）—— 否则有人会把整段删掉来
    #       「通过闸门」，清理任务本身就被牺牲了，那种绿比红更坏。
    print('\n[C13] 装期脚本零 fork 扫描')
    import re as _re
    for hook in ('postinst', 'prerm'):
        ti = ctl_by_name.get('./' + hook)
        if ti is None:
            ti = ctl_by_name.get(hook)
        if ti is None:
            ck.check(False, 'C13', '%s 存在' % hook, '%s 不存在，无法核验' % hook)
            continue
        text = ctl_tf.extractfile(ti).read().decode('utf-8', 'replace')
        _scan = [ln for ln in text.split('\n')
                 if _re.search(r'(^|[;&|]\s*|\s)(grep|egrep|fgrep)\s+-', ln)]
        ck.check(not _scan, 'C13',
                 '%s 无外部 grep 调用（shell 内建逐行扫）' % hook,
                 '%s 里 fork 了外部 grep：%r —— 每 app fork 一次进程会让'
                 '安装/卸载卡顿（v1.6.13 修「安装慢」改的正是这个），须用'
                 '`while IFS= read -r l || [ -n "$l" ]` + case' % (hook, _scan[:3]))
        ck.check('while IFS= read -r' in text, 'C13',
                 '%s 仍有逐行扫描逻辑（任务没被删）' % hook,
                 '%s 里找不到 `while IFS= read -r` —— 别把清理任务整个删掉'
                 '来规避 grep，旧版残留会还原不掉；改成内建逐行扫' % hook)

    return finish(ck)


def finish(ck):
    print('\n' + '=' * 68)
    if ck.fails:
        print('✗ 校验失败：%d 项通过，%d 项失败' % (len(ck.passes), len(ck.fails)))
        for tag, msg in ck.fails:
            # CI annotation：必须逐条 ::error::，否则不展开日志看不到
            print('::error::[%s] %s' % (tag, msg))
        return 1
    print('✓ 校验全部通过（%d 项）' % len(ck.passes))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))