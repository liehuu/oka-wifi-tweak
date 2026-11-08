#!/bin/sh
# 零 Theos 构建：xcrun clang 编 dylib → codesign 自签 → mkdeb.sh 拼 deb
#
# 为什么不用 Theos：
#   实测 Setup Theos（clone 主仓库 + 全部 submodule + 下载 SDK）要 680s，
#   而本工程没有任何 %hook / Logos 语法，全是运行时 hook（objc runtime），
#   不需要 logos.pl 预处理，不需要 theos 的 makefile 体系 ——
#   整套 Theos 只为跑一次 dpkg-deb 不值得。零 Theos 全流程约 20 秒。
#
# 用法：sh devkit/build.sh <arm64|arm64e> <arch-name> <version>
#   例：sh devkit/build.sh arm64e iphoneos-arm64e 1.0.0
set -eu

CPU="${1:?用法: build.sh <arm64|arm64e> <arch-name> <version>}"
ARCH_NAME="${2:?}"
VER="${3:?}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/.build"
NAME="OkaWiFi"
mkdir -p "$BUILD"

# 部署目标：arm64e 从 14.0 起，arm64 从 13.0 起。
# 用户是 iOS 17 arm64e 设备；保持较低部署目标不影响高版本运行，
# 反而让同一个产物覆盖更多机型。
case "$CPU" in
  arm64)  TRIPLE="arm64-apple-ios13.0"  ;;
  arm64e) TRIPLE="arm64e-apple-ios14.0" ;;
  *) echo "未知架构: $CPU"; exit 1 ;;
esac

SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
echo "SDK   : $SDK"
echo "TRIPLE: $TRIPLE"

# ---------- 1) 编译 ----------
# -undefined dynamic_lookup：MSHookFunction / _swift_unknownObjectWeakLoadStrong
#   这些符号在本 dylib 里是未定义的，运行时由宿主进程（Blinker）解析。
#   不加这个会报 "undefined symbols for architecture"。
#
# ★ Tweak.xm 是 Objective-C++（.xm 后缀），但本文件没有任何 C++ 代码，
#   用 clang -x objective-c 强制按 ObjC 编译即可，省掉 -lstdc++ 与
#   C++ 运行时的依赖。
#
# ★ install_name 用 @loader_path/.jbroot/...：
#   RootHide / rootless 下 dylib 装在 jbroot 里，这个写法两个 scheme 都适用。
#
# ★★ -I devkit/include 是必需的：
#   Tweak.xm 里 #import <substrate.h>，而这个头原本由 Theos 的
#   $THEOS/vendor/include/substrate.h 提供。零 Theos 构建时该路径不存在，
#   编译直接 fatal error: 'substrate.h' file not found。
#   仓库自带 devkit/include/substrate.h（只声明 MSHookFunction），用 -I 顶上去。
INC="$ROOT/devkit/include"
[ -f "$INC/substrate.h" ] || {
  echo "✗ 缺 $INC/substrate.h —— 零 Theos 构建没有 Theos 的 vendor/include，必须自带"
  exit 1
}

# ---------- 0) 网页增强资源内联 ----------
# v1.1.0：不改 App 包内 GCDWebUploader.bundle（会破坏签名），把 web/ 下的
#   JS/CSS 编成 dylib 里的 C 字符串常量，运行时由 Tweak.xm 追加到 epilogue。
# 生成脚本自带往返解码自检；这里再补一条"文件真的落盘且非空"的硬判据。
python3 "$ROOT/devkit/gen-webassets.py" "$ROOT/web" "$BUILD/OkaWebAssets.h" \
  || { echo "✗ 前端资源内联失败，中止"; exit 1; }
[ -s "$BUILD/OkaWebAssets.h" ] \
  || { echo "✗ OkaWebAssets.h 未生成或为空，中止"; exit 1; }

