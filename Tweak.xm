// ============================================================================
//  OkaWiFi.xm  —  Oka Files (Blinker) WiFi 传输增强
//  目标  : com.blinker.documents  1.7.5  (CFBundleVersion 2022012801)
//  设备  : arm64 / arm64e，iOS 14+，roothide 或 rootless 越狱
//
//  功能 1  桌面图标长按 → "开启 WiFi 传输"，点一下直接开启并把地址复制到剪贴板
//  功能 2  蜂窝网络 / 个人热点下也能开启 WiFi 传输，且显示正确的热点 IP
//
// ----------------------------------------------------------------------------
//  逆向依据（全部为 1.7.5 arm64 原版静态分析结果，非猜测）
//
//  [A] 开关逻辑
//      -[BNHttpServerPage changeWifiTransferStateAction:]        IMP 0x100013480
//          └─ wrapper，仅 retain 后 tail-call 真实现                      0x100013a14
//      真实现 0x100013a14：
//          httpServer = self.httpServer            // ivar off 0x08
//          running    = httpServer.isRunning       // vtable 0xe0
//          if (running) { hidden connectionView; qr alpha 0;
//                         startBtn.setTitle(StopWiFiTransfer); httpServer.stop(0xe8) }
//          else         { connectionView.hidden = NO;
//                         startBtn.setTitle(StartWiFiTransfer);
//                         httpServer.start(block)  // vtable 0xd0
//                         if (ok) { ipAddress.setText(httpServer.serverURL(0xd8));
//                                   qrCodeView.image = QR(url); qrCodeView.alpha = 1 } }
//      ★ 全程没有任何 reachability / WWAN 判断。开关本身不受网络类型限制。
//
//  [B] "只有 WiFi 才能开" 的真实来源
//      -[BNHttpServerPage reachabilityChangedWithNote:]          IMP 0x1000132a4
//          └─ 内部闭包                                          0x100012ddc
//              flags = SCNetworkReachabilityGetFlags(...)      0x100012e44
//              tbz   w8, #1,  unavailable                      // Reachable
//              tbnz  w8, #0x12, unavailable                    // bit18 IsWWAN(0x40000) ★
//              cbz   w9, unavailable                           // usable
//              → 可用分支：wifiStatus="Connected"; startBtn.hidden=NO; 生成二维码
//              → 不可用分支 0x1000130c0：wifiStatus="ON/OFF/Waiting";
//                                        connectionView.hidden=YES;
//                                        qrCodeView.alpha=0; startBtn.hidden=YES
//      ★ 结论：蜂窝下限制的只是"开始按钮被隐藏"，用户点不到。服务器能启动。
//
//  [C] 热点下地址为空的真实原因
//      GCDWebServer  _GCDWebServerGetPrimaryIPAddress               0x17ff4
//          getifaddrs(&ifaddr);
//          for (ifa = ...; ifa; ifa = ifa->ifa_next)
//              if (strcmp(ifa->ifa_name, "en0")) continue;          // ★ 唯一条件
//              if (!(ifa->ifa_flags & IFF_UP)) continue;
//              if (sa->sa_family != AF_INET/6) continue;
//              return GCDWebServerStringFromSockAddr(...);
//          return nil;
//      -[GCDWebServer(Extensions) serverURL]                        0xee44
//          ip = _GCDWebServerGetPrimaryIPAddress(0);
//          if (!ip) return nil;                                     // ★ 直接返回 nil
//      热点下接口是 bridge100，蜂窝是 pdp_ip0，都没有 en0 → 返回 nil →
//      serverURL 也是 nil → UI 地址栏空白、二维码内容为 "null"。
// ============================================================================

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <SystemConfiguration/SystemConfiguration.h>
// dispatch_after / dispatch_time / NSEC_PER_SEC 的来源。
// 不能指望 Foundation 间接带进来：Theos 有 prefix.pch 兜底，
// 零 Theos 构建（xcrun clang 单文件直接编）没有，必须显式包含。
#import <dispatch/dispatch.h>
#import <ifaddrs.h>
#import <sys/socket.h>
#import <sys/time.h>      // struct timeval（SO_SNDTIMEO / SO_RCVTIMEO）
#import <netinet/in.h>
#import <arpa/inet.h>
#import <net/if.h>
#import <netdb.h>      // getaddrinfo / getnameinfo（主机名实测）
#import <string.h>
#import <stdio.h>        // snprintf（OkaGWSKey 拼符号名用）
#import <stdlib.h>
#import <unistd.h>
#import <dlfcn.h>
// MSHookFunction 的来源：
//   CydiaSubstrate / libhooker / ElleKit 都导出同名符号。
//   ElleKit 会提供 <substrate.h> 兼容头；若目标环境只有 libhooker，
//   把下面这行换成 <libhooker/libhooker.h>。
#import <substrate.h>
// v1.1.0 网页增强资源（devkit/gen-webassets.py 在编译前生成到 .build/）。
// 纯 ASCII C 字符串常量 kOkaWebJS / kOkaWebCSS，构建脚本用 -I .build 顶上来。
#import "OkaWebAssets.h"

#define SHORTCUT_TYPE  @"com.oka.wifi.start"
// ★ Swift 类在 ObjC 运行时里注册的是 mangled 名（我从 __objc_classlist 的
//   class_ro_t.name 直接读出来的就是 mangled 形式），所以必须用它做 NSClassFromString。
#define PAGE_CLASS_MANGLED  @"_TtC7Blinker16BNHttpServerPage"
#define PAGE_CLASS_SWIFT    @"Blinker.BNHttpServerPage"
#define APPDEL_CLASS_MANGLED @"_TtC7Blinker11AppDelegate"
#define APPDEL_CLASS_SWIFT   @"Blinker.AppDelegate"

static NSString *const kPrefsPath = @"/var/mobile/Library/Preferences/com.oka.wifitweak.plist";

// 诊断日志已移除（v1.1.3）：插件运行时不输出任何系统日志。
// 功能逻辑不依赖日志，排错以实际功能表现为准。

// 类查找：先试 mangled，再试 Swift 写法，最后按后缀遍历兜底
static Class LookupClass(NSString *mangled, NSString *swiftName) {
    Class c = NSClassFromString(mangled);
    if (c) return c;
    c = NSClassFromString(swiftName);
    if (c) return c;

    NSString *suffix = [swiftName componentsSeparatedByString:@"."].lastObject;
    if (!suffix.length) return nil;

    // 两次调用之间类列表可能增长，所以第二次容量留点余量
    int n = objc_getClassList(NULL, 0);
    if (n <= 0) return nil;
    int cap = n + 256;
    Class *buf = (Class *)malloc(sizeof(Class) * cap);
    if (!buf) return nil;
    n = objc_getClassList(buf, cap);

    Class found = nil;
    for (int i = 0; i < n; i++) {
        const char *nm = class_getName(buf[i]);
        if (nm && strstr(nm, suffix.UTF8String)) { found = buf[i]; break; }
    }
    free(buf);
    return found;
}

// ---------------------------------------------------------------------------
// 运行期配置（读 plist，缺省全开）
// ---------------------------------------------------------------------------
static BOOL gEnableShortcut    = YES;   // 功能 1：长按菜单
static BOOL gEnableCellular    = YES;   // 功能 2a：蜂窝/热点可开启
static BOOL gEnableIPFix       = YES;   // 功能 2b：热点 IP 修正
static BOOL gEnableWebEnhance  = YES;   // 功能 3：电脑网页端拖放上传 / 批量移动
static BOOL gCopyURLOnShortcut = YES;   // 长按开启后把地址复制到剪贴板

// ★ v1.3.0 固定访问地址（只加不改）
//   原版端口本来就是固定的 5426，http://<IP>:5426 的访问方式**原样保留**。
//   这里额外多给一条 http://<设备名>.local:5426 —— IP 随「个人热点 /
//   家里 WiFi / 公司 WiFi」变化，主机名不变，电脑只需记一条地址。
//   ★ 默认不覆盖监听端口（gEnableFixedPort = NO）：一旦覆盖，App 原本的
//     http://IP:5426 就直接连不上，等于把用户原有的访问方式弄丢了。
static BOOL gEnableFixedAddr = YES;     // 额外产出 mDNS 形式的地址（不改端口）
static BOOL gEnableFixedPort = NO;      // 是否强制覆盖监听端口（默认关）
static NSUInteger gFixedPort  = 5426;   // 原版端口；仅在 EnableFixedPort 开启时生效
static NSString  *gFixedHost  = nil;    // 自定义主机名前缀（留空 = 取设备名）

// ★ v1.5.0 网页访问密码（HTTP Basic，服务端校验）
//   默认关闭：留空即完全不启用认证，与原版行为一致。
//   开启方式（偏好 plist：/var/mobile/Library/Preferences/com.oka.wifitweak.plist）：
//       WebPassword  = "你的密码"     <- 只要非空就启用
//       WebUsername  = "oka"          <- 可选，默认 oka
//   为什么用 Basic 而不是自写登录页：自写页面的密码在 JS 里明文可读，只是心理安慰；
//   Basic 由服务端在路由之前校验，所有路径（含 /oka-raw、下载）统一被拦。
//   局域网内是明文传输（Basic 固有特性），但足以挡住"同一 WiFi 下随手打开"。
static NSString  *gWebUser     = nil;   // 认证用户名（默认 oka）
static NSString  *gWebPassword = nil;   // 认证密码（空 = 不启用）

static void LoadPrefs(void) {
    NSDictionary *d = [NSDictionary dictionaryWithContentsOfFile:kPrefsPath];
    if (!d) return;
    if (d[@"EnableShortcut"])    gEnableShortcut    = [d[@"EnableShortcut"] boolValue];
    if (d[@"EnableCellular"])    gEnableCellular    = [d[@"EnableCellular"] boolValue];
    if (d[@"EnableIPFix"])       gEnableIPFix       = [d[@"EnableIPFix"] boolValue];
    if (d[@"EnableWebEnhance"])  gEnableWebEnhance  = [d[@"EnableWebEnhance"] boolValue];
    if (d[@"CopyURLOnShortcut"]) gCopyURLOnShortcut = [d[@"CopyURLOnShortcut"] boolValue];
    if (d[@"EnableFixedAddr"])   gEnableFixedAddr   = [d[@"EnableFixedAddr"] boolValue];
    if (d[@"EnableFixedPort"])   gEnableFixedPort   = [d[@"EnableFixedPort"] boolValue];
    if (d[@"FixedPort"]) {
        NSInteger p = [d[@"FixedPort"] integerValue];
        if (p > 0 && p < 65536) gFixedPort = (NSUInteger)p;
    }
    if ([d[@"FixedHost"] isKindOfClass:[NSString class]] && [d[@"FixedHost"] length]) {
        gFixedHost = d[@"FixedHost"];
    }
    if ([d[@"WebUsername"] isKindOfClass:[NSString class]] && [d[@"WebUsername"] length]) {
        gWebUser = d[@"WebUsername"];
    }
    if ([d[@"WebPassword"] isKindOfClass:[NSString class]] && [d[@"WebPassword"] length]) {
        gWebPassword = d[@"WebPassword"];
    }
}

// 保存一份强引用，避免"无界面实例"被提前释放导致 httpServer 消失
static NSMutableArray *gRetained = nil;
static void RetainObject(id o) {
    if (!o) return;
    if (!gRetained) gRetained = [NSMutableArray new];
    [gRetained addObject:o];
}

// 捕获到的 GCDWebServer 监听端口 / 运行状态。
// v1.0.2：两者都由 -[GCDWebServer serverURL] hook 用实例自己的
// port / isRunning 更新，比 startWithPort: hook 可靠（App 可能走 startWithOptions:）。
static NSUInteger gServerPort = 0;
static BOOL gServerRunning = NO;

// ===========================================================================
//  〇、后台保活
// ===========================================================================
//  App 的 UIBackgroundModes 只有 audio / fetch，没有 voip。
//  从桌面快捷方式开启后如果直接切后台，系统几秒内就挂起进程，
//  GCDWebServer 的监听 socket 也随之停摆 —— 电脑就连不上了。
//  这里用"后台播一段静音音频"把它钉在活跃态。App 包里自带 "Happy Dreams.mp3"，
//  直接复用它（不用往包里加文件），拿不到就静默跳过。
// ===========================================================================
static void *gKeepAlivePlayer = NULL;   // AVAudioPlayer*
static BOOL gKeepAliveOn = NO;

static void StartBackgroundKeepAlive(void) {
    if (gKeepAliveOn) return;

    Class avSessionCls = NSClassFromString(@"AVAudioSession");
    if (avSessionCls) {
        id session = ((id (*)(id, SEL))objc_msgSend)((id)avSessionCls,
                                                    @selector(sharedInstance));
        if (session) {
            typedef BOOL (*SetCat_t)(id, SEL, NSString *, NSError **);
            typedef BOOL (*SetActive_t)(id, SEL, BOOL, NSUInteger, NSError **);
            ((SetCat_t)(void *)objc_msgSend)(session,
                NSSelectorFromString(@"setCategory:error:"),
                @"AVAudioSessionCategoryPlayback", NULL);
            // AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation = 1
            ((SetActive_t)(void *)objc_msgSend)(session,
                NSSelectorFromString(@"setActive:withOptions:error:"), YES, 1, NULL);
        }
    }

    Class playerCls = NSClassFromString(@"AVAudioPlayer");
    if (!playerCls) { return; }

    NSString *path = [[NSBundle mainBundle] pathForResource:@"Happy Dreams" ofType:@"mp3"];
    id player = nil;
    if (path) {
        NSURL *u = [NSURL fileURLWithPath:path];
        id alloced = ((id (*)(id, SEL))objc_msgSend)((id)playerCls, @selector(alloc));
        player = ((id (*)(id, SEL, id, NSError **))objc_msgSend)(
            alloced, @selector(initWithContentsOfURL:error:), u, NULL);
    }
    if (player) {
        // numberOfLoops = -1 无限循环；volume = 0 静音
        ((void (*)(id, SEL, NSInteger))objc_msgSend)(player, @selector(setNumberOfLoops:), -1);
        ((void (*)(id, SEL, float))objc_msgSend)(player, @selector(setVolume:), 0.0f);
        BOOL ok = ((BOOL (*)(id, SEL))objc_msgSend)(player, @selector(play));
        gKeepAlivePlayer = (__bridge_retained void *)player;
        gKeepAliveOn = ok;
    }
}

