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

xcrun clang -target "$TRIPLE" -isysroot "$SDK" \
  -I "$INC" \
  -x objective-c \
  -dynamiclib -fobjc-arc -fobjc-exceptions -Os -Wall \
  -Wno-unused-parameter -Wno-unused-function -Wno-deprecated-declarations \
  -Wno-nullability-completeness -Wno-unguarded-availability-new \
  -undefined dynamic_lookup \
  -framework Foundation -framework UIKit \
  -framework AVFoundation -framework SystemConfiguration \
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
file "$BUILD/$NAME.dylib"
xcrun lipo -info "$BUILD/$NAME.dylib"

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
ARCH_LIST=""
ARCH_ACTUAL=""
if ARCH_LIST="$(xcrun lipo -archs "$BUILD/$NAME.dylib" 2>/dev/null)" && [ -n "$ARCH_LIST" ]; then
  ARCH_ACTUAL="$(printf '%s' "$ARCH_LIST" | tr ' ' '\n' | tail -1)"
else
  ARCH_LIST="$(xcrun lipo -info "$BUILD/$NAME.dylib" | sed 's/.*: *//')"
  ARCH_ACTUAL="$(printf '%s' "$ARCH_LIST" | tr ' ' '\n' | tail -1)"
fi

echo "ARCH_LIST=[$ARCH_LIST] ARCH_ACTUAL=$ARCH_ACTUAL (expect $CPU)"

NARCH="$(printf '%s' "$ARCH_LIST" | wc -w | tr -d ' ')"
# 单架构产物必须是**恰好一个**架构名，且与预期整词相等。
# 这里不用"包含"判断，避免 arm64 / arm64e 互相冒充。
if [ "$ARCH_ACTUAL" != "$CPU" ] || [ "$NARCH" != "1" ]; then
  echo "✗ 架构不匹配：列表=[$ARCH_LIST] 共 $NARCH 个，末项='$ARCH_ACTUAL'，期望单个 '$CPU'"
  echo "  （arm64e 与 arm64 是不同的 cpusubtype，混了会装了不加载）"
  exit 1
fi
echo "✓ 架构精确匹配（$CPU，单切片）"

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