# ★ 抑制项与 legacy/Makefile 保持一致。
#   重写 build.sh 时漏掉 -Wno-incompatible-pointer-types 与
#   -Wno-objc-method-access，是这次 CI 编译失败的直接怀疑对象：
#     · (void **)&orig_xxx 这类「对函数指针取二级指针」
#     · 向 UIViewController* 发 respondsToSelector: 时用变量 SEL
#   代码侧已尽量从根源改（HookInstanceMethod 的 outOrig 改成 void **），
#   但运行时 hook 代码天然绕不开强转，抑制项保留。
xcrun clang -target "$TRIPLE" -isysroot "$SDK" \
  -I "$INC" -I "$BUILD" \
  -x objective-c \
  -dynamiclib -fobjc-arc -fobjc-exceptions -Os -Wall \
  -Wno-unused-parameter -Wno-unused-function -Wno-deprecated-declarations \
  -Wno-nullability-completeness -Wno-unguarded-availability-new \
  -Wno-incompatible-pointer-types -Wno-objc-method-access \
  -Wno-arc-performSelector-leaks -Wno-format \
  -undefined dynamic_lookup \
  -framework Foundation -framework UIKit \
  -framework AVFoundation -framework SystemConfiguration \
  -framework CoreFoundation \
  "$ROOT/Tweak.xm" \
  -o "$BUILD/$NAME.dylib" \
  -Xlinker -install_name -Xlinker \
    "@loader_path/.jbroot/Library/MobileSubstrate/DynamicLibraries/$NAME.dylib"

# ---------- 2) 签名 ----------
# ellekit / RootHide 会跳过没有 LC_CODE_SIGNATURE 的 dylib，
# 表现是「装了完全没反应」，且系统不报任何错。
codesign --force --sign - "$BUILD/$NAME.dylib"

# ---------- 3) 架构硬闸门 ----------
echo "--- dylib ---"
# ★ 这两条**只**是诊断输出，不是判据，必须容错：
#   set -e 下，命令不存在（例如 Windows Git Bash 没有 file）会让整个
#   构建在这一行直接退出，而它本来根本不该有否决权。
#   真判据在下面的 verify-deb.py --check-macho。
command -v file >/dev/null 2>&1 && file "$BUILD/$NAME.dylib" || true
xcrun lipo -info "$BUILD/$NAME.dylib" 2>/dev/null || true

# ★★ 不能用 `lipo -info | grep -q "$CPU"`：
#   arm64e 的 lipo 输出是 "Non-fat file: x is architecture: arm64e"，
#   字符串里**包含** "arm64" 子串 → grep -q arm64 直接通过。
#   于是「arm64e 变体被误编成 arm64」这个最致命的错误查不出来
#   （症状：roothide 包装上去了，dylib 却因 cpusubtype 不匹配不被加载，
#    且系统不报任何错）。
#
# 优先用 lipo -archs：它只输出架构名，无需解析句子。
# 老版本 cctools 没有 -archs，退化为解析 lipo -info（取最后一个词）。
# ★ 两个变量都要在 if 之前初始化：set -u 下引用未定义变量会直接退出，
#   而这条路径只在旧工具链上才走到，平时测不出来。
#
# ★★ lipo 的输出只用来**记录**，不再作为判据。
#   真正的判据交给 devkit/verify-deb.py 里的 read_macho()：
#   直接读 Mach-O 头的 cputype/cpusubtype 做整数比较。
#   原因有两个：
#     1) lipo -info 输出是句子，`grep -q arm64` 对 arm64e 必然假通过
#        （"arm64e" 包含 "arm64"）—— 这个假闸门本工程已经栽过一次；
#     2) lipo 只在 macOS 有，本地无法复现，判据等于只在 CI 生效。
#   多个架构时 lipo -archs 可能输出 "arm64 arm64e" 也可能 "arm64,arm64e"，
#   tr ' ' '\n' 对付不了逗号，所以连这个解析都不该依赖。
ARCH_LIST=""
if ARCH_LIST="$(xcrun lipo -archs "$BUILD/$NAME.dylib" 2>/dev/null)" && [ -n "$ARCH_LIST" ]; then
  :
else
  ARCH_LIST="$(xcrun lipo -info "$BUILD/$NAME.dylib" | sed 's/.*: *//' || echo '?')"
fi
echo "lipo 报告: [$ARCH_LIST]（仅供参考，判据见下方 verify-deb.py）"

# 权威判据：读 Mach-O 头，cpusubtype 整值相等（arm64=0 / arm64e=2，
# 且 arm64e 的真实值带 capability 高位 0x80000002，查低 24 位）。
# 这一段与 CI 的 Verify deb 步骤用的是**同一份代码**，不会出现
# 「本地过了 CI 挂」或「CI 过了真机不加载」。
python3 "$ROOT/devkit/verify-deb.py" --check-macho "$BUILD/$NAME.dylib" --expect-cpu "$CPU" \
  || { echo "✗ dylib 架构/签名不合预期，中止"; exit 1; }

