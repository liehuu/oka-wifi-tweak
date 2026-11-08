#!/bin/sh
# 打 deb 包（不依赖 Theos / dpkg-deb）
#
# deb 的 ar 归档格式非常简单，Python 标准库即可生成。
# 用法：sh devkit/mkdeb.sh <arch> <version> <cpu>
#   arch    : iphoneos-arm | iphoneos-arm64 | iphoneos-arm64e
#   version : 1.1.6（与 .github/workflows/build.yml 的 env.VERSION 保持一致）
#   cpu     : arm64 | arm64e（仅用于产物命名，不参与打包逻辑）
set -eu

ARCH="${1:?用法: mkdeb.sh <arch> <version> <cpu>}"
VER="${2:?}"
CPU="${3:?}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/.build/stage-$CPU"
OUT="$ROOT/.build/out"
PKG="com.oka.wifitweak"
NAME="OkaWiFi"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$OUT"

# ---- 载荷 ----
# 三个 scheme 的安装路径不同，由 Theos 的 THEOS_PACKAGE_INSTALL_PREFIX 决定：
#   rootful  : /Library/MobileSubstrate/DynamicLibraries
#   rootless : /var/jb/Library/MobileSubstrate/DynamicLibraries
#   roothide : /Library/MobileSubstrate/DynamicLibraries（在 jbroot 内，与 rootful 同结构）
# 所以只有 rootless 需要在 stage 里多套一层 var/jb。
#
# ★★ 这里**只能**建 DEST，不能先 mkdir 一个 $STAGE/Library 占位：
#   之前无条件 mkdir "$STAGE/Library/MobileSubstrate/DynamicLibraries"，
#   rootless 变体的 data.tar 里就同时出现了空的 ./Library/... 和
#   ./var/jb/Library/... —— 实测已复现。
#   后果：rootless 设备上 / 是只读的，dpkg 解 ./Library/... 直接失败，
#   整个包装不上去（而 rootful 设备上则会往根目录留一堆空目录，卸载不清）。
#   空目录也是 tar 成员，不会因为"里面没文件"就自动消失。
PREFIX=""
case "$ARCH" in
  iphoneos-arm64) PREFIX="var/jb" ;;   # rootless
  *)              PREFIX=""       ;;   # rootful / roothide
esac
DEST="$STAGE${PREFIX:+/$PREFIX}/Library/MobileSubstrate/DynamicLibraries"
mkdir -p "$DEST"

cp "$ROOT/.build/$NAME.dylib" "$DEST/$NAME.dylib"
cp "$ROOT/$NAME.plist"        "$DEST/$NAME.plist"

# 权限：目录 755，文件 644
# ★ dylib 必须 755：644 会被 ellekit / RootHide 的加载器拒绝，
#   表现是「装了完全没反应」，dpkg 却不报任何错。
find "$STAGE" -type d -exec chmod 755 {} \;
find "$STAGE" -type f -exec chmod 644 {} \;
chmod 755 "$DEST/$NAME.dylib"

# ---- control ----
# 版本号必须由参数派生，不能硬编码（否则会出现「包名 v1.0、内容 v1.1」）。
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Name: Oka WiFi 传输增强
Version: $VER
Architecture: $ARCH
Description: 为 Oka Files (Blinker) 增加桌面长按快捷开启 WiFi 传输，并解除「仅 WiFi 可开启」的限制，同时修正个人热点下的显示地址。
Depends: mobilesubstrate
Maintainer: oka
Author: oka
Section: Tweaks
EOF
chmod 644 "$STAGE/DEBIAN/control"

# ---- postinst ----
# 装完只打印提示，**绝不 respring / sbreload / killall SpringBoard**。
#
# ★★ 这里踩过一次真实事故（另一个项目 1.1-1，用户报「dpkg 被中断，无法安装 deb」）：
#   postinst 是 dpkg 事务的**最后一步**。在 Sileo/Zebra 里安装时，
#   postinst 里跑 sbreload 会重启 SpringBoard，而 Sileo 是前台 App ——
#   一起被系统终止。此时 dpkg 尚未收尾（写 status、释放 lock），
#   事务被打断 → 锁残留 + 半装 → 之后**任何** deb 都装不上。
#   `|| true` 只能兜住「返回码非 0」，兜不住「进程被杀 / 事务被切断」。
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
# 只输出提示，不做任何会影响宿主进程的动作。
set -e
printf '\n'
printf 'Oka WiFi 传输增强 已安装。\n'
printf '生效方式：请手动重启桌面（respring），或者直接把 App 从后台划掉再重开。\n'
printf '  · Sileo/Zebra 装完后点「Restart SpringBoard」\n'
printf '\n'
printf '用法：长按 Oka Files 图标 → 「开启 WiFi 传输」\n'
printf '  开启后地址会自动复制到剪贴板。\n'
printf '\n'
printf '开关文件（可选，默认全开）：\n'
printf '  /var/mobile/Library/Preferences/com.oka.wifitweak.plist\n'
printf '    EnableShortcut    BOOL  长按菜单\n'
printf '    EnableCellular    BOOL  蜂窝/热点下允许开启\n'
printf '    EnableIPFix       BOOL  热点地址修正\n'
printf '    CopyURLOnShortcut BOOL  开启后复制地址\n'
printf '\n'
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

