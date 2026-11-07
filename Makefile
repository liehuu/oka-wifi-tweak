TARGET := iphone:clang:latest:14.0
ARCHS = arm64 arm64e

# 这份代码里大量使用手工类型转换的 objc_msgSend（因为要在不知道完整头文件
# 的情况下调用 Swift 类的私有方法），theos 默认会给所有警告加 -Werror，
# 那样必然一堆 -Wstrict-prototypes / -Wincompatible-pointer-types 直接报错。
# GO_EASY_ON_ME 就是官方给这种情况留的开关：去掉 -Werror，但保留警告输出。
GO_EASY_ON_ME = 1

# ---------------------------------------------------------------------------
#  包体方案（三种越狱，装错位置 = 完全不生效）
#
#    make package SCHEME=rootful    传统 iOS 14 及以下     -> iphoneos-arm
#    make package SCHEME=rootless   Dopamine / palera1n    -> iphoneos-arm64  (/var/jb)
#    make package SCHEME=roothide   RootHide（本机适用）   -> iphoneos-arm64e
#
#  ★ roothide 必须用 roothide/theos 那个仓库来编，标准 theos 的
#    $THEOS/vendor/mod/ 下没有 roothide 模块，会直接报
#    "'roothide' package scheme does not exist"。
# ---------------------------------------------------------------------------
ifneq ($(SCHEME),)
THEOS_PACKAGE_SCHEME = $(SCHEME)
endif

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = OkaWiFi
OkaWiFi_FILES = Tweak.xm
OkaWiFi_CFLAGS = -fobjc-arc -Wno-deprecated-declarations -Wno-unused-function \
                 -Wno-nullability-completeness -Wno-incompatible-pointer-types \
                 -Wno-objc-method-access -Wno-unguarded-availability-new

# 这三个 framework 必须显式链接，理由不是"能不能编译"，而是"运行时类在不在"：
#   - AVFoundation   : 保活用的 AVAudioPlayer / AVAudioSession，App 自己不链接它，
#                      不写进 LC_LOAD_DYLIB 的话进程里根本没加载，NSClassFromString 拿到 nil
#   - SystemConfiguration : 功能 2a 要 hook 的 SCNetworkReachabilityGetFlags 靠
#                      dlsym(RTLD_DEFAULT) 找，符号没加载就 hook 不上
#   - UIKit / Foundation : ObjC 基础
OkaWiFi_FRAMEWORKS = Foundation UIKit AVFoundation SystemConfiguration
OkaWiFi_LIBRARIES = substrate

include $(THEOS_MAKE_PATH)/tweak.mk

after-install::
	install.exec "killall -9 Blinker || true"