# 额外核验 cpusubtype：arm64e 的 Mach-O cpusubtype 实测为 0x80000002
# （高字节是 capability 位），otool 只显示 2。仅作记录，不做硬判据 ——
# 不同 Xcode 版本写法有差异，硬判会误伤。
xcrun otool -arch "$CPU" -hv "$BUILD/$NAME.dylib" 2>/dev/null | sed -n '2,3p' || true

# ---------- 4) 功能指纹闸门 ----------
# 这些字符串任何一条丢了，产物表面看不出来，装到机器上就是功能缺失。
# ★ ObjC selector 不进 nm -u（走 _objc_msgSend + __objc_methname），只能 grep -a。
#
# 功能 1：快捷菜单
for sym in UIApplicationShortcutItem application:performActionForShortcutItem:completionHandler: \
           com.oka.wifi.start; do
  grep -aq "$sym" "$BUILD/$NAME.dylib" \
    || { echo "✗ dylib 缺 ${sym}（长按菜单会失效）"; exit 1; }
done

# 功能 2a：蜂窝解禁
for sym in SCNetworkReachabilityGetFlags reachabilityChangedWithNote: \
           changeWifiTransferStateAction: setHidden:; do
  grep -aq "$sym" "$BUILD/$NAME.dylib" \
    || { echo "✗ dylib 缺 ${sym}（蜂窝解禁会失效）"; exit 1; }
done

# 功能 2b：IP 修正
for sym in _GCDWebServerGetPrimaryIPAddress getifaddrs inet_ntop bridge pdp_ip; do
  grep -aq "$sym" "$BUILD/$NAME.dylib" \
    || { echo "✗ dylib 缺 ${sym}（热点地址修正会失效）"; exit 1; }
done

# 后台保活
for sym in AVAudioPlayer AVAudioSession setNumberOfLoops: setVolume:; do
  grep -aq "$sym" "$BUILD/$NAME.dylib" \
    || { echo "✗ dylib 缺 ${sym}（后台保活会失效）"; exit 1; }
done

# Swift weak 读取（outlet 全靠它，缺了就是 outlet 全 nil）
# ★ 这个串只在 dlsym 的实参里出现一次。若哪天改成 RTLD_NEXT 或换成符号常量，
#   字符串可能不再进 __cstring → 闸门报"缺符号"，看着像编译坏了，其实是改了实现。
#   报错时提示先去看 dlsym 那一行。
grep -aq "_swift_unknownObjectWeakLoadStrong" "$BUILD/$NAME.dylib" \
  || { echo "✗ dylib 缺 _swift_unknownObjectWeakLoadStrong（outlet 读取会失效）"
       echo "  排查：Tweak.xm 里 dlsym(RTLD_DEFAULT, \"_swift_unknownObjectWeakLoadStrong\") 是否还以字符串字面量形式存在"
       exit 1; }