# ---- 打包 ----
DEB="$OUT/${PKG}_${VER}_${ARCH}.deb"
# PREFIX 必须一起 export：Python 侧要按它判断 data.tar 里允不允许 var/jb 前缀。
# 漏了这个，os.environ.get('PREFIX') 恒为空 → rootless 变体会被闸门误判为"混入 var/jb"。
export STAGE OUT DEB PREFIX
python3 - <<'PY'
import io, os, lzma, tarfile, gzip, hashlib, time

stage = os.environ['STAGE']
deb   = os.environ['DEB']

# 固定 mtime（必须是 int）。
# ★ float 秒会让 PAX 格式写扩展头，而 dpkg 的 tar 实现不认 PAX 扩展头。
_MTIME = int(os.environ.get('SOURCE_DATE_EPOCH') or 1700000000)


def _is_exec(rel):
    """哪些文件在 deb 里必须是可执行位。

    SpringBoard 注入的 dylib 如果丢了 +x，装上去表现是「插件完全没加载」，
    而 dpkg 不报任何错 —— 这类问题排查成本极高，必须精确。
    """
    if rel.endswith('.dylib'):
        return True
    if rel.startswith('DEBIAN/') and rel.split('/')[-1] in \
            ('postinst', 'postrm', 'preinst', 'prerm'):
        return True
    return False


