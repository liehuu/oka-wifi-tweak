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
import struct
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.join(HERE, 'verify-deb.py')
PY = sys.executable

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
LC_CODE_SIGNATURE = 0x1D
LC_SEGMENT_64 = 0x19


def make_macho(cputype=CPU_TYPE_ARM64, cpusubtype=0, with_sig=True, fat=False):
    """构造最小 Mach-O 64 位可执行体（只有头 + 1~2 个 load command）。

    cpusubtype 可以带 capability 高位（arm64e 实测 0x80000002），
    这里原样写入，用于验证 verify-deb.py 是否正确地做 & 0x00FFFFFF 掩码。
    """
    cmds = []
    # 一个 LC_SEGMENT_64 占位（真实 dylib 必有，解析器要能正确跳过它）
    seg = struct.pack('<II16sQQQQiiII', LC_SEGMENT_64, 72, b'__TEXT',
                      0, 0x1000, 0, 0x1000, 5, 5, 0, 0)
    cmds.append(seg)
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
    if fat:
        # 简单包一层 fat 头：magic + nfat_arch + 一个 arch 描述
        nfat = 1
        fat_hdr = struct.pack('>II', 0xCAFEBABE, nfat)
        arch = struct.pack('>iiIII', CPU_TYPE_ARM64, 0, 4096, len(blob), 12)
        blob = fat_hdr + arch + blob
    return blob


def make_control_tar(arch='iphoneos-arm', postinst_mode=0o755, with_postinst=True):
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
        pi = b'#!/bin/sh\nexit 0\n'
        ti = tarfile.TarInfo('./postinst')
        ti.size = len(pi)
        ti.mode = postinst_mode
        ti.mtime = 1700000000
        tf.addfile(ti, io.BytesIO(pi))
    tf.close()
    return buf.getvalue()


def make_data_tar(dylib_blob, prefix='', plist_mode=0o644, dylib_mode=0o755,
                  extra_members=(), deiban_member=False, pax=False):
    """构造 data.tar。

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

    parts = ([prefix] if prefix else []) + [
        'Library', 'MobileSubstrate', 'DynamicLibraries']
    path = './' + '/'.join(parts)
    for i in range(1, len(parts) + 1):
        d = './' + '/'.join(parts[:i])
        ti = tarfile.TarInfo(d)
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o755
        ti.mtime = mtime
        tf.addfile(ti)

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
    ctl = make_control_tar(arch=arch,
                           postinst_mode=kw.pop('postinst_mode', 0o755),
                           with_postinst=kw.pop('with_postinst', True))
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
    CASES.append((name, should_pass, deb_bytes, scheme, arch, cpu, want_in_output))


def build_cases():
    ok_arm64 = make_macho(CPU_TYPE_ARM64, 0, with_sig=True)
    # arm64e 的 cpusubtype 真实值带 capability 高位
    ok_arm64e = make_macho(CPU_TYPE_ARM64, 0x80000002, with_sig=True)
    no_sig = make_macho(CPU_TYPE_ARM64, 0, with_sig=False)
    fat = make_macho(CPU_TYPE_ARM64, 0, with_sig=True, fat=True)

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
        if ok and want and (not should_pass) and want not in out:
            # 失败方向还要确认是**这一条**判据拦住的，不是别处先失败
            ok = False
            reason = '期望输出含 %s，但没有（可能被别的检查先拦住）' % want
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