# 功能 3（v1.1.7）：网页增强注入层
# 前 4 条是 ObjC 侧：类方法 hook 的 selector、幂等标记、类名常量。
# 其余来自内联的 kOkaWebJS / kOkaWebCSS —— 丢了说明 gen-webassets.py 的
# 产物没被编进来，页面会照常打开但增强功能全部不存在，属于"静默功能缺失"。
# v1.1.1 的交互是「全页面拖放 + 复用原版 #move-modal」；
# v1.1.2 再加两条：sortListingInPlace（列表排序）与 oka-compact（顶部紧凑）。
# v1.1.3 移除诊断日志（见下方负向闸门）；
# v1.1.4 修选中残留：resetSelectionOnReload（列表重建即清空）+ liveSelectedItems（剔除幽灵项）。
# v1.1.6 三项网页交互：行末按钮重排（oka-download / oka-rename 都在删除左边）、
#   左侧选中热区（onSelectZoneCapture / oka-select-zone / toggleRowSelection）、
#   目录前端 ZIP 打包（buildZip / collectTree / xhrGet / downloadItem）。
#   v1.1.5 的「点行即下载」已按反馈取消，由下面的负向闸门反查。
# v1.1.7 三项网页交互：点目录行进入（onRowAction）、点文件行在线预览
#   （openPreview / previewKind / fetchPreview / oka-preview-modal）、
#   目录 ZIP 显式写顶层文件夹条目（下方单独的 dirs: [rootRel] 判据）。
# 另一项 v1.1.7：图标列按文件类型换字形（applyRowIcon / ICON_CLASS / iconKind），
#   原本所有文件都是同一个下载箭头，与每行下载按钮撞车。
for sym in responseWithHTMLTemplate:variables: initWithHTMLTemplate:variables: \
           OKA-WIFI-INJECTED GCDWebServerDataResponse \
           oka-list-tools onMoveButtonCapture HTML_MOVE_ENHANCE move-confirm \
           oka-drag-overlay webkitGetAsEntry dirAlreadyExists \
           sortListingInPlace isListingArray isDirEntry origAjax oka-compact \
           resetSelectionOnReload liveSelectedItems hidden.bs.modal \
           decorateRows startRename downloadItem oka-dir-row oka-file-row \
           oka-name oka-rename oka-rename-input glyphicon-pencil \
           oka-download oka-download-cell oka-select-zone onSelectZoneCapture \
           toggleRowSelection buildZip collectTree xhrGet application/zip \
           onRowAction openPreview previewKind fetchPreview renderPreviewBody \
           clearPreviewURL HTML_PREVIEW oka-preview-modal oka-preview-body \
           oka-preview-text oka-preview-download \
           applyRowIcon fileIconClass iconKind ICON_CLASS ICON_EXT \
           glyphicon-picture glyphicon-film glyphicon-music \
           glyphicon-compressed oka-preview-unsupported listDir \
           oka-raw fetchBytes rawURL rawUsable saveBytesAs mimeOf \
           navigateDownload DOWNLOAD_BLOB_MAX; do
  grep -aqF "$sym" "$BUILD/$NAME.dylib" \
    || { echo "✗ dylib 缺 ${sym}（网页增强会静默失效）"; exit 1; }
done
echo "✓ v1.1.8 网页交互指纹齐全（点目录进入 / 点文件预览 / 目录 ZIP 保留顶层文件夹 / 按类型换图标 / 内联读取 / Blob 下载）"

# v1.1.7：按类型换图标必须真的挂在列表装饰流程里。
# 只检查 ICON_CLASS 存在不够 —— 定义了却没人调用，图标照样全是下载箭头。
# 这里要求 decorateRows 内部有 applyRowIcon(...) 调用，且图标表确有图片/视频/音频类。
if ! grep -aqF "applyRowIcon(tr, String(d.name || baseName(d.path)), isDir);" \
        "$BUILD/$NAME.dylib"; then
  echo "✗ decorateRows 未按文件类型换图标（图标列会退回统一下载箭头）"
  exit 1
fi
echo "✓ 图标列按文件类型换字形已挂在列表装饰流程里"

# ---------- 4a) v1.1.8 内联读取接口闸门 ----------
# 用户实测三个问题（下载被 Chrome 拦 / PDF 预览失败 / 目录下载弹一堆 IDM 窗）
# 共同根因：/download 的响应固定带 Content-Disposition: attachment。
# 解药是新增 GET /oka-raw，用 isAttachment:NO 返回同一份文件。
#
# 这一层必须查「成对结构」，不能只查符号存在：
#   · responseWithFile:isAttachment: 在 /download 的实现里也有（传的是 YES），
#     只查它存在等于没查；
#   · 必须同时确认 initWithUploadDirectory: 被 hook 了，否则 handler 永远
#     不会注册，前端静默回退，症状与修复前一模一样。
for sym in oka-raw responseWithFile:isAttachment: initWithUploadDirectory: \
           uploadDirectory stringByStandardizingPath responseWithClientError:message:; do
  grep -aqF "$sym" "$BUILD/$NAME.dylib" \
    || { echo "✗ dylib 缺 ${sym}（/oka-raw 内联接口不完整）"; exit 1; }
done