static void StopBackgroundKeepAlive(void) {
    if (gKeepAlivePlayer) {
        id p = (__bridge id)gKeepAlivePlayer;
        ((void (*)(id, SEL))objc_msgSend)(p, @selector(stop));
        // ★ 与 (__bridge_retained void *) 成对的释放必须用 CFBridgingRelease / CFRelease。
        //   这里原来写 CFRelease(gKeepAlivePlayer)，参数是 void*：
        //   ARC 下 CFRelease 签名是 void CFRelease(CFTypeRef)，CFTypeRef 为 const void*，
        //   显式传 void* 会被判 "incompatible pointer types" 直接报错。
        //   CFBridgingRelease 接收 void* 并转成 CFTypeRef 再 release，
        //   语义就是「把 +1 的引用交还给 ARC」，正好对应 __bridge_retained。
        CFRelease((CFTypeRef)gKeepAlivePlayer);
        gKeepAlivePlayer = NULL;
    }
    gKeepAliveOn = NO;
}

// ===========================================================================
//  一、功能 2b：热点 IP 修正
// ===========================================================================
//  hook _GCDWebServerGetPrimaryIPAddress(BOOL includeIPv6) -> NSString*
//  原函数找不到 en0 就返回 nil，这里退回自己挑一个"电脑真能连上"的地址。
// ===========================================================================

typedef NSString *(*GetPrimaryIPAddress_t)(BOOL);
static GetPrimaryIPAddress_t orig_GetPrimaryIPAddress = NULL;

static NSString *IPStringFromSockAddr(const struct sockaddr *sa) {
    if (!sa) return nil;
    char buf[INET6_ADDRSTRLEN] = {0};
    if (sa->sa_family == AF_INET) {
        const struct sockaddr_in *sin = (const struct sockaddr_in *)sa;
        if (inet_ntop(AF_INET, &sin->sin_addr, buf, sizeof(buf)))
            return [NSString stringWithUTF8String:buf];
    } else if (sa->sa_family == AF_INET6) {
        const struct sockaddr_in6 *sin6 = (const struct sockaddr_in6 *)sa;
        if (IN6_IS_ADDR_LINKLOCAL(&sin6->sin6_addr) ||
            IN6_IS_ADDR_LOOPBACK(&sin6->sin6_addr)) return nil;
        if (inet_ntop(AF_INET6, &sin6->sin6_addr, buf, sizeof(buf)))
            return [NSString stringWithUTF8String:buf];
    }
    return nil;
}

// 接口打分：分数越高越优先。热点场景电脑走的就是 bridge100 这个网段。
// IPv4 一律高于 IPv6 —— 电脑浏览器输的地址基本都是 v4，v6 链接本地地址
// 带 scope id（fe80::1%en0）复制到别处也用不了。
static int ScoreInterface(const char *name, sa_family_t fam) {
    if (!name) return -1;
    if (strcmp(name, "lo0") == 0)          return -1;   // 回环，绝对排除
    if (strncmp(name, "utun", 4) == 0)     return -1;   // VPN 隧道对端不可达
    if (strncmp(name, "ipsec", 5) == 0)    return -1;
    if (strncmp(name, "awdl", 4) == 0)     return -1;   // AirDrop，不可用
    if (strncmp(name, "llw", 3) == 0)      return -1;

    int v4 = (fam == AF_INET);

    if (strncmp(name, "bridge", 6) == 0)   return v4 ? 100 : 60;  // 个人热点
    if (strncmp(name, "en", 2) == 0)       return v4 ? 90  : 50;  // Wi-Fi
    if (strncmp(name, "pdp_ip", 6) == 0)   return v4 ? 80  : 40;  // 蜂窝
    return v4 ? 10 : 5;
}

static NSString *BestLocalIPAddress(BOOL includeIPv6) {
    struct ifaddrs *head = NULL;
    if (getifaddrs(&head) != 0 || !head) return nil;

    NSString *best = nil;
    int bestScore = -1;

    for (struct ifaddrs *ifa = head; ifa; ifa = ifa->ifa_next) {
        if (!ifa->ifa_addr) continue;
        if (!(ifa->ifa_flags & IFF_UP)) continue;

        sa_family_t fam = ifa->ifa_addr->sa_family;
        if (fam != AF_INET && !(includeIPv6 && fam == AF_INET6)) continue;

        int s = ScoreInterface(ifa->ifa_name, fam);
        if (s < 0) continue;

        NSString *ip = IPStringFromSockAddr(ifa->ifa_addr);
        if (!ip.length) continue;
        if ([ip isEqualToString:@"0.0.0.0"]) continue;
        if ([ip hasPrefix:@"127."]) continue;
        if ([ip hasPrefix:@"fe80:"]) continue;   // 链路本地，跨设备不可用

        if (s > bestScore) { bestScore = s; best = ip; }
    }
    freeifaddrs(head);

    return best;
}

// 原函数返回值必须是**真正的 IP**才能采信。
// 实测反汇编（GCDWebServer.framework 0x17FF4）：
//   找不到 en0 时走 "mov x20,#0" → 返回 nil；
//   -[GCDWebServer serverURL] 在 0xEE94 用 cbz 判空，
//   为 nil 直接 "mov x20,#0"（0xEF3C）→ serverURL 返回 nil。
//   旧写法用 `if (r.length > 0) return r`，只要原函数（或上层包装）
//   回一个非空的占位串（"unknown" / "localhost" / "(null)"），
//   fallback 就永远不触发 —— 界面于是停在占位文案上。
static BOOL IsPlausibleIPAddress(NSString *s) {
    if (!s.length) return NO;
    const char *c = [s UTF8String];
    if (!c) return NO;
    struct in_addr a4;
    struct in6_addr a6;
    if (inet_pton(AF_INET,  c, &a4) == 1) return YES;
    if (inet_pton(AF_INET6, c, &a6) == 1) return YES;
    return NO;
}

// 生成「IP + 真实端口」的访问地址，给「WiFi 传输已开启」弹窗用。
// 与页面上的 <设备名>.local 互补：IP 在任何系统/任何网络下都能连，
// .local 靠 mDNS 解析（老系统不装 Bonjour、或热点下 mDNS 不稳时会失败）。
static NSString *OkaIPURLString(void) {
    NSString *ip = BestLocalIPAddress(NO);
    if (!ip.length) return nil;
    NSUInteger port = gServerPort;
    if (!port) port = gFixedPort;
    if (!port) return nil;
    return [NSString stringWithFormat:@"http://%@:%lu/", ip, (unsigned long)port];
}

static NSString *my_GetPrimaryIPAddress(BOOL includeIPv6) {
    NSString *r = orig_GetPrimaryIPAddress ? orig_GetPrimaryIPAddress(includeIPv6) : nil;
    if (!gEnableIPFix) return r;
    if (IsPlausibleIPAddress(r)) return r;     // 是合法 IP，原逻辑正常，不动

    NSString *ip = BestLocalIPAddress(includeIPv6);
    if (ip) { return ip; }
    return r;
}

// ===========================================================================
//  ★★★ v1.0.2 主力修复：hook -[GCDWebServer serverURL] ★★★
// ===========================================================================
//  这才是 "unknown" 的真正源头，前两版改 UI 控件都没打中。
//
//  静态证据（BNFileSharingService.framework，Helper.uploaderUrl 共享实现 0x713C）：
//    0x715C  mov  x19, #0x6e75              "un"
//    0x7160  movk x19, #0x6e6b, lsl #16    "kn"
//    0x7164  movk x19, #0x776f, lsl #32    "ow"
//    0x7168  movk x19, #0x6e,   lsl #48    "n"     → Swift 小字符串 "unknown"
//    0x71EC  bl   objc_msgSend(uploader, serverURL)   → NSURL?
//    0x7240  (serverURL == nil) → 直接返回 x19 = "unknown"
//    0x7268  bl   _$s10Foundation3URLVSgWOb           → Foundation.URL? 转换
//
//  即：Helper.uploaderUrl 在 serverURL 为 nil 时返回常量 "unknown"，
//  App 的「服务器地址」文字、二维码、复制按钮用的都是这个字符串。
//  _GCDWebServerGetPrimaryIPAddress 硬编码 en0 → 热点下 serverURL 返回 nil
//  → uploaderUrl == "unknown"。所以只要让 serverURL 返回正确 NSURL，
//  整条 UI 链路（含二维码）一次性全对，不必碰任何 outlet / 时序 / 控件类型。
//
//  为什么不用 MSHookFunction 那层：它改的是 C 函数入口，对 framework 内部
//  调用是否生效无法从代码证明；而 serverURL 是 ObjC 方法，method_setImplementation
//  一定命中（GCDWebUploader 继承 GCDWebServer，方法查找走到父类 IMP）。
//  另外 GCDWebServer 公开 port(NSUInteger) / isRunning(BOOL)，
//  真实端口和运行状态都能直接从实例取，不用猜 8080、不用扫端口。
// ---------------------------------------------------------------------------
//  ★ v1.2.0 mDNS 主机名
// ---------------------------------------------------------------------------
//  iOS 的 mDNSResponder 会无条件把「设备名」注册成 <设备名>.local，这一步是
//  系统行为：App 不需要发布 Bonjour 服务，Info.plist 也不需要声明
//  NSBonjourServices（当前包里只有 _smb._tcp，走这条路不受影响）。
//
//  唯一要求是主机名必须是合法 DNS 标签：ASCII、小写、只含 a-z0-9 和连字符、
//  不以连字符开头结尾、长度 <= 63。设备名可能是中文（「张三的 iPhone」），
//  所以这里做一次规范化；规范化后为空（纯中文名）就返回 nil，调用方退回 IP。
//
//  ★ 一个坑：改了设备名，地址就变了。所以设备名要当「固定配置」看待，
//    建议在 设置 → 通用 → 关于本机 → 名称 里设成短英文（如 oka / oka-air）。
//  主机名的权威来源：读 mDNSResponder 实际注册的那个名字。
//  ★ UIDevice.name 与它不一定相同 —— 系统会把空格换成连字符、丢掉非法字符，
//    同名设备还会自动追加 -2 / -3 后缀。名字对不上，<name>.local 就解析不到，
//    电脑浏览器直接打不开，所以必须优先读系统登记值，而不是自己猜。
//  SCDynamicStoreCopyLocalHostName 由 SystemConfiguration 导出（iOS 上可用）；
//  用 dlsym 解析，解析不到或返回 nil 时再退回设备名规范化，不产生硬依赖。
typedef CFStringRef (*CopyLocalHostName_t)(void *);
static NSString *OkaLocalHostNameFromSystem(void) {
    static CopyLocalHostName_t fn = NULL;
    static BOOL tried = NO;
    if (!tried) {
        tried = YES;
        fn = (CopyLocalHostName_t)dlsym(RTLD_DEFAULT, "SCDynamicStoreCopyLocalHostName");
    }
    if (!fn) return nil;
    CFStringRef s = fn(NULL);
    if (!s) return nil;
    return CFBridgingRelease(s);      // Copy 命名规则 -> 所有权交还 ARC
}

// ---- 候选主机名收集 --------------------------------------------------------
// 为什么不能只用 UIDevice.name：iOS 16+ 在无 entitlement 时恒定返回 "iPhone"，
// 与用户设置里的名称无关，拼出来的 <name>.local 根本解析不到本机 —— 这正是
// 界面上出现「iphone.local」又把用户带偏的原因。
// 为什么不能盲信 SCDynamicStoreCopyLocalHostName：它在 App 沙箱内常返回 nil
// （符号存在但无权限），于是静默退化成上面那个错名字。
// 所以：把所有来源都列出来，逐个用 getaddrinfo 实测，只采用真的能解析到本机的。