def _tar():
    """把 stage 目录打成 tar 字节流。

    ★ 不能 tf.add(目录) 递归加：os.walk 又会单独走到子目录 → 重复打包。
    ★ 不能读 os.stat().st_mode：打包机上的 umask 会污染产物权限。
    ★★ 必须显式 format=GNU_FORMAT：
      Python 3.8+ 的 tarfile.open(mode='w') 默认 PAX_FORMAT。
      PAX 遇「mtime 带小数秒 / uid 太大 / 路径非 ASCII」时会在**每个**成员前
      插一个 typeflag='x' 的扩展头，dpkg 直接报：
        corrupted filesystem tarfile in package archive:
        unsupported PAX tar header type 'x'
      一律 GNU_FORMAT，mtime 固定成整数。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w', format=tarfile.GNU_FORMAT) as tf:
        for root, dirs, files in os.walk(stage):
            # ★★ DEBIAN/ 绝不能进 data.tar：
            #   dpkg 只从 control.tar 读控制文件。data.tar 里若也带着
            #   ./DEBIAN/postinst，解包时会被落到文件系统根目录的
            #   /DEBIAN/postinst ——
            #     · rootful 上：往根目录塞垃圾文件，卸载不会清掉
            #     · rootless 上：/ 是只读的 → 解包直接失败、整个包装不上
            rel_root = os.path.relpath(root, stage).replace('\\', '/')
            if rel_root == 'DEBIAN' or rel_root.startswith('DEBIAN/'):
                dirs[:] = []
                continue
            if rel_root == '.':
                dirs[:] = [d for d in dirs if d != 'DEBIAN']
            dirs.sort(); files.sort()
            for name in dirs:
                full = os.path.join(root, name)
                ti = tf.gettarinfo(full, arcname='./' + os.path.relpath(full, stage))
                ti.mode = 0o755
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = 'root'
                ti.mtime = _MTIME
                tf.addfile(ti)
            for name in files:
                full = os.path.join(root, name)
                ti = tf.gettarinfo(full, arcname='./' + os.path.relpath(full, stage))
                rel = os.path.relpath(full, stage).replace('\\', '/')
                ti.mode = 0o755 if _is_exec(rel) else 0o644
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = 'root'
                ti.mtime = _MTIME
                with open(full, 'rb') as fh:
                    tf.addfile(ti, fh)
    return buf.getvalue()


data = lzma.compress(_tar(), format=lzma.FORMAT_XZ)

ctl = io.BytesIO()
with tarfile.open(fileobj=ctl, mode='w', format=tarfile.GNU_FORMAT) as tf:
    for name in ('control', 'postinst'):
        p = os.path.join(stage, 'DEBIAN', name)
        if os.path.exists(p):
            ti = tf.gettarinfo(p, arcname='./' + name)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            ti.mode = 0o755 if name in ('postinst', 'prerm', 'preinst', 'postrm') else 0o644
            ti.mtime = _MTIME
            with open(p, 'rb') as fh:
                tf.addfile(ti, fh)
control = gzip.compress(ctl.getvalue(), mtime=0)

members = [
    ('debian-binary', b'2.0\n'),
    ('control.tar.gz', control),
    ('data.tar.xz', data),
]

out = io.BytesIO()
out.write(b'!<arch>\n')
for name, blob in members:
    hdr = ('%-16s' % name).encode()
    hdr += ('%-12s' % str(int(time.time()))).encode()
    hdr += ('%-6s' % '0').encode()      # uid
    hdr += ('%-6s' % '0').encode()      # gid
    hdr += ('%-8s' % '100644').encode() # mode
    hdr += ('%-10s' % str(len(blob))).encode()
    hdr += b'`\n'
    out.write(hdr)
    out.write(blob)
    if len(blob) % 2:
        out.write(b'\n')

blob = out.getvalue()

# ---------- 闸门：tar 里不得有任何 PAX 扩展头 ----------
# dpkg 的 tar 实现不认 PAX 扩展头（typeflag 'x' = ASCII 120），
# 一旦出现，装包直接失败。
#
# ★ 必须按 tar 的真实结构走：读一个头(512B) → 按 size 字段跳过数据区
#   （按 512 对齐）→ 再读下一个头。**不能每 512 字节盲扫**：
#   盲扫会在二进制载荷里把恰好等于 'x' 的字节当成 PAX 头，报假警报。
def _check_no_pax_headers(raw, label):
    bad = []
    off = 0
    n = len(raw)
    while off + 512 <= n:
        blk = raw[off:off + 512]
        if blk == b'\x00' * 512:
            break
        tf = blk[156:157]
        size_f = blk[124:136].rstrip(b'\x00 ').decode('ascii', 'replace')
        try:
            size = int(size_f, 8) if size_f else 0
        except ValueError:
            size = 0
        if tf in (b'x', b'g'):
            nm = blk[0:100].rstrip(b'\x00').decode('ascii', 'replace')
            bad.append('%s@%d' % (nm, off))
        off += 512 + ((size + 511) // 512) * 512
    if bad:
        print('  ✗ %s 里有 %d 个 PAX 扩展头（dpkg 不认，装包必失败）:' % (label, len(bad)))
        for b in bad[:6]:
            print('      ', b)
        raise SystemExit(1)


_check_no_pax_headers(lzma.decompress(data), 'data.tar')
_check_no_pax_headers(gzip.decompress(control), 'control.tar')
print('  ✓ tar 无 PAX 扩展头（GNU_FORMAT，dpkg 兼容）')


# ---------- 闸门：data.tar 里不得出现该 scheme 之外的路径前缀 ----------
# 这条闸门专治"多建了一个空目录"这类看不出、装不上的缺陷：
#   · rootless 变体若混进 ./Library/...，rootless 设备上 / 只读 → 解包失败 → 整包装不上
#   · rootful/roothide 变体若混进 ./var/jb/...，会往设备上塞一个无用的 /var/jb 树
#
# ★ 判据要"允许祖先目录"：
#   rootless 的合法成员既包括 ./var/jb/Library/... 下的文件，
#   也包括 ./var 与 ./var/jb 这两个**祖先目录**本身的成员头。
#   若只写 n.startswith('./var/jb/')，祖先目录 ./var、./var/jb 会被误判成非法
#   （实测踩过：闸门把正常包判成坏包，属于假 FAIL，比漏报更能把人带偏）。
def _check_payload_prefix(raw, want_prefix):
    """want_prefix='var/jb' 时，成员必须是 ./var、./var/jb 或 ./var/jb/... 下的路径；
    want_prefix 为空时，成员不得落在 ./var/jb 之下。"""
    tf = tarfile.open(fileobj=io.BytesIO(raw))
    names = tf.getnames()

    if want_prefix:
        # 逐级祖先：var/jb -> ['./var', './var/jb']
        parts = want_prefix.split('/')
        ancestors = {'./' + '/'.join(parts[:i]) for i in range(1, len(parts) + 1)}
        allowed_prefix = './%s/' % want_prefix

        def ok(n):
            return n in ancestors or n.startswith(allowed_prefix)

        bad = [n for n in names if not ok(n)]
        desc = '只含 %s 下的路径（含祖先目录）' % allowed_prefix
    else:
        bad = [n for n in names if n == './var/jb' or n.startswith('./var/jb/')]
        desc = '不含 var/jb 前缀'

    if bad:
        print('  ✗ data.tar 里有 %d 个不该出现的路径（该 scheme 应%s）:'
              % (len(bad), desc))
        for b in bad[:8]:
            print('      ', b)
        raise SystemExit(1)
    print('  ✓ data.tar 共 %d 个成员，路径全部符合该 scheme（%s）' % (len(names), desc))


_check_payload_prefix(lzma.decompress(data), os.environ.get('PREFIX') or '')

open(deb, 'wb').write(blob)
print('  %s  %d B  sha256=%s' % (os.path.basename(deb), len(blob),
                                 hashlib.sha256(blob).hexdigest()[:16]))
PY

echo "  -> $DEB"