#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  build.sh —— 一键编译打包（需 macOS + Theos）
#
#  用法：
#    ./build.sh              # rootless（默认，iOS 15+/Dopamine 等）
#    ./build.sh roothide     # roothide（RootHide 越狱，/var/jb 不存在）
#    ./build.sh rootful      # 传统 rootful（/Library/MobileSubstrate）
#
#  产物：packages/com.oka.wifitweak_<version>_<arch>.deb
# ---------------------------------------------------------------------------
set -e

THEOS="${THEOS:-$HOME/theos}"
SCHEME="${1:-rootless}"

if [ ! -d "$THEOS" ]; then
  echo "!! 找不到 Theos：$THEOS"
  echo "   请先安装：https://theos.dev/docs/installation"
  echo "   或设置环境变量 THEOS=/path/to/theos"
  exit 1
fi
export THEOS
export PATH="$THEOS/bin:$PATH"

echo "==> Theos : $THEOS"
echo "==> Scheme: $SCHEME"

# 清理
make clean >/dev/null 2>&1 || true

# 编译 + 打包
if [ "$SCHEME" = "rootful" ]; then
  make package FINALPACKAGE=1
else
  make package SCHEME="$SCHEME" FINALPACKAGE=1
fi

echo
echo "==> 产物："
ls -lh packages/*.deb 2>/dev/null || echo "   （没找到 deb，请检查上面的报错）"
echo
echo "==> 安装到已连接设备（需 openssh + 已配置 ~/.ssh/config 或 THEOS_DEVICE_IP）"
echo "    make install SCHEME=$SCHEME"
echo "    或：dpkg -i 手动装 + killall -9 Blinker"