static BOOL OkaIPv4IsLocal(const char *dotted) {
    unsigned a = 0, b = 0, c = 0, d = 0;
    if (!dotted || sscanf(dotted, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) return NO;
    if (a == 10) return YES;                                // 10.0.0.0/8
    if (a == 172 && b >= 16 && b <= 31) return YES;          // 172.16.0.0/12
    if (a == 192 && b == 168) return YES;                    // 192.168.0.0/16
    if (a == 169 && b == 254) return YES;                    // 自分配地址
    if (a == 172 && b == 20 && c == 10) return YES;          // 个人热点常见段
    return NO;
}

// host 能否解析到「本机的局域网地址」。只认私有段，避免 127.0.0.1 造成假阳性。
static BOOL OkaHostResolvesToSelf(NSString *host) {
    if (!host.length) return NO;
    struct addrinfo hints, *res = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int rc = getaddrinfo(host.UTF8String, NULL, &hints, &res);
    if (rc != 0 || !res) return NO;

    BOOL ok = NO;
    for (struct addrinfo *p = res; p && !ok; p = p->ai_next) {
        char buf[INET6_ADDRSTRLEN] = {0};
        if (p->ai_family == AF_INET) {
            struct sockaddr_in *sa = (struct sockaddr_in *)p->ai_addr;
            if (!inet_ntop(AF_INET, &sa->sin_addr, buf, sizeof(buf))) continue;
            ok = OkaIPv4IsLocal(buf);
        } else if (p->ai_family == AF_INET6) {
            struct sockaddr_in6 *sa6 = (struct sockaddr_in6 *)p->ai_addr;
            if (!inet_ntop(AF_INET6, &sa6->sin6_addr, buf, sizeof(buf))) continue;
            // 链路本地 fe80:: 与唯一本地 fc00::/fd00:: 才算本机
            if (strncasecmp(buf, "fe80", 4) == 0 ||
                strncasecmp(buf, "fc", 2) == 0 ||
                strncasecmp(buf, "fd", 2) == 0) ok = YES;
        }
    }
    freeaddrinfo(res);
    return ok;
}

// 把任意来源压成可用标签：mode=0 宽松（仅小写、空格→连字符，保留中文等 UTF-8），
// mode=1 激进（只留 ASCII 字母数字，其余压成一个连字符，去首尾，截 40）。
static NSString *OkaNormalizeHostLabel(NSString *raw, int mode) {
    if (!raw.length) return nil;
    NSString *s = [raw stringByTrimmingCharactersInSet:
                   [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if ([s.lowercaseString hasSuffix:@".local"]) {
        s = [s substringToIndex:s.length - 6];
    }
    if (!s.length) return nil;

    if (mode == 0) {
        NSMutableString *loose = [NSMutableString stringWithCapacity:s.length];
        for (NSUInteger i = 0; i < s.length; i++) {
            unichar c = [s characterAtIndex:i];
            if (c == ' ' || c == '\t') [loose appendString:@"-"];
            else [loose appendFormat:@"%C", c];
        }
        NSString *res = [loose lowercaseString];
        return res.length ? res : nil;
    }

    NSMutableString *out = [NSMutableString stringWithCapacity:s.length];
    BOOL lastDash = YES;
    for (NSUInteger i = 0; i < s.length; i++) {
        unichar c = [s characterAtIndex:i];
        if (c >= 'a' && c <= 'z') { [out appendFormat:@"%c", (char)c]; lastDash = NO; }
        else if (c >= 'A' && c <= 'Z') { [out appendFormat:@"%c", (char)(c - 'A' + 'a')]; lastDash = NO; }
        else if (c >= '0' && c <= '9') { [out appendFormat:@"%c", (char)c]; lastDash = NO; }
        else if (!lastDash) { [out appendString:@"-"]; lastDash = YES; }
    }
    while (out.length && [out hasSuffix:@"-"]) {
        [out deleteCharactersInRange:NSMakeRange(out.length - 1, 1)];
    }
    if (out.length > 40) {
        out = [[out substringToIndex:40] mutableCopy];
        while (out.length && [out hasSuffix:@"-"]) {
            [out deleteCharactersInRange:NSMakeRange(out.length - 1, 1)];
        }
    }
    return out.length ? out : nil;
}

static void OkaCollectHostCandidates(NSMutableArray *out) {
    // ① 反查本机 IP 的 PTR —— mDNSResponder 实际注册了什么名字，这里就返回什么。
    //    这是唯一「问系统」的权威来源，放第一位；静态的读法（UIDevice.name）
    //    在 iOS 16+ 恒为 "iPhone"，正是假名字的来源。
    NSString *selfIP = BestLocalIPAddress(NO);
    if (selfIP.length) {
        struct addrinfo qh, *qres = NULL;
        memset(&qh, 0, sizeof(qh));
        qh.ai_family = AF_INET;
        qh.ai_flags = AI_NUMERICHOST;
        if (getaddrinfo(selfIP.UTF8String, NULL, &qh, &qres) == 0 && qres) {
            char pname[NI_MAXHOST] = {0};
            if (getnameinfo(qres->ai_addr, qres->ai_addrlen, pname, sizeof(pname),
                            NULL, 0, NI_NAMEREQD) == 0 && pname[0]) {
                NSString *s = [NSString stringWithUTF8String:pname];
                if (s.length) [out addObject:s];
            }
            freeaddrinfo(qres);
        }
    }

    // ② SystemConfiguration 登记值（沙箱内常为 nil）
    NSString *sysName = OkaLocalHostNameFromSystem();
    if (sysName.length) [out addObject:sysName];

    // ③ gethostname()：部分越狱环境返回真实 LocalHostName
    char hbuf[256] = {0};
    if (gethostname(hbuf, sizeof(hbuf) - 1) == 0 && hbuf[0]) {
        NSString *s = [NSString stringWithUTF8String:hbuf];
        if (s.length) [out addObject:s];
    }

    // ④ NSProcessInfo.hostName：沙箱内多为 localhost，越狱环境可能给真名
    NSString *pn = [[NSProcessInfo processInfo] hostName];
    if (pn.length) [out addObject:pn];

    // ⑥ 最后才是设备名（iOS 16+ 多为 "iPhone"，仅作兜底）
    NSString *dev = [[UIDevice currentDevice] name];
    if (dev.length) [out addObject:dev];
}

// ★ 阻塞探测：只在后台队列调用。名字不存在时 getaddrinfo 要等 mDNS 超时
//   （数秒），所以绝不能在 App 取地址的路径上（主线程）同步执行。
static NSString *OkaProbeHostLabelBlocking(void) {
    NSMutableArray *cands = [NSMutableArray array];
    OkaCollectHostCandidates(cands);

    // 每个候选先按宽松规范化（保留中文）试，再按激进规范化试；
    // 只有 getaddrinfo 真能解析到本机私有地址的才采纳。
    for (NSString *raw in cands) {
        for (int mode = 0; mode <= 1; mode++) {
            NSString *label = OkaNormalizeHostLabel(raw, mode);
            if (!label.length) continue;
            if ([label isEqualToString:@"localhost"]) continue;
            NSString *full = [label stringByAppendingString:@".local"];
            if (OkaHostResolvesToSelf(full)) return label;
        }
    }
    // 一个都解析不到：返回 nil，让上层退回纯 IP 地址。
    // 宁可不显示固定地址，也不能显示一个打不开的假名字。
    return nil;
}

static void OkaRefreshVisiblePage(void);   // 定义在页面区（探测成功后重写地址）
static __weak id gLastPage = nil;          // 最近打开的传输页

static NSString *gHostLabel = nil;          // 探测成功后的缓存
static BOOL gHostProbeRunning = NO;
static NSTimeInterval gHostProbedAt = 0;
static const NSTimeInterval kHostRetryInterval = 30.0;

static NSString *OkaFixedHostLabel(void) {
    if (gFixedHost.length) return gFixedHost;      // 用户显式配置，信任
    if (gHostLabel.length) return gHostLabel;      // 命中缓存

    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
    BOOL due = !gHostProbeRunning &&
               (gHostProbedAt <= 0 || (now - gHostProbedAt) > kHostRetryInterval);
    if (due) {
        gHostProbeRunning = YES;
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            NSString *found = OkaProbeHostLabelBlocking();
            dispatch_async(dispatch_get_main_queue(), ^{
                gHostProbeRunning = NO;
                gHostProbedAt = [NSDate timeIntervalSinceReferenceDate];
                if (found.length) {
                    gHostLabel = found;
                    // 探测成功：已打开的页面可能还写着 IP，主动刷新一次。
                    OkaRefreshVisiblePage();
                }
            });
        });
    }
    // 探测未完成（或失败）：返回 nil，让上层先走纯 IP。
    // 先显示 IP 是安全的；显示一个解析不到的假名字才是坑。
    return nil;
}

static NSString *OkaFixedHost(void) {
    NSString *label = OkaFixedHostLabel();
    if (!label.length) return nil;
    return [label stringByAppendingString:@".local"];
}

static NSURL *(*orig_serverURL)(id, SEL);
static NSURL *my_serverURL(id self, SEL _cmd) {
    NSURL *url = orig_serverURL ? orig_serverURL(self, _cmd) : nil;

    // 运行状态 / 真实端口一律从实例取（权威判据，不猜端口）
    BOOL running = NO;
    if ([self respondsToSelector:@selector(isRunning)]) {
        running = ((BOOL (*)(id, SEL))objc_msgSend)(self, @selector(isRunning));
    }
    gServerRunning = running;

    NSUInteger port = 0;
    if ([self respondsToSelector:@selector(port)]) {
        port = ((NSUInteger (*)(id, SEL))objc_msgSend)(self, @selector(port));
    }
    if (!port) port = [[url port] unsignedIntegerValue];
    if (port) gServerPort = port;

    // 服务没在跑就别造地址：此时空白 / unknown 是正确状态。
    if (!running && !port) return url;

    NSUInteger livePort = port ? port : gFixedPort;

    // ① 固定地址：<设备名>.local:<固定端口>
    //    与当前 IP 完全解耦，热点换成 WiFi 也不用改地址。
    if (gEnableFixedAddr) {
        NSString *mdnsHost = OkaFixedHost();
        if (mdnsHost.length) {
            return [NSURL URLWithString:
                    [NSString stringWithFormat:@"http://%@:%lu/",
                     mdnsHost, (unsigned long)livePort]];
        }
    }

    // ② IP 修正（原逻辑保留）：主机名规范化失败时退回 IP，
    //    WiFi 下原本就正常的 IP 地址原样透传。
    if (gEnableIPFix) {
        if (IsPlausibleIPAddress([url host])) return url;
        NSString *ip = BestLocalIPAddress(NO);
        if (ip.length) {
            return [NSURL URLWithString:
                    [NSString stringWithFormat:@"http://%@:%lu/",
                     ip, (unsigned long)livePort]];
        }
    }
    return url;
}

// ===========================================================================
//  二、功能 2a：解除蜂窝/热点限制
// ===========================================================================
//  两层保险：
//   层 1（根因）hook SCNetworkReachabilityGetFlags，把 IsWWAN(0x40000) 等
//              "蜂窝特征位"清掉，App 自己算出来的结论就是"WiFi 可达"，
//              于是它自己把开始按钮显示出来、二维码也画出来。
//   层 2（兜底）hook -[BNHttpServerPage reachabilityChangedWithNote:]，跑完
//              原逻辑后强制 startBtn.hidden = NO。即便某些系统版本 flag 语义
//              有差异，按钮也一定是可点的。
// ===========================================================================

typedef Boolean (*SCNRGetFlags_t)(void *, void *);
static SCNRGetFlags_t orig_SCNRGetFlags = NULL;

//  只清 IsWWAN 一位，别的不碰。
//  理由（按 0x100012ee4~0x100012f04 的汇编逐条推演）：
//      w9  = 1 & ~(flags >> 2)        // = !TransientConnection
//      w10 = ConnectionRequired ? w9 : 1
//      w9  = (ConnectionOnDemand|ConnectionOnTraffic) ? w10 : w9
//      if (flags & IsWWAN)  -> 不可用          ★ 唯一的蜂窝判据
//      if (!w9)             -> 不可用
//  实测蜂窝下 flags = Reachable(0x2) | IsWWAN(0x40000)，TransientConnection 并不置位，
//  所以 w9 本来就是 1；把 bit18 清掉后整条判定直接走"可用"分支。
//  改别的位只会给 App 其它联网逻辑引入副作用，没必要。
static Boolean my_SCNRGetFlags(void *target, void *flags) {
    Boolean ok = orig_SCNRGetFlags ? orig_SCNRGetFlags(target, flags) : 0;
    if (!ok || !flags || !gEnableCellular) return ok;

    uint32_t f = *(uint32_t *)flags;
    if (!(f & (1u << 18))) return ok;          // 本来就不是蜂窝，不动

    f &= ~(1u << 18);                          // kSCNetworkReachabilityFlagsIsWWAN
    *(uint32_t *)flags = f;
    return ok;
}

// --- 取 BNHttpServerPage 的 outlet / 服务对象 -------------------------------
//
//  ★★ 这里不能用 object_getIvar ★★
//  BNHttpServerPage 是 Swift 类，8 个 ivar 里有 7 个是 Swift 的
//  `weak var`（UILabel/UIView/UIImageView/UIButton/UITextView，全是 nil 初始值）。
//  证据：-init 的实现（0x1000121f0）对 wifiStatus/connection/ipAddress/
//  connectionView/qrCodeView/startBtn 逐个调用 _swift_unknownObjectWeakInit
//  （0x100012284 ~ 0x100012310 六次，加 reachability 之外的弱引用初始化）。
//
//  Swift 的 weak 存储不是"指向对象的指针"，而是一个 side-table 引用
//  （objc_storeWeak 那套：目标 alive 时低位是对象指针，已销毁时是
//   isa=Tombstone 的占位对象）。直接把这段内存当 id 用，
//  轻则拿到垃圾指针、`respondsToSelector` 随机返回，重则 EXC_BAD_ACCESS。
//  唯一正确的读法是 _swift_unknownObjectWeakLoadStrong(ivaddr)，
//  它内部处理 side-table 并返回 +0 retained 的强引用，用完要 release。
//
//  httpServer（off 0x8）是唯一一个"强引用"ivar：
//  反汇编里是 `bl BNFileSharingService.Helper.shared` 后直接
//  `str x0, [x21, #0x8]`，没有 WeakInit，所以可以正常 object_getIvar。
//  但它存的是 Swift 结构体单例（Helper.shared），未必是 ObjC 对象，
//  因此下面单独用 PointerAtOffset 按裸指针读，不假设它是 ObjC 引用。

// 按真实 ivar 偏移读一个裸指针（不做任何 Swift 语义解释）
static void *PointerAtOffset(id obj, ptrdiff_t off) {
    if (!obj || off < 0) return NULL;
    return *(void **)((uint8_t *)(__bridge void *)obj + off);
}

// Swift weak 存储的正确读法：_swift_unknownObjectWeakLoadStrong(slot) 返回 +1 retained。
// 该符号由 libswiftCore 导出；App 已链接 libswiftCore.dylib，RTLD_DEFAULT 必然可解析。
// 解析不到时退化为 nil（宁可功能不生效，也不要解引用垃圾指针）。
typedef id (*WeakLoadStrong_t)(void *);
static WeakLoadStrong_t gWeakLoadStrong = NULL;

// 统一入口。
// 返回 +1 retained（ns_returns_retained），ARC 调用方会在作用域结束时自动 release，
// 与 weak/strong 两种来源都能正确配对，不会泄漏也不会悬垂。
static id GetIvar(id obj, const char *name) __attribute__((ns_returns_retained));
static id GetIvar(id obj, const char *name) {
    if (!obj) return nil;
    Class cls = [obj class];
    Ivar iv = class_getInstanceVariable(cls, name);
    if (!iv) return nil;
    ptrdiff_t off = ivar_getOffset(iv);

    // httpServer 是唯一的强引用（反汇编里直接 str，未走 WeakInit）。
    // object_getIvar 对 strong ivar 返回 +0，而本函数声明为 ns_returns_retained，
    // 所以这里必须额外 +1 一次，否则 ARC 会在作用域结束时多减一次引用 → 过度释放崩溃。
    //
    // ★ 不能写成 (__bridge id)(__bridge_retained void *)v：
    //   嵌套两个桥接转换，语义是「先转成 +1 的 void*，再转回 id（此时 ARC 认为它 +0）」，
    //   clang 对这种写法行为不确定，也是本次 CI 里同类报错的来源。
    // ★ 也不能写 [v retain]：ARC 明确禁止显式发送 retain/release
    //   （error: ARC forbids explicit message send of 'retain'）。
    //   正确做法是 CFBridgingRetain：它返回 +1 的 CFTypeRef，
    //   转成 id 时所有权已经转移，与 ns_returns_retained 契约完全对齐。
    if (strcmp(name, "httpServer") == 0) {
        id v = object_getIvar(obj, iv);
        if (!v) return nil;
        return (__bridge id)CFBridgingRetain(v);   // +1，符合 ns_returns_retained
    }

    // 其余全是 Swift weak：必须走 LoadStrong，否则拿到的是 side-table 垃圾
    if (!gWeakLoadStrong) return nil;
    void *slot = (uint8_t *)(__bridge void *)obj + off;
    return gWeakLoadStrong(slot);        // +1，符合 ns_returns_retained 契约
}

// --- 读取服务状态 -----------------------------------------------------------
//
//  ★ 为什么不去反射 BNFileSharingHelper ★
//  BNFileSharingService.framework 的 __objc_classlist 只有 2 项：
//      PodsDummy_BNFileSharingService、__ARCLite__
//  Helper 是纯 Swift 类，压根没有 ObjC 类元数据。对它的实例调用
//  object_getClass / class_getInstanceVariable / respondsToSelector
//  属于未定义行为（可能直接崩）。
//
//  ★ 改用 App 自己写好的结果 ★
//  changeWifiTransferStateAction: 启动成功后，App 自己会执行
//      ipAddress.setText(httpServer.serverURL().absoluteString)
//      qrCodeView.image = QR(url)
//  所以只要读 ipAddress（UITextView，weak outlet）的 text，就能拿到地址，
//  完全不需要理解 Helper 的内部结构。这条路是 App 主流程走过的，
//  比我们去猜 Swift 内部布局可靠得多。
// ---------------------------------------------------------------------------

// 从页面读 App 自己填好的地址文本
static NSString *URLTextFromPage(id page) {
    if (!page) return nil;
    id tv = GetIvar(page, "ipAddress");     // UITextView
    if (!tv) return nil;

    SEL s = @selector(text);
    if (![tv respondsToSelector:s]) return nil;
    id t = ((id (*)(id, SEL))objc_msgSend)(tv, s);
    if (![t isKindOfClass:[NSString class]]) return nil;

    NSString *str = (NSString *)t;
    str = [str stringByTrimmingCharactersInSet:
           [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (str.length == 0) return nil;
    // App 在未启动时可能显示占位文案，只认 http(s):// 开头的
    if (![str.lowercaseString hasPrefix:@"http"]) return nil;
    // ★ v1.3.0：ipAddress 现在是两行（固定地址 + 备用 IP）。
    //   这里只取第一行：否则复制进剪贴板会连"备用："那行一起，
    //   粘到浏览器里直接打不开。二维码不受影响 —— 它走 serverURL。
    NSRange nl = [str rangeOfString:@"\n"];
    if (nl.location != NSNotFound && nl.location > 0) {
        str = [str substringToIndex:nl.location];
        str = [str stringByTrimmingCharactersInSet:
               [NSCharacterSet whitespaceCharacterSet]];
    }
    return str.length ? str : nil;
}

// 强制把"开始"按钮放出来（startBtn / connectionView 都是 weak outlet，取到才动）
static void ForceStartButtonVisible(id page) {
    if (!page) return;

    id startBtn = GetIvar(page, "startBtn");
    if (startBtn) {
        ((void (*)(id, SEL, BOOL))objc_msgSend)(startBtn, @selector(setHidden:), NO);
    }

    id connectionView = GetIvar(page, "connectionView");
    if (connectionView) {
        ((void (*)(id, SEL, BOOL))objc_msgSend)(connectionView, @selector(setHidden:), NO);
    }
}

// 实现在文件后部的「UI 层地址修正」段；本文件的几个 hook 都在它之前，
// 所以前置声明必须放在最靠前的那个调用点（my_reachabilityChanged）之前。
static void RewriteAddressIfNeeded(id page);

// hook: reachabilityChangedWithNote:
static void (*orig_reachabilityChanged)(id, SEL, id);
static void my_reachabilityChanged(id self, SEL _cmd, id note) {
    if (orig_reachabilityChanged) orig_reachabilityChanged(self, _cmd, note);
    if (!gEnableCellular) return;

    // 原逻辑在蜂窝下会把开始按钮藏起来，这里再放出来。
    // 注意：这一步只是让按钮可见；真正的"能启动"依赖下面 SCNetworkReachability
    // 的 flag 清洗（层 1）。两层都开，任何一层失效都还有另一层兜底。
    ForceStartButtonVisible(self);

    // reachability 通知也是 App 刷新地址文案的入口。原逻辑跑完可能又把
    // ipAddress 写回占位词，所以延迟一拍再校正一次（幂等，地址正常时不动）。
    __weak id weakPage = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.3 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakPage) RewriteAddressIfNeeded(weakPage);
    });
}

