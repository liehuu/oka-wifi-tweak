// v1.6.6 桌面动态图标：切换方（Tweak.xm 里的 dylib）与注入方
// （devkit/iconinject.m 编出的 helper）共用的常量。
//
// 为什么抽这一份：两个二进制必须对「alternate icon 叫什么、标记键叫什么、
// 宿主 bundle id 是什么」有同一份认知。任何一边改名而另一边没跟上，
// 症状都是「注入成功但切换失效」或「切换成功但下次重装又注入一次」——
// 这类静默分叉在运行时没有任何报错，只能从源头消除。
//
// 注意：这些是 C 字符串宏。dylib 侧通过
//   [NSString stringWithUTF8String:OKA_ICON_NAME_GREEN]
// 转成 NSString；helper 侧直接当 C 串用（CFStringCreateWithCString /
// 文件路径拼接）。禁止在任一侧硬编码字面量。
#ifndef OKA_ICON_COMMON_H
#define OKA_ICON_COMMON_H

// alternate icon 名 = bundle 里 PNG 的 basename。
// helper 写 <name>@2x.png / <name>@3x.png，dylib 用这个名字切换。
#define OKA_ICON_NAME_GREEN   "OkaIconGreen"

// Info.plist 标记键：helper 成功注入后写入；dylib 冷启动读它判断
// 「动态图标功能是否已启用」。App 更新/重装宿主 App 会丢掉这个键，
// 此时 dylib 静默不切换（桌面保持默认图标 = 语义安全的"关"态），
// 重装本插件 deb 后 postinst 重新注入即恢复。
#define OKA_ICON_KEY_INJECTED "OkaIconInjected"

// 宿主 App bundle id（定位 .app 目录用）。
#define OKA_TARGET_BUNDLE_ID  "com.blinker.documents"

#endif /* OKA_ICON_COMMON_H */