# ★ 关键判据：isAttachment 必须传 NO。
#   源码里 OkaRawFileResponse 末尾是 `, abs, NO);`。编译成 dylib 后
#   字符串常量不会保留实参，所以这里改从源码侧做结构确认（见下面
#   devkit 的源码检查），dylib 侧只确认这条 selector 存在。
# ★ 这条判据只能在**源码**里查，不能在 dylib 里查：
#   · 编进 Mach-O 后 `@"..."` 的 `@` 不保留，只剩 `/oka-raw`；
#   · 而前端 JS 的注释里也写着 `/oka-raw`（JS 会被编进同一个 dylib），
#     所以 `grep -aqF "/oka-raw" dylib` 即使服务端 handler 被整个删掉
#     也照样命中 —— 那就成了永远通过的装饰。
#   源码里 `@"/oka-raw"` 这个形式只出现在真正的 handler 注册行，
#   注释用的是 `/oka-raw`（无 @），所以能如实区分。
if ! grep -qF '@"/oka-raw"' "$ROOT/Tweak.xm"; then
  echo "✗ Tweak.xm 里没有 handler 注册用的 @\"/oka-raw\" 字面量"
  exit 1
fi
# dylib 侧只确认「注册调用确实编进去了」—— 这条与上面成对：
# 有 selector + 有 route 字面量，才算 handler 真的存在。
if ! grep -aqF "addHandlerForMethod:path:requestClass:processBlock:" "$BUILD/$NAME.dylib"; then
  echo "✗ dylib 里没有 addHandlerForMethod（/oka-raw 注册调用未编入）"
  exit 1
fi
echo "✓ v1.1.8 内联读取接口 /oka-raw 已编入产物"

# 源码侧：isAttachment 必须显式传 NO，且不能出现 YES 的误用。
# 写成「取反 if + exit 1」的真闸门，不用 || true 短路。
#
# ★ 判据要能失败：`OkaRawFileResponse` 里那一行是
#      return ((OkaFileResp_t)(void *)objc_msgSend)((id)fileResp, sel, abs, NO);
#   匹配 `, abs, NO);` 是精确到实参的，把它改成 YES 这一条一定 FAIL。
#   不做 hasPrefix 之类的宽松匹配，也不用 `in` 判子串。
if ! grep -qF ', abs, NO);' "$ROOT/Tweak.xm"; then
  echo "✗ OkaRawFileResponse 未用 isAttachment:NO（响应仍会是 attachment）"
  exit 1
fi

# 同一函数的 selector 必须取到 responseWithFile:isAttachment:。
# 这条与上一条是「成对结构」：只查 selector 存在，把 NO 改成 YES 仍能通过；
# 只查 NO 存在，selector 被换成 responseWithFile: 也仍能通过（那会走默认
# isAttachment:YES）。两条都查才封住。
if ! grep -qF 'NSSelectorFromString(@"responseWithFile:isAttachment:")' \
        "$ROOT/Tweak.xm"; then
  echo "✗ 未按 selector 名调用 responseWithFile:isAttachment:"
  exit 1
fi

# 负向：绝不能出现 isAttachment:YES 的新增用法。
# 原版 /download 的实现不在我们代码里，所以源码里不该有 YES。
if grep -qF ', abs, YES);' "$ROOT/Tweak.xm"; then
  echo "✗ 源码出现 isAttachment:YES（会让响应变回 attachment）"
  exit 1
fi
echo "✓ 内联接口用 isAttachment:NO 返回（Content-Disposition: inline）"

# v1.1.8 前端：下载必须走 Blob，不能再用 window.location 做导航式下载。
# 用户实测「点下载要等半天，还报已阻止不安全的下载」的根因就是它：
# 导航式下载在明文局域网源上会被 Chrome 安全策略拦截，且要读完才判定类型。
# 判据绑成对结构：saveBytesAs 存在 + a.click() 拖着 blob URL 一起存在。
# 少任何一个都说明下载链路退回旧写法。
if ! grep -qF 'function saveBytesAs(name, bytes, mime)' "$ROOT/web/oka-wifi.js"; then
  echo "✗ 缺 saveBytesAs（下载没有走 Blob）"
  exit 1
fi
if ! grep -qF "a.download = name;" "$ROOT/web/oka-wifi.js"; then
  echo "✗ 缺 a.download（Blob 下载未设置文件名）"
  exit 1
fi
# 负向：window.location = 'download?...' 这种导航式下载只允许出现在
# navigateDownload() 这个明确的兜底函数里，不能出现在 downloadItem 主体。
# 用 awk 截出 downloadItem 函数体再查，避免整文件匹配把兜底函数也扫进来。
if awk '/^  function downloadItem\(d\) \{/,/^  \}$/' "$ROOT/web/oka-wifi.js" \
     | grep -qF "window.location"; then
  echo "✗ downloadItem 里仍有 window.location（会触发不安全下载拦截）"
  exit 1