// hook: viewDidLoad —— 首次进入页面时立刻放行按钮 + 校正地址
static void (*orig_viewDidLoad)(id, SEL);
static void my_viewDidLoad(id self, SEL _cmd) {
    if (orig_viewDidLoad) orig_viewDidLoad(self, _cmd);
    gLastPage = self;                       // 供后台探测成功后刷新地址
    if (gEnableCellular) {
        ForceStartButtonVisible(self);
        // reachability 通知可能在 viewDidLoad 之后才到，补一个延迟兜底
        __weak id weakSelf = self;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.6 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            if (weakSelf && gEnableCellular) ForceStartButtonVisible(weakSelf);
        });
    }
    // 若服务器是之前从快捷菜单开启的，进页面时地址文案可能已经是占位词。
    // RewriteAddressIfNeeded 只在「服务器在跑 + 地址坏了」时才改，幂等。
    __weak id weakPageForIP = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.2 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakPageForIP) {
            RewriteAddressIfNeeded(weakPageForIP);
        }
    });
    // ★ v1.3.1：直接进页面时 changeWifiTransferStateAction: 不会触发，
    //   备用行（原版 http://IP:端口）就一直不出现 —— 用户实测只看到一行。
    //   这里补一拍更晚的：App 可能在 1.2s 之后才把地址写进 ipAddress。
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.6 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakPageForIP) {
            RewriteAddressIfNeeded(weakPageForIP);
        }
    });
}

// ===========================================================================
//  三、功能 1：桌面快捷菜单
// ===========================================================================
//  注意：Info.plist 里没有 UIApplicationShortcutItems，也不能改（会破坏签名），
//  所以用运行时 API 注入动态菜单项（UIApplicationShortcutItem）。
// ===========================================================================

static UIApplicationShortcutItem *MakeShortcutItem(void) {
    UIApplicationShortcutIcon *icon = [UIApplicationShortcutIcon iconWithTemplateImageName:@"wifi"];
    if (!icon) icon = [UIApplicationShortcutIcon iconWithType:UIApplicationShortcutIconTypeShare];
    return [[UIApplicationShortcutItem alloc] initWithType:SHORTCUT_TYPE
                                            localizedTitle:@"开启 WiFi 传输"
                                         localizedSubtitle:nil
                                                      icon:icon
                                                  userInfo:nil];
}

static UIWindow *KeyWindow(void) {
    UIWindow *keyWin = nil;
    if (@available(iOS 13.0, *)) {
        for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
            if (![scene isKindOfClass:[UIWindowScene class]]) continue;
            for (UIWindow *w in ((UIWindowScene *)scene).windows) {
                if (w.isKeyWindow) { keyWin = w; break; }
            }
            if (keyWin) break;
        }
        if (!keyWin) {
            for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
                if (![scene isKindOfClass:[UIWindowScene class]]) continue;
                UIWindowScene *ws = (UIWindowScene *)scene;
                if (ws.windows.count) { keyWin = ws.windows.firstObject; break; }
            }
        }
    }
    if (!keyWin) keyWin = UIApplication.sharedApplication.keyWindow;
    return keyWin;
}

// 深度优先找已有的 BNHttpServerPage
static UIViewController *FindPage(UIViewController *root, int depth) {
    if (!root || depth > 12) return nil;
    Class pageCls = LookupClass(PAGE_CLASS_MANGLED, PAGE_CLASS_SWIFT);
    if (pageCls && [root isKindOfClass:pageCls]) return root;

    UIViewController *r = FindPage(root.presentedViewController, depth + 1);
    if (r) return r;

    if ([root isKindOfClass:[UINavigationController class]]) {
        for (UIViewController *c in ((UINavigationController *)root).viewControllers) {
            r = FindPage(c, depth + 1);
            if (r) return r;
        }
    }
    if ([root isKindOfClass:[UITabBarController class]]) {
        for (UIViewController *c in ((UITabBarController *)root).viewControllers) {
            r = FindPage(c, depth + 1);
            if (r) return r;
        }
    }
    for (UIViewController *c in root.childViewControllers) {
        r = FindPage(c, depth + 1);
        if (r) return r;
    }
    return nil;
}

// 没有现成页面时，从 nib 造一个（不显示）。BNHttpServerPage.nib 在 App 包里。
//
// ★★ 不要用 initWithNibName:bundle: ★★
//  反汇编 0x100013760（-initWithNibName:bundle: 的 Swift 覆写）：
//      bl __swift_stdlib_reportUnimplementedInitializer  → 必然 fatalError 崩溃
//  这个覆写只是 Swift 编译器为"子类没继承 designated init"生成的占位。
//
//  正确入口是 -init（IMP 0x1000123c4，实现在 0x1000121f0）：它内部做的是
//      nibName = BNHttpServerPage（类名，0x100013794 返回）
//      super init(nibName: nil, bundle: Bundle(for: 类))   ← 注意 nibName 传的是 nil
//      httpServer = BNFileSharingService.Helper.shared
//      reachability = ... 并注册 SCNetworkReachability 回调
//      outlet weak 槽全部 WeakInit(nil)
//  所以 `[[cls alloc] init]` 就是 App 自己创建这个页面的唯一正确方式。
static UIViewController *MakeHeadlessPage(void) {
    Class cls = LookupClass(PAGE_CLASS_MANGLED, PAGE_CLASS_SWIFT);
    if (!cls) { return nil; }

    UIViewController *page = nil;
    @try {
        // 走 -init（App 自己的 designated init），绝不能换成 initWithNibName:bundle:
        page = [[cls alloc] init];
        (void)page.view;                       // 触发 viewDidLoad，连好所有 outlet
    } @catch (NSException *e __unused) {
        return nil;
    }
    if (!page) return nil;

    // 关键校验：App 的 changeWifiTransferStateAction: 里有大量 Swift 强解包，
    // outlet 缺一个就是 brk #1（0x100013e24 起全是 brk）。所以这里逐个必须为真。
    //
    // ★ 注意：这里"必须为真"的只有 httpServer。
    //   其余 6 个 outlet 是 Swift weak，页面没上屏时它们是 nil —— 这是正常的，
    //   不是错误。用它们当门槛会导致无界面路径永远走不通。
    //   changeWifiTransferStateAction: 真正强解包的是 httpServer。
    id hs = GetIvar(page, "httpServer");
    if (!hs) {
        return nil;
    }
    RetainObject(page);                        // 保住它，别被释放
    return page;
}

// 拿到端口：hook GCDWebServer 的启动方法顺便记录
//  依据：方法表类型 B32@0:8Q16@24，且函数体 0x000000f270 "mov x0, x19; ret"
//  返回的是 startWithOptions:error: 的结果 —— 确实是 BOOL。
//  （注：__objc_methtype 段里另有一条 v32@0:8Q16@24 是别的同签名方法，别被带偏。）
static BOOL (*orig_startWithPort)(id, SEL, NSUInteger, NSString *);
static BOOL my_startWithPort(id self, SEL _cmd, NSUInteger port, NSString *name) {
    BOOL ok = orig_startWithPort ? orig_startWithPort(self, _cmd, port, name) : NO;

    NSUInteger real = ((NSUInteger (*)(id, SEL))objc_msgSend)(self, @selector(port));
    if (real) gServerPort = real;
    if (ok) gServerRunning = YES;
    return ok;
}

