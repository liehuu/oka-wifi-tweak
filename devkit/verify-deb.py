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