fi
echo "✓ 下载走 Blob（saveBytesAs + a.download），导航式下载仅作兜底"

# 负向：v1.1.5 的「点行即下载」入口必须彻底消失。用户明确要求取消；
# 旧实现一旦回流，点名称又会触发下载，左侧热区也会被行级 handler 干扰。
if grep -aqF "openRow" "$BUILD/$NAME.dylib"; then
  echo "✗ dylib 仍有 openRow（v1.1.5 点行下载未取消）"
  exit 1
fi
echo "✓ 无 v1.1.5 点行下载残留"

# v1.1.7：目录 ZIP 必须把顶层文件夹本身写成一条显式 0 字节目录条目。
# v1.1.6 只把 "Docs/" 当文件名前缀，ZIP 里没有 "Docs/" 条目，部分解压工具
# 不创建隐式目录，用户解压出来是一堆散装文件而不是一个文件夹。
grep -aqF "dirs: [rootRel]" "$BUILD/$NAME.dylib" \
  || { echo "✗ dylib 缺顶层目录条目（目录 ZIP 解压会得到散装文件）"; exit 1; }
echo "✓ 目录 ZIP 显式写入顶层文件夹条目"

# 负向闸门：v1.1.0 的独立拖放框 / 独立批量移动弹窗必须从产物里彻底消失。
# 用户明确反馈过这两样占版面、另起一套入口不合理，不能让旧代码回流。
for stale in oka-dropzone oka-move-modal oka-move-open oka-batch-bar; do
  if grep -aqF "$stale" "$BUILD/$NAME.dylib"; then
    echo "✗ dylib 仍有 v1.1.0 残留 ${stale}（独立拖放框 / 独立移动弹窗未清理）"
    exit 1
  fi
done
echo "✓ 无 v1.1.0 独立拖放框 / 独立移动弹窗残留"

# ---------- 4b) 诊断日志负向闸门（v1.1.3） ----------
# 使用者不需要诊断日志：源码里不能有 LOG( / NSLog，编译产物里不能有
# "[OkaWiFi]" 日志前缀，也不能再引用 _NSLog 未定义符号。
# 光看源码"没写 NSLog"不算数 —— 必须从 dylib 原始字节反查，防止宏回流。
if grep -nE '(^|[^A-Za-z_])LOG\(' "$ROOT/Tweak.xm" || grep -n 'NSLog' "$ROOT/Tweak.xm"; then
  echo "✗ Tweak.xm 仍有诊断日志调用（v1.1.3 起已移除）"
  exit 1
fi
for stale in '[OkaWiFi]' NSLog; do
  if grep -aqF "$stale" "$BUILD/$NAME.dylib"; then
    echo "✗ dylib 仍有诊断日志残留 ${stale}"
    exit 1
  fi
done
echo "✓ 无诊断日志残留（源码 + 产物双向）"

# 目标 App 的过滤条件（写在 plist 里，但 dylib 里也应有类名常量）
for sym in Blinker BNHttpServerPage; do
  grep -aq "$sym" "$BUILD/$NAME.dylib" \
    || { echo "✗ dylib 缺 ${sym}"; exit 1; }
done
echo "✓ 功能指纹齐全"

# ---------- 5) 崩溃防护 ----------
# 必须存在 __gcc_except_tab（@try 的 LSDA 落点），否则异常会漏出线程。
# ★ 段名别记错：不存在叫 __objc_exception 的段。
if xcrun otool -l "$BUILD/$NAME.dylib" | grep -q '__gcc_except_tab'; then
  echo "✓ 存在 __gcc_except_tab 段（@try 的 LSDA 落点）"
else
  echo "✗ 缺 __gcc_except_tab：@try/@catch 可能被优化掉，异常会漏出线程"
  exit 1
fi

# ★★ __objc_personality 是**未定义符号**，不是段 —— 别用 otool -l 找它。
if xcrun nm -u "$BUILD/$NAME.dylib" 2>/dev/null | grep -q '__objc_personality'; then
  echo "✓ 引用了 __objc_personality（ObjC 异常运行时挂钩已就位）"
else
  echo "✗ 缺 __objc_personality：@try/@catch 不会生效"
  exit 1
fi

# ---------- 6) 打包 ----------
sh "$ROOT/devkit/mkdeb.sh" "$ARCH_NAME" "$VER" "$CPU"