// ★ v1.2.0 固定端口
//   GCDWebServer 的 -startWithPort:bonjourName: 内部实现就是
//       options[GCDWebServerOption_Port] = @(port); ...;
//       return [self startWithOptions:options error:NULL];
//   而 -startWithOptions:（单参版）也是转调 -startWithOptions:error:。
//   所以在 -startWithOptions:error: 这一层重写 Port 一处，两条启动路径全覆盖，
//   不用分别改 startWithPort / startWithOptions。
//
//   GCDWebServer 的 options 键就是常量名本身（@"GCDWebServerOption_Port"），
//   已由 GCDWebServer.framework 字符串表核实存在该常量。
// ---------------------------------------------------------------------------
//  ★ 取 GCDWebServer 选项键的**真实值**
// ---------------------------------------------------------------------------
//  坑：GCDWebServer.h 里声明的是
//        extern NSString* const GCDWebServerOption_Port;
//      所以「符号名」是 GCDWebServerOption_Port，但「它指向的 CFString」不是这个名字。
//      实测 GCDWebServer.framework 符号表：
//        _GCDWebServerOption_Port        -> @"Port"
//        _GCDWebServerOption_BonjourName -> @"BonjourName"
//        _GCDWebServerOption_ServerName  -> @"ServerName"
//      写成 options[@"GCDWebServerOption_Port"] = @(8080) 只会往字典里塞一个
//      GCDWebServer 永不读取的键：端口照旧随机，界面照旧显示随机端口，
//      而且没有任何报错——属于「看起来改了、其实没生效」的静默失效。
//
//      这里 dlsym 拿符号地址，再读它指向的 NSString。dlsym 传的名字不带前导下划线。
//      取不到（框架未加载 / 符号被裁）才退回 fallback，保证不会崩。
static NSString *OkaGWSKey(const char *bare, NSString *fallback) {
    char sym[96];
    int n = snprintf(sym, sizeof(sym), "GCDWebServerOption_%s", bare);
    if (n <= 0 || n >= (int)sizeof(sym)) return fallback;

    void *h = dlsym(RTLD_DEFAULT, sym);
    if (!h) {
        dlopen("@rpath/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
        dlopen("Frameworks/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
        h = dlsym(RTLD_DEFAULT, sym);
    }
    if (!h) return fallback;

    NSString *v = *(NSString * const *)h;   // 符号指向的是 NSString * const 变量
    return v.length ? v : fallback;
}


// 取 GCDWebServer 某个 NSString* const 常量的**值**（不是符号名）。
//   与 OkaGWSKey 相同机制，只是名字不加 Option_ 前缀。
//   取不到返回 fallback，保证不崩。
static NSString *OkaGWSConst(const char *symbolName, NSString *fallback) {
    void *h = dlsym(RTLD_DEFAULT, symbolName);
    if (!h) {
        dlopen("@rpath/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
        dlopen("Frameworks/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
        h = dlsym(RTLD_DEFAULT, symbolName);
    }
    if (!h) return fallback;
    NSString *v = *(NSString * const *)h;
    return v.length ? v : fallback;
}

// ★ v1.5.0：给上传器注入 HTTP Basic 认证。
//   位置必须在 startWithOptions: 之前 —— 认证是**启动选项**，服务起来之后再改无效。
//   isKindOfClass 判定用 GCDWebUploader 这个具体类：
//     GCDWebDAVServer 是它的兄弟类（不是子类），账号密码由 App 自己生成并传入，
//     一旦被我们覆盖，WebDAV 客户端就会登录失败。这里显式排除，只认上传器。
static BOOL OkaIsUploader(id server) {
    Class upCls = NSClassFromString(@"GCDWebUploader");
    if (!upCls || !server) return NO;
    return [server isKindOfClass:upCls];
}

// 把认证三项塞进 options。密码为空时不改动任何东西（保持原版行为）。
// 键名走 OkaGWSKey：符号缺失退回裸字面量，与 Port / SuspendInBackground 同口径。
// 账号表格式是 GCDWebServer 规定的 username -> password 字典（明文，由服务端比对）。
static void OkaApplyWebAuth(id server, NSMutableDictionary *opts) {
    if (!opts) return;
    if (!gWebPassword.length) return;
    if (!OkaIsUploader(server)) return;

    static NSString *methodKey = nil;
    static NSString *realmKey  = nil;
    static NSString *acctKey   = nil;
    static NSString *basicVal  = nil;
    if (!methodKey) {
        methodKey = OkaGWSKey("AuthenticationMethod",   @"AuthenticationMethod");
        realmKey  = OkaGWSKey("AuthenticationRealm",    @"AuthenticationRealm");
        acctKey   = OkaGWSKey("AuthenticationAccounts", @"AuthenticationAccounts");
        // 认证方法常量的真实值（不是 "Basic" 字面量，必须 dlsym 读）。
        basicVal  = OkaGWSConst("GCDWebServerAuthenticationMethod_Basic", @"Basic");
    }
    if (!basicVal.length) return;

    NSString *user = gWebUser.length ? gWebUser : @"oka";
    opts[methodKey] = basicVal;
    opts[realmKey]  = @"Oka Files";
    opts[acctKey]   = @{ user: gWebPassword };
}

static BOOL (*orig_startWithOptions)(id, SEL, NSDictionary *, NSError **);
static BOOL my_startWithOptions(id self, SEL _cmd, NSDictionary *options, NSError **error) {
    if (!orig_startWithOptions) return NO;

    // ★ v1.4.1：关掉 GCDWebServer 的「进后台自动停服」。
    //   GCDWebServer 的 AutomaticallySuspendInBackground 默认 YES：App 一进后台
    //   （锁屏 / 切到别的 App）它自己就调用 stop。表现就是「WiFi 传输开着放着
    //   一会儿不管，自己就关了」，且关闭时刻约等于自动锁屏时间，不是空闲计时器。
    //   App 侧（BNFileSharingService）从未传过这个选项，用的是默认值，
    //   所以只能在这里补成 NO。键名沿用 OkaGWSKey：符号缺失时退回裸字面量。
    NSMutableDictionary *base = options ? [options mutableCopy]
                                       : [NSMutableDictionary dictionary];
    static NSString *suspendKey = nil;
    if (!suspendKey) suspendKey = OkaGWSKey("AutomaticallySuspendInBackground",
                                            @"AutomaticallySuspendInBackground");
    base[suspendKey] = @NO;
    OkaApplyWebAuth(self, base);

    NSDictionary *use = base;
    // ★ 只有显式开启 gEnableFixedPort 才覆盖端口。
    //   默认 NO -> 端口原样沿用 App 自己的 5426。
    if (gEnableFixedPort) {
        NSMutableDictionary *m = [base mutableCopy];
        static NSString *portKey = nil;
        if (!portKey) portKey = OkaGWSKey("Port", @"Port");
        m[portKey] = @(gFixedPort);
        use = m;
    }

    BOOL ok = orig_startWithOptions(self, _cmd, use, error);

    // 固定端口被别的进程占用时退一步：端口回到 App 原本的，但「关自动停服」保留。
    // 宁可地址回到随机端口（功能不丢），也不要开不起来。
    if (!ok && use != base) {
        ok = orig_startWithOptions(self, _cmd, base, error);
    }

    NSUInteger real = ((NSUInteger (*)(id, SEL))objc_msgSend)(self, @selector(port));
    if (real) gServerPort = real;
    if (ok) gServerRunning = YES;
    return ok;
}

// 停服时要把 gServerPort 清掉。
// 不清的后果很实际：用户开过一次 → App 里手动停掉 → 再长按快捷方式，
// 我们会因为 gServerPort != 0 误判"已在运行"，于是永远不再真正启动。
// （这个 bug 我是靠"顺序走一遍用户操作"推出来的，跑起来才会发现。）
static void (*orig_stop)(id, SEL);
static void my_stop(id self, SEL _cmd) {
    if (orig_stop) orig_stop(self, _cmd);
    gServerPort = 0;
    gServerRunning = NO;
}

// ★ v1.4.2：第二层保险 —— 接管 GCDWebServer 的「进后台就停服」处理器。
//   第一层是 my_startWithOptions 把 AutomaticallySuspendInBackground 注入成 NO；
//   但那依赖「符号能解析到、选项键没被改」，落空时是静默失效。
//   这里直接 hook 私有方法 -[GCDWebServer _didEnterBackground:]：
//   原实现只有一句「if (_suspendInBackground) [self stop]」。
//   把 _suspendInBackground 就地改成 NO 再交给原实现 —— 它看到 NO 就不停服，
//   其它逻辑照常执行。ivar 取不到时直接返回：这里的目标就是别停服，
//   所以不能"保守放行"去调原实现。
static void (*orig_didEnterBackground)(id, SEL, id);
static void my_didEnterBackground(id self, SEL _cmd, id note) {
    Class gwsCls = NSClassFromString(@"GCDWebServer");
    Ivar iv = gwsCls ? class_getInstanceVariable(gwsCls, "_suspendInBackground") : NULL;
    if (!iv) return;
    BOOL *flag = (BOOL *)((char *)(__bridge void *)self + ivar_getOffset(iv));
    *flag = NO;
    if (orig_didEnterBackground) orig_didEnterBackground(self, _cmd, note);
}

// 兜底算地址：自己的 IP + 记录到的端口
static NSString *FallbackServerURLString(void) {
    NSUInteger port = gServerPort ? gServerPort : gFixedPort;

    // 与 my_serverURL 保持同一口径：优先 mDNS 主机名，拿不到才退回 IP。
    if (gEnableFixedAddr) {
        NSString *mdnsHost = OkaFixedHost();
        if (mdnsHost.length) {
            return [NSString stringWithFormat:@"http://%@:%lu/",
                    mdnsHost, (unsigned long)port];
        }
    }

    NSString *ip = BestLocalIPAddress(NO);
    if (!ip) return nil;
    return [NSString stringWithFormat:@"http://%@:%lu/", ip, (unsigned long)port];
}

// ---------------------------------------------------------------------------
//  UI 层地址修正：不依赖 MSHookFunction / GCDWebServer 内部
// ---------------------------------------------------------------------------
//  为什么必须有这一层：
//    上面 hook _GCDWebServerGetPrimaryIPAddress 是「从源头修」，但它有两个
//    失效点任何一个命中都会让界面停留在占位文案（真机实测显示 "unknown"）：
//      1) MSHookFunction 在该 framework 上没真正打上桩（dlsym 找到符号
//         不等于桩已生效）；
//      2) App 显示地址时压根不走 serverURL，而是自己另算了一个状态文案。
//    而 serverURL 拿到 nil 就返回 nil（反汇编 0xEE94 / 0xEF3C 已确认），
//    App 把「没有地址」渲染成占位词。
//    所以这里做第二层：等 App 写完 ipAddress 之后，发现文案不对就直接改写。
//    这一层只看 UIKit 的 outlet，不碰 Swift Helper，不碰 GCDWebServer 符号。
// ---------------------------------------------------------------------------

// 直接读原始文本（URLTextFromPage 会过滤掉非 http 开头的，这里要读原样）
static NSString *RawAddressText(id page) {
    if (!page) return nil;
    id tv = GetIvar(page, "ipAddress");
    if (!tv) return nil;
    SEL s = @selector(text);
    if (![tv respondsToSelector:s]) return nil;
    id t = ((id (*)(id, SEL))objc_msgSend)(tv, s);
    return [t isKindOfClass:[NSString class]] ? (NSString *)t : nil;
}

// 从 "http://1.2.3.4:8080/" 里抠 host
static NSString *HostFromURLString(NSString *s) {
    if (!s.length) return nil;
    NSRange scheme = [s rangeOfString:@"://"];
    if (scheme.location == NSNotFound) return nil;
    NSString *rest = [s substringFromIndex:scheme.location + 3];
    NSRange slash = [rest rangeOfString:@"/"];
    if (slash.location != NSNotFound) rest = [rest substringToIndex:slash.location];
    NSRange colon = [rest rangeOfString:@":" options:NSBackwardsSearch];
    if (colon.location != NSNotFound) rest = [rest substringToIndex:colon.location];
    return rest.length ? rest : nil;
}

// 从现有文案里抠端口（App 可能用 startWithOptions:，端口 hook 没覆盖到）
static NSUInteger PortFromText(NSString *s) {
    if (!s.length) return 0;
    NSRange r = [s rangeOfString:@":" options:NSBackwardsSearch];
    if (r.location == NSNotFound || r.location + 1 >= s.length) return 0;
    NSString *tail = [s substringFromIndex:r.location + 1];
    NSUInteger v = 0;
    for (NSUInteger i = 0; i < tail.length; i++) {
        unichar c = [tail characterAtIndex:i];
        if (c < '0' || c > '9') break;
        v = v * 10 + (NSUInteger)(c - '0');
        if (v > 65535) return 0;
    }
    return v ? v : 0;
}

// ---------------------------------------------------------------------------
//  端口自证：优先用「实测 LISTEN」拿真实端口，但不再把它当硬门槛
// ---------------------------------------------------------------------------
//  v1.0.1 把 connect 探测设成不让步的前置条件，结果可能把修复卡死：
//  GCDWebServer 若只绑热点接口、或端口不是 8080/8081，探测全失败 → 直接不改。
//  v1.0.2 起 gServerPort / gServerRunning 由 serverURL hook 从实例直接读，
//  本身就是比 connect 更硬的证据；探测退化为「优先拿真实端口」的手段。
//  真正要防的只有一种情况：服务已停止、界面残留 unknown 时凭空填假地址，
//  这由 my_stop 清零 gServerPort / gServerRunning 来兜住。
static BOOL PortIsListeningAddr(struct in_addr a, NSUInteger port) {
    if (!port || port > 65535) return NO;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return NO;

    // loopback 上未监听的端口会立刻 ECONNREFUSED；仍设超时，避免个别接口
    // 把 SYN drop 掉时卡住主线程（这段跑在主队列）。
    struct timeval tv;
    tv.tv_sec = 0;
    tv.tv_usec = 120000;
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_len    = sizeof(addr);
    addr.sin_family = AF_INET;
    addr.sin_port   = htons((uint16_t)port);
    addr.sin_addr   = a;

    int rc = connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    close(fd);
    return rc == 0;
}

// 候选端口按可信度排序：start hook 记录 > App 文案里抠出来的 > 原版 5426 > 8080/8081（历史兼容）。
// 回环 + 对外 IP 各试一次：GCDWebServer 可能绑 0.0.0.0，也可能只绑热点接口。
static NSUInteger ResolveLivePort(NSString *cur, NSString *localIP) {
    struct in_addr lo, lan;
    memset(&lo, 0, sizeof(lo));
    memset(&lan, 0, sizeof(lan));
    inet_pton(AF_INET, "127.0.0.1", &lo);
    BOOL hasLan = (localIP.length && inet_pton(AF_INET, [localIP UTF8String], &lan) == 1);

    NSUInteger cand[5];
    int n = 0;
    if (gServerPort && gServerPort <= 65535) cand[n++] = gServerPort;
    NSUInteger fromText = PortFromText(cur);
    if (fromText && fromText != gServerPort) cand[n++] = fromText;
    if (gFixedPort != gServerPort && gFixedPort != fromText) cand[n++] = gFixedPort;
    if (8080 != gServerPort && 8080 != fromText && 8080 != gFixedPort) cand[n++] = 8080;
    if (8081 != gServerPort && 8081 != fromText && 8081 != gFixedPort) cand[n++] = 8081;

    for (int i = 0; i < n; i++) {
        if (PortIsListeningAddr(lo, cand[i]) ||
            (hasLan && PortIsListeningAddr(lan, cand[i]))) {
            return cand[i];
        }
    }
    // 探测不到 ≠ 服务没跑。serverURL hook 读到的实例 port / isRunning
    // 是直接证据，有其一就允许写回（用户实测「传输功能一直正常」）。
    if (gServerPort) {
        return gServerPort;
    }
    if (gServerRunning) {
        return gFixedPort;
    }
    return 0;
}

// 什么算「地址坏了」：空 / 占位词 / 不是 http / host 不是合法 IP
static BOOL AddressLooksBroken(NSString *s) {
    if (!s.length) return YES;
    NSString *low = [s lowercaseString];
    if ([low rangeOfString:@"unknown"].location != NSNotFound) return YES;
    if ([low rangeOfString:@"(null)"].location != NSNotFound) return YES;
    if ([low rangeOfString:@"http://"].location != 0) return YES;
    NSString *host = HostFromURLString(s);
    if (!host) return YES;
    if ([low rangeOfString:@"localhost"].location != NSNotFound) return YES;
    // ★ v1.2.0：mDNS 主机名是合法地址，不能判成「坏地址」再被改回 IP，
    //   否则兜底逻辑会每秒把固定地址覆盖掉。
    if ([host.lowercaseString hasSuffix:@".local"]) return NO;
    return !IsPlausibleIPAddress(host);
}

// ---------------------------------------------------------------------------
//  ★ v1.3.0 备用行：把原版可用的 IP 地址永远留在屏幕上
// ---------------------------------------------------------------------------
//  mDNS（<设备名>.local）不是万能的：Windows 老系统没装 Bonjour、部分网络
//  禁组播时，固定地址解析不到。原版用 http://<IP>:5426 一直可用，用户明确
//  要求「不要去掉我输入网址能访问网页的功能」。
//  ipAddress 是 UITextView，支持换行，所以两行都放得下：
//      第一行  固定地址（主）
//      第二行  备用：http://<当前IP>:<端口>/（原版方式）
//  复制时由 URLTextFromPage 只取第一行，不会把"备用："带进剪贴板。

// 探测成功后强制重写一次地址：此时 App 已写过 IP，普通路径（只在地址坏时改）
// 不会覆盖它，所以这里直接按「mDNS 主行 + IP 备用行」重写。
static void OkaRefreshVisiblePage(void) {
    id page = gLastPage;
    if (!page || !gEnableFixedAddr) return;
    id tv = GetIvar(page, "ipAddress");
    if (!tv) return;
    NSString *ip = BestLocalIPAddress(NO);
    if (!ip.length) return;
    NSString *host = OkaFixedHost();
    if (!host.length) return;

    SEL getText = @selector(text);
    if (![tv respondsToSelector:getText]) return;
    NSString *cur = ((id (*)(id, SEL))objc_msgSend)(tv, getText);
    if (![cur isKindOfClass:[NSString class]] || !cur.length) return;
    NSUInteger port = ResolveLivePort(cur, ip);
    if (!port) return;                     // 服务没在跑，不造假地址

    NSString *primary = [NSString stringWithFormat:@"http://%@:%lu/",
                         host, (unsigned long)port];
    // ★ v1.4.0：只写主行（理由同上）
    NSString *text = primary;
    if ([text isEqualToString:cur]) return;

    SEL setText = @selector(setText:);
    if (![tv respondsToSelector:setText]) return;
    ((void (*)(id, SEL, id))objc_msgSend)(tv, setText, text);
}

// 在 App 写完地址之后调用（v1.0.2 起降为**兜底**，主力是 my_serverURL）。
// 三个条件：① 地址本身是坏的  ② 枚举得到本机 IP  ③ 端口/运行状态有证据。
// 绝不覆盖 WiFi 下本来就正常的地址，也绝不在停止服务后凭空填地址。
static void RewriteAddressIfNeeded(id page) {
    if (!page || !gEnableIPFix) return;
    id tv = GetIvar(page, "ipAddress");
    if (!tv) return;

    NSString *cur = RawAddressText(page);
    if (!AddressLooksBroken(cur)) return;          // 正常，别动

    NSString *ip = BestLocalIPAddress(NO);
    if (!ip) { return; }

    NSUInteger port = ResolveLivePort(cur, ip);
    if (!port) return;   // 服务没在跑：unknown / 空白是正确状态，不造假地址

    NSUInteger livePort = port;      // port 已被 ResolveLivePort 确定为真实监听端口

    // 主行：固定地址优先（与 my_serverURL 同口径），拿不到主机名才退回 IP。
    NSString *primary = [NSString stringWithFormat:@"http://%@:%lu/",
                         ip, (unsigned long)livePort];
    if (gEnableFixedAddr) {
        NSString *h = OkaFixedHost();
        if (h.length) {
            primary = [NSString stringWithFormat:@"http://%@:%lu/",
                       h, (unsigned long)livePort];
        }
    }

    // ★ v1.4.0：只写主行。控件实测只能画一行（UILabel + 固定高度），
    //   写第二行只会被裁掉；IP 地址改由「WiFi 传输已开启」弹窗给出。
    NSString *text = primary;

    SEL setText = @selector(setText:);
    if ([tv respondsToSelector:setText]) {
        ((void (*)(id, SEL, id))objc_msgSend)(tv, setText, text);
    }
}

// ★ 不 hook viewDidAppear: —— class_getInstanceMethod 会沿继承链查找，
//   若 BNHttpServerPage 没有自己 override，method_setImplementation 会改到
//   UIViewController 上，污染整个 App 的所有页面。改用已验证安全的
//   my_viewDidLoad（见下方）挂一次校正。
// hook: changeWifiTransferStateAction: —— 页面自己实现的方法，hook 安全
//   启动是异步的（0x100013cdc 进全局队列），App 会在稍后写 ipAddress。
//   所以延迟两拍各校正一次；RewriteAddressIfNeeded 自带幂等判断，
//   地址正常时不会反复写。
//   这个方法同时也是「停止」的入口。停止后端口不再 LISTEN，
//   ResolveLivePort 探测不到就不会改写，不会凭空填一个假地址。
static void (*orig_changeWiFiState)(id, SEL, id);
static void my_changeWiFiState(id self, SEL _cmd, id sender) {
    if (orig_changeWiFiState) orig_changeWiFiState(self, _cmd, sender);
    __weak id weakSelf = self;
    // ★ v1.3.0：先修坏地址，再补备用行。顺序不能反 ——
    //   顺序反了会出现「先追加备用行、随后整段被判为坏地址再被改写」的来回跳。
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.6 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakSelf) {
            RewriteAddressIfNeeded(weakSelf);
        }
    });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.8 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakSelf) {
            RewriteAddressIfNeeded(weakSelf);
        }
    });
    // ★ v1.3.1：服务器启动是异步的，App 有时在 2s 之后才写 ipAddress，
    //   把已经补好的两行覆盖回一行。第三拍专治这个。
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3.2 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakSelf) {
            RewriteAddressIfNeeded(weakSelf);
        }
    });
}

// 弹出结果提示
// ★ v1.4.0：两个地址一起给 ——
//   · IP 地址（弹窗里显示、并复制到剪贴板）：任何系统/网络都能连，最稳；
//   · 固定地址（<设备名>.local）：换网络不用改地址，由 WiFi 传输页显示。
//   这样不依赖页面控件能否画两行，两个地址用户都看得到。
static void ShowResultAlert(NSString *fixedURL, NSString *ipURL, BOOL copied) {
    UIWindow *win = KeyWindow();
    UIViewController *vc = win.rootViewController;
    while (vc.presentedViewController) vc = vc.presentedViewController;
    if (!vc) return;

    NSMutableArray *lines = [NSMutableArray array];
    if (ipURL.length) [lines addObject:ipURL];
    if (fixedURL.length && ![fixedURL isEqualToString:ipURL]) {
        [lines addObject:[NSString stringWithFormat:@"固定地址：%@", fixedURL]];
    }
    if (!lines.count) [lines addObject:@"已在后台开启，请在 App 的 WiFi 传输页查看地址"];

    NSString *msg = [lines componentsJoinedByString:@"\n"];
    if (copied && ipURL.length) msg = [msg stringByAppendingString:@"\n\n（IP 已复制到剪贴板）"];

    UIAlertController *ac = [UIAlertController alertControllerWithTitle:@"WiFi 传输已开启"
                                                              message:msg
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [ac addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleCancel handler:nil]];
    if (ipURL.length) {
        [ac addAction:[UIAlertAction actionWithTitle:@"再复制 IP" style:UIAlertActionStyleDefault
                                            handler:^(UIAlertAction *a) {
            UIPasteboard.generalPasteboard.string = ipURL;
        }]];
    }
    if (fixedURL.length && ![fixedURL isEqualToString:ipURL]) {
        [ac addAction:[UIAlertAction actionWithTitle:@"复制固定地址" style:UIAlertActionStyleDefault
                                            handler:^(UIAlertAction *a) {
            UIPasteboard.generalPasteboard.string = fixedURL;
        }]];
    }
    [vc presentViewController:ac animated:YES completion:nil];
}

// 主流程：开启 WiFi 传输
static void StartWiFiTransfer(BOOL fromShortcut) {
    UIWindow *win = KeyWindow();
    UIViewController *page = FindPage(win.rootViewController, 0);
    if (!page) page = MakeHeadlessPage();

    if (!page) {
        if (fromShortcut) ShowResultAlert(nil, nil, NO);
        return;
    }

    // 已经开着就别再点一次（再点一次是"停止"，会把用户正在传的文件中断）
    NSString *existing = URLTextFromPage(page);
    BOOL portLive = (gServerPort != 0);
    if (existing.length || portLive) {
        NSString *s = existing.length ? existing : FallbackServerURLString();
        // ★ 复制给用户的是 IP：它一定能连；.local 在老系统/热点下可能解析不了。
        NSString *ipURL = OkaIPURLString();
        if (fromShortcut && gCopyURLOnShortcut && ipURL.length)
            UIPasteboard.generalPasteboard.string = ipURL;
        if (fromShortcut) ShowResultAlert(s, ipURL, gCopyURLOnShortcut && ipURL.length);
        // 即便是"已开启"也要确保保活挂着（上次可能是手动开的）
        if (fromShortcut) StartBackgroundKeepAlive();
        return;
    }

    SEL toggle = NSSelectorFromString(@"changeWifiTransferStateAction:");
    if (![page respondsToSelector:toggle]) {
        if (fromShortcut) ShowResultAlert(nil, nil, NO);
        return;
    }
    ((void (*)(id, SEL, id))objc_msgSend)(page, toggle, nil);

    // 如果是快捷方式启动的，用户在浏览器里传文件期间 App 会一直被切到后台，
    // 必须立刻挂上保活，否则进程被挂起、端口就断了。
    if (fromShortcut) StartBackgroundKeepAlive();

    if (!fromShortcut) return;

    // 启动是异步的（0x100013cdc blr 进全局队列），等一会再读地址。
    __weak id weakPage = page;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.5 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        id p = weakPage;
        // 优先用 App 自己写进 ipAddress 的地址（它走的是 serverURL，最权威）
        NSString *s = URLTextFromPage(p);
        if (!s.length) s = FallbackServerURLString();
        NSString *ipURL = OkaIPURLString();
        if (ipURL.length && gCopyURLOnShortcut) UIPasteboard.generalPasteboard.string = ipURL;
        ShowResultAlert(s, ipURL, gCopyURLOnShortcut && ipURL.length);
    });
}

// --- AppDelegate hooks -----------------------------------------------------
static BOOL (*orig_didFinishLaunching)(id, SEL, UIApplication *, NSDictionary *);
static BOOL my_didFinishLaunching(id self, SEL _cmd, UIApplication *app, NSDictionary *opts) {
    BOOL r = orig_didFinishLaunching ? orig_didFinishLaunching(self, _cmd, app, opts) : YES;
    if (!gEnableShortcut) return r;

    NSMutableArray *items = [NSMutableArray array];
    if (app.shortcutItems) [items addObjectsFromArray:app.shortcutItems];

    BOOL exists = NO;
    for (UIApplicationShortcutItem *it in items) {
        if ([it.type isEqualToString:SHORTCUT_TYPE]) { exists = YES; break; }
    }
    if (!exists) {
        [items insertObject:MakeShortcutItem() atIndex:0];
        app.shortcutItems = items;
    }
    return r;
}

// App 自身没有实现 performActionForShortcutItem:，由我们补上
static void my_performAction(id self, SEL _cmd, UIApplication *app,
                            UIApplicationShortcutItem *item, void (^completion)(BOOL)) {
    if (item && [item.type isEqualToString:SHORTCUT_TYPE]) {
        StartWiFiTransfer(YES);
        if (completion) completion(YES);
        return;
    }
    if (completion) completion(NO);
}

// ===========================================================================
//  四、安装
// ===========================================================================
// outOrig 用 void ** 而不是 IMP *：
//   调用方持有的是 `BOOL (*)(id, SEL, ...)` 这类**具体签名**的函数指针，
//   `(IMP *)&orig_xxx` 是一次有损强转（IMP 是 `void (*)(void)`），
//   clang 在 -Wall 下会报 -Wincompatible-pointer-types；
//   更麻烦的是它对函数指针取二级指针，属于 strict aliasing 灰色地带。
//   参数类型改成 void ** 后 `&orig_xxx` 依然是 `BOOL(**)(...)` → void** 的转换，
//   但这一步在 C 里是常见的 out-parameter 写法，且我们只做一次写入，
//   不通过它回读，不存在 aliasing 问题。
//   （MSHookFunction 的第三个参数本身就是 void **，这里保持同一约定。）
static void HookInstanceMethod(Class cls, SEL sel, IMP newImp, void **outOrig, const char *tag __unused) {
    if (!cls) return;
    Method m = class_getInstanceMethod(cls, sel);
    if (!m) { return; }
    if (outOrig) *outOrig = (void *)method_getImplementation(m);
    method_setImplementation(m, newImp);
}

// ===========================================================================
//  三、v1.1.0 电脑网页端增强（拖放上传 / 批量移动）
// ===========================================================================
//  不改 App 包内 GCDWebUploader.bundle 的任何文件 —— 那会破坏代码签名。
//  GCDWebUploader 用 +[GCDWebServerDataResponse responseWithHTMLTemplate:variables:]
//  渲染 index.html，模板里有 %epilogue% 占位符，值来自 variables[@"epilogue"]。
//  这里在 variables 层追加 <style> + <script>，随首页 HTML 一起下发。
//
//  为什么两个方法都 hook：
//    · 类方法 responseWithHTMLTemplate:variables: 是常规入口；
//    · 其内部通常转 -initWithHTMLTemplate:variables:，App 若直接走实例方法也要覆盖。
//    两者同时命中时用 epilogue 里的 OKA-WIFI-INJECTED 标记保证只注入一次。
//
//  作用域：只有带 epilogue key、或模板路径是 index.html 的响应才被改。
//  就算误命中别的页面，增强 JS 找不到 #listing / .btn-toolbar 会自行降级，
//  不会抛异常，也不会动原页面任何元素。
// ===========================================================================

static NSString *OkaWebInjectionHTML(void) {
    static NSString *html = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        NSString *js  = [NSString stringWithUTF8String:kOkaWebJS];
        NSString *css = [NSString stringWithUTF8String:kOkaWebCSS];
        // gen-webassets.py 已做往返解码自检；这里仍对解码失败做兜底，
        // 避免把空脚本下发出去、让页面看起来像"增强没生效"。
        if (!js)  js  = @"";
        if (!css) css = @"";
        // 全部用 NSString 字面量拼接（@"..." 与 "..." 混用 clang 会报错）。
        // JS/CSS 作为参数传入，不参与格式串解析，其中的 % 不会被当格式符。
        html = [NSString stringWithFormat:
                @"\n<!-- OKA-WIFI-INJECTED -->\n"
                @"<style type=\"text/css\" id=\"oka-wifi-css\">%@</style>\n"
                @"<script type=\"text/javascript\" id=\"oka-wifi-js\">%@</script>\n",
                css, js];
    });
    return html;
}

static NSDictionary *OkaAugmentVariables(NSString *path, NSDictionary *vars) {
    if (!gEnableWebEnhance) return vars;
    if (![vars isKindOfClass:[NSDictionary class]]) return vars;

    id ep = [vars objectForKey:@"epilogue"];
    BOOL isIndex = [path isKindOfClass:[NSString class]] &&
                   [[path lastPathComponent] isEqualToString:@"index.html"];
    if (!isIndex && ![ep isKindOfClass:[NSString class]]) return vars;

    if ([ep isKindOfClass:[NSString class]] &&
        [(NSString *)ep rangeOfString:@"OKA-WIFI-INJECTED"].location != NSNotFound) {
        return vars;   // 类方法 + 实例方法双 hook 命中，只注入一次
    }

    NSMutableDictionary *m = [vars mutableCopy];
    NSString *base = [ep isKindOfClass:[NSString class]] ? (NSString *)ep : @"";
    [m setObject:[base stringByAppendingString:OkaWebInjectionHTML()]
          forKey:@"epilogue"];
    return m;
}

static id (*orig_responseHTMLTemplate)(id, SEL, NSString *, NSDictionary *);
static id my_responseHTMLTemplate(id self, SEL _cmd, NSString *path, NSDictionary *vars) {
    return orig_responseHTMLTemplate(self, _cmd, path,
                                     OkaAugmentVariables(path, vars));
}

static id (*orig_initHTMLTemplate)(id, SEL, NSString *, NSDictionary *);
static id my_initHTMLTemplate(id self, SEL _cmd, NSString *path, NSDictionary *vars) {
    return orig_initHTMLTemplate(self, _cmd, path,
                                 OkaAugmentVariables(path, vars));
}

static void HookClassMethod(Class cls, SEL sel, IMP newImp, void **outOrig, const char *tag __unused) {
    if (!cls) return;
    Method m = class_getClassMethod(cls, sel);
    if (!m) { return; }
    if (outOrig) *outOrig = (void *)method_getImplementation(m);
    method_setImplementation(m, newImp);
}

// ===========================================================================
//  四、v1.1.8 内联读取接口 GET /oka-raw
//      v1.1.9 目录时间接口 GET /oka-stat（列表显示最后修改时间）
// ===========================================================================
//  【为什么必须动服务端】
//  用户实测三个问题，全部来自同一个根因：
//
//    (1) 点下载按钮要等很久、浏览器报「已阻止不安全的下载」，要手动点保留
//    (2) PDF 点预览报「未能加载 PDF 文档」，但 IDM 弹出了下载窗口
//    (3) 点下载文件夹，IDM 弹出很多个窗口，每个文件一个
//
//  /download 的响应是 `responseWithFile:isAttachment:YES`，它一定带：
//      Content-Disposition: attachment; filename="..."
//  于是：
//    (1) 前端用 `window.location = 'download?...'` 做**顶层导航**去下载。
//        这是一种「页面跳转触发的下载」，在 http://172.20.10.1:8080 这种
//        明文局域网源上会被 Chrome 的安全下载策略判定为「不安全下载」并拦截，
//        Chrome 还要先把它读完才知道是不是危险类型 —— 这就是「反应半天」。
//    (2) 预览用 XHR 取字节本身没问题，但 IDM 的浏览器集成监听的是
//        Content-Disposition: attachment 的响应，一旦看到就抢走它。
//        抢走的后果是 iframe 拿到的 Blob 不完整 → 「未能加载 PDF 文档」。
//    (3) 目录 ZIP 是前端逐文件发 XHR 取字节。每个响应都带 attachment，
//        IDM 把每一条都当成一次下载任务 → 一个目录 N 个文件弹 N 个窗。
//
//  【解法】
//  新增 GET /oka-raw?path=<相对路径>，用 `isAttachment:NO` 返回同一份文件。
//
//  ★ 注意 isAttachment:NO 的真实语义（v1.1.9 实测更正，别再照抄旧描述）：
//    它不是「发 Content-Disposition: inline」，而是**根本不发这个响应头**。
//    证据两重：
//      ① GCDWebServer.framework 二进制里 `inline` 出现 0 次，
//         `Content-Disposition` 只出现 1 次，值是
//         `attachment; filename="%@"; filename*=UTF-8''%@`（纯 attachment 分支）；
//      ② 上游 GCDWebServerFileResponse.m 是
//         `if (attachment) { ... setValue:... forAdditionalHeader:@"Content-Disposition" }`
//         —— NO 时该分支整个跳过。
//    行为上这正好是我们想要的：没有 Content-Disposition，IDM 不当下载任务接管，
//    Chrome 也不走「不安全下载」判定（它就是一次普通 XHR 响应）。
//    旧注释写的 `Content-Disposition: inline; filename="..."` 是错的，已改。
//
//  【为什么不直接把 /download 改成 inline】
//  /download 是原版页面「点文件名下载」在用的接口，也是别的客户端
//  （第三方工具、脚本）依赖的语义。改它的行为属于破坏性变更：
//  在 Safari / 手机浏览器上 inline 会让 PDF 直接打开而不是存盘。
//  新开一个路径，原路径行为一字不动 —— 出问题只需要不注册这个 handler。
//
//  【接口安全性】
//  路径必须落在 uploadDirectory 之内。这里不自己拼字符串判断前缀
//  （`/a/b` 与 `/a/bc` 同前缀会误判），而是：
//    1) 先经 _GCDWebServerNormalizePath 消掉 ../ 与重复斜杠；
//    2) 拼出绝对路径后取 standardized 形式；
//    3) 用「等于根目录 或 以 根目录 + '/' 开头」判断，且拒绝根目录本身。
//  最后再检查是普通文件（非目录、非符号链接指向外部）。
// ===========================================================================

//  GCDWebServer 的错误响应构造。签名：
//      +[GCDWebServerErrorResponse responseWithClientError:message:]
//  message 是格式串，这里只传不含 % 的固定串，不会被当格式符解析。
static id OkaClientError(NSInteger code, NSString *msg) {
    Class errCls = NSClassFromString(@"GCDWebServerErrorResponse");
    SEL sel = NSSelectorFromString(@"responseWithClientError:message:");
    if (!errCls || ![errCls respondsToSelector:sel]) { return nil; }
    typedef id (*OkaErr_t)(id, SEL, NSInteger, NSString *);
    return ((OkaErr_t)(void *)objc_msgSend)((id)errCls, sel, code, msg);
}

// 取 GCDWebUploader 的上传根目录。
// 用 -[GCDWebUploader uploadDirectory]（R,N 只读属性，二进制里已确认存在），
// 不读 ivar 偏移 —— 属性是 ObjC 层契约，App 升级重排版也不会失效。
static NSString *OkaUploadDirectoryOf(id server) {
    if (!server) { return nil; }
    SEL sel = NSSelectorFromString(@"uploadDirectory");
    if (![server respondsToSelector:sel]) { return nil; }
    id dir = ((id (*)(id, SEL))objc_msgSend)(server, sel);
    return [dir isKindOfClass:[NSString class]] ? (NSString *)dir : nil;
}

// 从 request 的 query 里取 path 参数（原样返回，不做归一化）。
// /oka-raw 与 /oka-stat 共用：两者必须按同一个语义解析 path。
static NSString *OkaQueryPath(id request) {
    SEL selQuery = NSSelectorFromString(@"query");
    if (![request respondsToSelector:selQuery]) { return nil; }
    id q = ((id (*)(id, SEL))objc_msgSend)(request, selQuery);
    if (![q isKindOfClass:[NSDictionary class]]) { return nil; }
    id v = [(NSDictionary *)q objectForKey:@"path"];
    return [v isKindOfClass:[NSString class]] ? (NSString *)v : nil;
}

// 把 query 里的相对 path 解析成上传根目录内的绝对路径。
// 返回 nil = 失败，*outCode 带回 HTTP 状态码：
//   400 缺 path 参数 / 403 越界 / 404 不存在 / 500 拿不到上传根。
//
// ★ 围栏逻辑只在这里实现一次，/oka-raw 与 /oka-stat 共用。
//   两份拷贝慢慢长歪是经典事故：改了一处忘了另一处，于是「一个接口拦住、
//   另一个接口放行目录穿越」。共用之后没有第二种可能。
static NSString *OkaResolveInRoot(id server, id request,
                                  NSInteger *outCode, BOOL *outIsDir) {
    if (outCode) { *outCode = 0; }

    NSString *root = OkaUploadDirectoryOf(server);
    if (root.length == 0) { if (outCode) { *outCode = 500; } return nil; }

    NSString *rel = OkaQueryPath(request);
    if (rel.length == 0) { if (outCode) { *outCode = 400; } return nil; }

    // ★ 不依赖 GCDWebServer 内部的 _GCDWebServerNormalizePath：
    //   那是 framework 私有 C 符号，有没有导出不确定，用 -undefined
    //   dynamic_lookup 引用一个不存在的符号会在首次调用时直接崩。
    //   这里用 Foundation 的 stringByStandardizingPath 做词法归一化
    //   （消掉 ./ 与 ../ 以及重复斜杠），足够挡住目录穿越；
    //   符号链接不在防护范围内（原版 download 同样不挡，保持一致）。
    //
    // ★ 方法名是 stringByStandardizingPath，不是 standardizingPath ——
    //   后者是 Swift 那边的属性名，ObjC 里没有这个 selector，
    //   写了会在编译期报 "no visible @interface declares the selector"。
    //   去掉前导斜杠再拼：避免 root 末尾与 rel 开头各一个 '/' 拼出 '//'。
    NSString *relClean = rel;
    while ([relClean hasPrefix:@"/"]) {
        relClean = [relClean substringFromIndex:1];
    }
    NSString *abs = [[root stringByAppendingPathComponent:relClean]
                     stringByStandardizingPath];
    NSString *rootStd = [root stringByStandardizingPath];

    // ★ 围栏判断必须带路径分隔符，不能用 abs hasPrefix:rootStd 裸判：
    //   根目录是 /a/b 时，/a/bc 也满足 hasPrefix(/a/b)，越界读就成立了。
    //   这里要求「完全等于根」或「以 根 + '/' 开头」。
    NSString *rootSlash = [rootStd hasSuffix:@"/"] ? rootStd
                                                   : [rootStd stringByAppendingString:@"/"];
    BOOL inside = [abs isEqualToString:rootStd] || [abs hasPrefix:rootSlash];
    if (!inside) { if (outCode) { *outCode = 403; } return nil; }

    BOOL isDir = NO;
    if (![[NSFileManager defaultManager] fileExistsAtPath:abs isDirectory:&isDir]) {
        if (outCode) { *outCode = 404; } return nil;
    }
    if (outIsDir) { *outIsDir = isDir; }
    return abs;
}

// /oka-raw 的实际处理：把 path 解析成上传根目录内的绝对路径，用
// isAttachment:NO 返回。注意 NO 的实际效果是**不发 Content-Disposition 头**
// （不是发 inline，见上面 【解法】 里更正过的说明）。
static id OkaRawFileResponse(id server, id request) {
    NSInteger code = 0;
    BOOL isDir = NO;
    NSString *abs = OkaResolveInRoot(server, request, &code, &isDir);
    if (!abs) {
        if (code == 400) { return OkaClientError(400, @"missing path"); }
        if (code == 403) { return OkaClientError(403, @"invalid path"); }
        if (code == 404) { return OkaClientError(404, @"not found"); }
        return OkaClientError(500, @"no upload directory");
    }
    // 目录不在这里返回内容：目录交给前端的 ZIP 打包（它自己递归 list）。
    if (isDir) { return OkaClientError(400, @"is a directory"); }

    // ★★ 这一行就是三个问题的共同解药：
    //    isAttachment:NO → 响应不带 Content-Disposition 头
    //    （不是「带 inline」，见文件上方【解法】里的实测更正）。
    //    不带该头的响应：IDM 不当成下载任务抢走；Chrome 也不走
    //    「已阻止不安全的下载」判定，因为它只是普通 XHR 响应。
    Class fileResp = NSClassFromString(@"GCDWebServerFileResponse");
    SEL sel = NSSelectorFromString(@"responseWithFile:isAttachment:");
    if (!fileResp || ![fileResp respondsToSelector:sel]) { return nil; }
    typedef id (*OkaFileResp_t)(id, SEL, NSString *, BOOL);
    return ((OkaFileResp_t)(void *)objc_msgSend)((id)fileResp, sel, abs, NO);
}

// /oka-stat 的实际处理：返回**一个目录下所有条目**的最后修改时间。
//
//  【为什么要新开接口而不是改 list】
//  原版 listDirectory: 的返回项只有 path / name / size 三个键，没有时间字段
//  （已用 GCDWebUploader.m 第 246-262 行逐行核对）。那个 JSON 结构是原版
//  页面的契约，往里加键虽然一般无害，但属于改原版接口语义；新开一个只读
//  接口更安全，出问题不注册即可，原版行为一字不动。
//
//  【为什么按目录批量而不是按文件】
//  列表一屏几十上百行，按文件逐个请求会把页面拖垮（N 次往返）。
//  这里一次返回整个目录的 {相对path: unix秒} 映射，前端只发一次请求。
//
//  【key 必须与 list 的 path 字段逐字节一致】
//  原版 list 用 [relativePath stringByAppendingPathComponent:item] 拼 path，
//  目录再补一个 "/"；这里照同样的方式拼，前端才能用 $(tr).data("path") 直接查表。
static id OkaStatResponse(id server, id request) {
    NSInteger code = 0;
    BOOL isDir = NO;
    NSString *abs = OkaResolveInRoot(server, request, &code, &isDir);
    if (!abs) {
        if (code == 400) { return OkaClientError(400, @"missing path"); }
        if (code == 403) { return OkaClientError(403, @"invalid path"); }
        if (code == 404) { return OkaClientError(404, @"not found"); }
        return OkaClientError(500, @"no upload directory");
    }
    if (!isDir) { return OkaClientError(400, @"is not a directory"); }

    NSString *rel = OkaQueryPath(request);
    NSFileManager *fm = [NSFileManager defaultManager];
    NSArray *items = [fm contentsOfDirectoryAtPath:abs error:NULL];
    // 目录读不出来（权限等）就回空表，而不是报错：
    // 前端拿不到时间只该少显示一列，不该把整个列表打成失败。
    if (![items isKindOfClass:[NSArray class]]) { items = nil; }

    NSUInteger cap = items ? [items count] : 0;
    NSMutableDictionary *out = [NSMutableDictionary dictionaryWithCapacity:cap];
    for (NSString *item in items) {
        if (![item isKindOfClass:[NSString class]] || item.length == 0) { continue; }
        NSString *childAbs = [abs stringByAppendingPathComponent:item];
        NSDictionary *attr = [fm attributesOfItemAtPath:childAbs error:NULL];
        if (![attr isKindOfClass:[NSDictionary class]]) { continue; }
        NSDate *mtime = [attr objectForKey:NSFileModificationDate];
        if (![mtime isKindOfClass:[NSDate class]]) { continue; }

        BOOL childIsDir = [[attr objectForKey:NSFileType]
                           isEqualToString:NSFileTypeDirectory];
        NSString *key = [rel stringByAppendingPathComponent:item];
        if (childIsDir) { key = [key stringByAppendingString:@"/"]; }

        // 用 numberWithDouble 而不是 @( ... ) 装箱表达式：
        // .xm 先过 Logos 预处理，少一层语法就不必赌它的解析边界。
        [out setObject:[NSNumber numberWithDouble:[mtime timeIntervalSince1970]]
                forKey:key];
    }

    // responseWithJSONObject: 是 GCDWebServerDataResponse 的 Extensions
    // 分类方法（二进制里确认存在 6 处），不是主类原生接口，所以仍要
    // respondsToSelector 兜一层：拿不到就返回 nil，前端按「接口不可用」处理。
    Class dataCls = NSClassFromString(@"GCDWebServerDataResponse");
    SEL selJson = NSSelectorFromString(@"responseWithJSONObject:");
    if (!dataCls || ![dataCls respondsToSelector:selJson]) { return nil; }
    typedef id (*OkaJSONResp_t)(id, SEL, id);
    return ((OkaJSONResp_t)(void *)objc_msgSend)((id)dataCls, selJson, out);
}

// handler 只注册一次。reload 插件、App 反复 init uploader 都可能重复进入，
// 重复 addHandlerForMethod 会让 GCDWebServer 抛「handler already registered」。
//
// ★ 用 NSHashTable 弱引用存 server，不能用 `%p` 指针串当 key：
//   旧 server 释放后新 server 完全可能拿到同一地址，那样第二个实例
//   会被误判成「已注册」而跳过，接口就静默失效了。
static NSHashTable *gOkaRawRegistered = nil;

// 注册两个只读接口：GET /oka-raw（取字节）与 GET /oka-stat（取修改时间）。
//
// 两个接口各自 addHandler，各用**独立的 block**，不要用同一个 block 靠
// request.path 分派：GCDWebServer 的 processBlock 签名不含 path，block 里
// 拿不到「这次是哪个路由命中」，只能从 request 反查 —— 多一层猜测，
// handler 是分别注册的，直接分开写就没有这个问题。
static void OkaRegisterRawHandler(id server) {
    if (!server) { return; }

    SEL selAdd = NSSelectorFromString(@"addHandlerForMethod:path:requestClass:processBlock:");
    if (![server respondsToSelector:selAdd]) { return; }
    Class reqCls = NSClassFromString(@"GCDWebServerRequest");
    if (!reqCls) { return; }

    if (!gOkaRawRegistered) {
        gOkaRawRegistered = [NSHashTable weakObjectsHashTable];
    }
    if ([gOkaRawRegistered containsObject:server]) { return; }

    typedef void (*OkaAddHandler_t)(id, SEL, NSString *, NSString *, Class, id);
    OkaAddHandler_t fn = (OkaAddHandler_t)(void *)objc_msgSend;

    // ★ block 必须用 block 类型声明，不能写 `id block = ^...`。
    //   ARC 只在「赋给 block 类型变量」时把栈上的 block 拷到堆上；
    //   赋给 `id` 变量不会 copy，函数返回后栈帧消失，handler 表里留下
    //   一个悬垂 block。GCDWebServer 内部虽然会 [processBlock copy]，
    //   但依赖「被调方一定 copy」是隐式契约，这里自己把类型写对。
    //
    //   block 弱引用 server：handler 表由 server 自己持有，强捕获会形成
    //   server → handler → block → server 的环，server 永远不释放。
    typedef id (^OkaProcessBlock_t)(id);
    __weak id weakServer = server;

    OkaProcessBlock_t rawBlock = ^id(id request) {
        id strong = weakServer;
        if (!strong) { return OkaClientError(500, @"server gone"); }
        return OkaRawFileResponse(strong, request);
    };

    OkaProcessBlock_t statBlock = ^id(id request) {
        id strong = weakServer;
        if (!strong) { return OkaClientError(500, @"server gone"); }
        return OkaStatResponse(strong, request);
    };

    @try {
        fn(server, selAdd, @"GET", @"/oka-raw", reqCls, (id)rawBlock);
        fn(server, selAdd, @"GET", @"/oka-stat", reqCls, (id)statBlock);
        [gOkaRawRegistered addObject:server];
    } @catch (NSException *e) {
        // 注册失败不能让 App 崩。
        // 这里刻意不输出任何诊断日志：v1.1.3 起产物里禁止日志调用与前缀常量，
        // build.sh 对源码与产物做双向负向检查，写了会让整个构建失败。
        //
        // ★ 两个 addHandler 放在同一个 @try 里是有意的：/oka-stat 在
        //   /oka-raw 之后注册，前一个抛异常时后一个也跟着跳过。
        //   失败面收窄成「两个都没有」，前端就能用同一条降级判据处理
        //   （探测到没有就不显示时间列 / 回退 download），
        //   不会出现「raw 有、stat 没有」这种半残状态导致的逻辑分叉。
        //   前端探测不到 /oka-raw 会自动回退到 /download，功能不丢。
    }
}

// ★ 挂在 -[GCDWebUploader initWithUploadDirectory:] 上，而不是 init 之后找
//   实例：只有在 init 里注册，handler 才一定早于 startWithOptions: 进入
//   路由表。事后找实例注册，一旦服务已经在跑，新 handler 也可能因为
//   GCDWebServer 的 handler 快照机制而不生效。
//
//   must-succeed 语义：hook 失败时前端探测不到 /oka-raw 会自己回退到
//   /download（行为退回 v1.1.7：慢、会被 IDM 抢，但功能不丢）。
static id (*orig_initWithUploadDirectory)(id, SEL, NSString *);
static id my_initWithUploadDirectory(id self, SEL _cmd, NSString *path) {
    id r = orig_initWithUploadDirectory(self, _cmd, path);
    if (r) { OkaRegisterRawHandler(r); }
    return r;
}

__attribute__((constructor))
static void OkaWiFiInit(void) {
    @autoreleasepool {
        LoadPrefs();

        // Swift weak 槽的读取函数。App 链接了 libswiftCore，正常必能解析到；
        // 解析不到说明不是 Swift App 或环境异常，此时 GetIvar 会返回 nil，
        // 功能降级为"仅按钮放行"，不会崩。
        gWeakLoadStrong = (WeakLoadStrong_t)dlsym(RTLD_DEFAULT,
                                                  "_swift_unknownObjectWeakLoadStrong");
        if (!gWeakLoadStrong) {
            void *h = dlopen("/usr/lib/swift/libswiftCore.dylib", RTLD_LAZY);
            if (!h) h = dlopen("libswiftCore.dylib", RTLD_LAZY);
            if (h) gWeakLoadStrong = (WeakLoadStrong_t)dlsym(h, "_swift_unknownObjectWeakLoadStrong");
        }

        // ---- 2b: 热点 IP ----
        if (gEnableIPFix) {
            void *sym = dlsym(RTLD_DEFAULT, "_GCDWebServerGetPrimaryIPAddress");
            if (!sym) {
                void *h = dlopen("@rpath/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
                if (!h) h = dlopen("Frameworks/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
                if (h) sym = dlsym(h, "_GCDWebServerGetPrimaryIPAddress");
            }
            if (sym) {
                MSHookFunction(sym, (void *)my_GetPrimaryIPAddress,
                               (void **)&orig_GetPrimaryIPAddress);
            }

            // ★ v1.0.2 主力：ObjC 方法层修正 serverURL。
            // 不用 RTLD_NOLOAD —— 那就只是「查一下」，类没注册时不会真的加载；
            // 用 RTLD_LAZY 才能确保 framework 被加载、类被 runtime 注册。
            Class gws = NSClassFromString(@"GCDWebServer");
            if (!gws) {
                dlopen("@rpath/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
                dlopen("Frameworks/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
                gws = NSClassFromString(@"GCDWebServer");
            }
            if (gws) {
                HookInstanceMethod(gws, @selector(serverURL),
                                   (IMP)my_serverURL, (void **)&orig_serverURL,
                                   "-[GCDWebServer serverURL]");
                HookInstanceMethod(gws, @selector(startWithPort:bonjourName:),
                                   (IMP)my_startWithPort, (void **)&orig_startWithPort,
                                   "-[GCDWebServer startWithPort:bonjourName:]");
                // ★ v1.2.0 固定端口：两条启动路径的真正交汇点
                HookInstanceMethod(gws, @selector(startWithOptions:error:),
                                   (IMP)my_startWithOptions, (void **)&orig_startWithOptions,
                                   "-[GCDWebServer startWithOptions:error:]");
                // 同时挂 stop，用于清 gServerPort / gServerRunning
                HookInstanceMethod(gws, @selector(stop),
                                   (IMP)my_stop, (void **)&orig_stop,
                                   "-[GCDWebServer stop]");
                // ★ v1.4.2：进后台不自动停服（第二层，见 my_didEnterBackground）。
                HookInstanceMethod(gws, NSSelectorFromString(@"_didEnterBackground:"),
                                   (IMP)my_didEnterBackground,
                                   (void **)&orig_didEnterBackground,
                                   "-[GCDWebServer _didEnterBackground:]");
            }

            // ---- 2b 兜底：UI 层地址写回 ----
            // 不依赖上面的 MSHookFunction 是否真的打上桩。
            // 只 hook 页面自己实现的方法，避免沿继承链改到 UIViewController。
            Class pageClsIP = LookupClass(PAGE_CLASS_MANGLED, PAGE_CLASS_SWIFT);
            if (pageClsIP) {
                HookInstanceMethod(pageClsIP,
                                   NSSelectorFromString(@"changeWifiTransferStateAction:"),
                                   (IMP)my_changeWiFiState, (void **)&orig_changeWiFiState,
                                   "-[BNHttpServerPage changeWifiTransferStateAction:]");
            }
        }

        // ---- 2a: 解蜂窝限制 ----
        if (gEnableCellular) {
            void *sym = dlsym(RTLD_DEFAULT, "SCNetworkReachabilityGetFlags");
            if (sym) {
                MSHookFunction(sym, (void *)my_SCNRGetFlags, (void **)&orig_SCNRGetFlags);
            }

            Class pageCls = LookupClass(PAGE_CLASS_MANGLED, PAGE_CLASS_SWIFT);
            if (pageCls) {
                HookInstanceMethod(pageCls, NSSelectorFromString(@"reachabilityChangedWithNote:"),
                                   (IMP)my_reachabilityChanged,
                                   (void **)&orig_reachabilityChanged,
                                   "-[BNHttpServerPage reachabilityChangedWithNote:]");
                HookInstanceMethod(pageCls, @selector(viewDidLoad),
                                   (IMP)my_viewDidLoad, (void **)&orig_viewDidLoad,
                                   "-[BNHttpServerPage viewDidLoad]");
            }
        }

        // ---- 1: 快捷菜单 ----
        if (gEnableShortcut) {
            Class appDel = LookupClass(APPDEL_CLASS_MANGLED, APPDEL_CLASS_SWIFT);
            if (appDel) {
                HookInstanceMethod(appDel,
                                   @selector(application:didFinishLaunchingWithOptions:),
                                   (IMP)my_didFinishLaunching,
                                   (void **)&orig_didFinishLaunching,
                                   "-[AppDelegate application:didFinishLaunchingWithOptions:]");

                SEL sel = @selector(application:performActionForShortcutItem:completionHandler:);
                if (!class_addMethod(appDel, sel, (IMP)my_performAction, "v@:@@@?")) {
                    Method m = class_getInstanceMethod(appDel, sel);
                    if (m) method_setImplementation(m, (IMP)my_performAction);
                }
            }
        }

        // ---- 3: 电脑网页端增强（v1.1.0） ----
        // 与 2b 一样用 RTLD_LAZY 真加载 framework，确保类已注册；
        // 不用 RTLD_NOLOAD（那只是"查一下"，类没注册时不会真的加载）。
        if (gEnableWebEnhance) {
            Class dataResp = NSClassFromString(@"GCDWebServerDataResponse");
            if (!dataResp) {
                dlopen("@rpath/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
                dlopen("Frameworks/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
                dataResp = NSClassFromString(@"GCDWebServerDataResponse");
            }
            if (dataResp) {
                HookClassMethod(dataResp,
                                NSSelectorFromString(@"responseWithHTMLTemplate:variables:"),
                                (IMP)my_responseHTMLTemplate,
                                (void **)&orig_responseHTMLTemplate,
                                "+[GCDWebServerDataResponse responseWithHTMLTemplate:variables:]");
                HookInstanceMethod(dataResp,
                                   NSSelectorFromString(@"initWithHTMLTemplate:variables:"),
                                   (IMP)my_initHTMLTemplate,
                                   (void **)&orig_initHTMLTemplate,
                                   "-[GCDWebServerDataResponse initWithHTMLTemplate:variables:]");
            }

            // ---- 3b: v1.1.8 内联读取接口 GET /oka-raw ----
            // 让「预览 / 目录打包 / Blob 下载」能拿到不带
            // Content-Disposition: attachment 的响应，从根上解决
            // Chrome「已阻止不安全的下载」与 IDM 抢请求。
            Class uploader = NSClassFromString(@"GCDWebUploader");
            if (!uploader) {
                dlopen("@rpath/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
                dlopen("Frameworks/GCDWebServer.framework/GCDWebServer", RTLD_LAZY);
                uploader = NSClassFromString(@"GCDWebUploader");
            }
            if (uploader) {
                HookInstanceMethod(uploader,
                                   NSSelectorFromString(@"initWithUploadDirectory:"),
                                   (IMP)my_initWithUploadDirectory,
                                   (void **)&orig_initWithUploadDirectory,
                                   "-[GCDWebUploader initWithUploadDirectory:]");
            }
        }

    }
}