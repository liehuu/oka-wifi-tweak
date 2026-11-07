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
#import <string.h>
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

#define LOG(fmt, ...) NSLog(@"[OkaWiFi] " fmt, ##__VA_ARGS__)

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

static void LoadPrefs(void) {
    NSDictionary *d = [NSDictionary dictionaryWithContentsOfFile:kPrefsPath];
    if (!d) return;
    if (d[@"EnableShortcut"])    gEnableShortcut    = [d[@"EnableShortcut"] boolValue];
    if (d[@"EnableCellular"])    gEnableCellular    = [d[@"EnableCellular"] boolValue];
    if (d[@"EnableIPFix"])       gEnableIPFix       = [d[@"EnableIPFix"] boolValue];
    if (d[@"EnableWebEnhance"])  gEnableWebEnhance  = [d[@"EnableWebEnhance"] boolValue];
    if (d[@"CopyURLOnShortcut"]) gCopyURLOnShortcut = [d[@"CopyURLOnShortcut"] boolValue];
    LOG("prefs: shortcut=%d cellular=%d ipfix=%d web=%d copy=%d",
        gEnableShortcut, gEnableCellular, gEnableIPFix,
        gEnableWebEnhance, gCopyURLOnShortcut);
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
//  直接复用它（不用往包里加文件），拿不到就放弃并记日志。
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
    if (!playerCls) { LOG("× 没有 AVAudioPlayer，跳过保活"); return; }

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
        LOG("后台保活音频 %@ (path=%@)", ok ? @"已启动" : @"启动失败", path);
        gKeepAlivePlayer = (__bridge_retained void *)player;
        gKeepAliveOn = ok;
    } else {
        LOG("× 找不到 Happy Dreams.mp3，保活未启用");
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
    LOG("后台保活已停止");
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

        LOG("iface %.10s -> %@ (score %d)", ifa->ifa_name, ip, s);
        if (s > bestScore) { bestScore = s; best = ip; }
    }
    freeifaddrs(head);

    if (best) LOG("BestLocalIPAddress = %@ (score %d)", best, bestScore);
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

static NSString *my_GetPrimaryIPAddress(BOOL includeIPv6) {
    NSString *r = orig_GetPrimaryIPAddress ? orig_GetPrimaryIPAddress(includeIPv6) : nil;
    // ★ 这条日志是定位「地址显示 unknown」的关键证据：
    //   完全没有这行 → MSHookFunction 没打上 / 函数根本没被调用；
    //   orig=(null) 且没有 fallback → BestLocalIPAddress 在真机上没枚举到可用接口。
    LOG("GetPrimaryIPAddress orig=%@ (判定=%s)",
        r, IsPlausibleIPAddress(r) ? "IP" : "非IP/空");
    if (!gEnableIPFix) return r;
    if (IsPlausibleIPAddress(r)) return r;     // 是合法 IP，原逻辑正常，不动

    NSString *ip = BestLocalIPAddress(includeIPv6);
    if (ip) { LOG("GetPrimaryIPAddress fallback -> %@", ip); return ip; }
    LOG("× BestLocalIPAddress 也没拿到地址，只能返回原值");
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
static NSURL *(*orig_serverURL)(id, SEL);
static NSURL *my_serverURL(id self, SEL _cmd) {
    NSURL *url = orig_serverURL ? orig_serverURL(self, _cmd) : nil;
    if (!gEnableIPFix) return url;

    // 运行状态是权威判据（不再靠 gServerPort 猜）
    SEL selRun = @selector(isRunning);
    if ([self respondsToSelector:selRun]) {
        gServerRunning = ((BOOL (*)(id, SEL))objc_msgSend)(self, selRun);
    }

    // WiFi 下原 URL 的 host 本来就是合法 IP —— 原样返回，零副作用。
    NSString *host = [url host];
    if (IsPlausibleIPAddress(host)) return url;

    // 真实端口：实例自己的 port 属性 > 原 URL 的 port > hook 记录 > 8080
    NSUInteger port = 0;
    SEL selPort = @selector(port);
    if ([self respondsToSelector:selPort]) {
        port = ((NSUInteger (*)(id, SEL))objc_msgSend)(self, selPort);
    }
    if (!port) port = [[url port] unsignedIntegerValue];
    if (port) gServerPort = port;

    // 服务没在跑就别造地址：此时 unknown / 空白是正确状态。
    if (!gServerRunning && !port) return url;

    NSString *ip = BestLocalIPAddress(NO);
    if (!ip) { LOG("× serverURL 修正跳过：枚举不到本机 IP"); return url; }
    if (!port) port = gServerPort ? gServerPort : 8080;

    NSURL *fixed = [NSURL URLWithString:
                    [NSString stringWithFormat:@"http://%@:%lu/", ip, (unsigned long)port]];
    LOG("serverURL 修正: 原=[%@] -> 新=[%@] (port=%lu running=%d)",
        url, fixed, (unsigned long)port, gServerRunning);
    return fixed;
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

    uint32_t before = f;
    f &= ~(1u << 18);                          // kSCNetworkReachabilityFlagsIsWWAN
    *(uint32_t *)flags = f;
    LOG("flags 0x%08x -> 0x%08x (cleared IsWWAN)", before, f);
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
    return str;
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
        if (weakPageForIP) RewriteAddressIfNeeded(weakPageForIP);
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
    if (!cls) { LOG("× 找不到 BNHttpServerPage 类"); return nil; }
    LOG("找到页面类 %s", class_getName(cls));

    UIViewController *page = nil;
    @try {
        // 走 -init（App 自己的 designated init），绝不能换成 initWithNibName:bundle:
        page = [[cls alloc] init];
        (void)page.view;                       // 触发 viewDidLoad，连好所有 outlet
    } @catch (NSException *e) {
        LOG("× 创建无界面页面失败: %@", e);
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
        LOG("× httpServer 为空，放弃无界面页面（避免 brk）");
        return nil;
    }
    RetainObject(page);                        // 保住它，别被释放
    // ★ ARC 下 id → void* 必须显式 __bridge，不能裸强转：
    //     "cast of Objective-C pointer type 'id' to C pointer type 'void *'
    //      requires a bridged cast"
    //   这条是硬 error，不是 warning。（我上一轮为了消 -Wformat 加的
    //   (void *)hs 就栽在这里，CI 直接编译失败。）
    //   %p 只读地址、不做所有权转移，所以用 __bridge（不是 __bridge_retained）。
    LOG("✓ 已创建无界面 BNHttpServerPage (httpServer=%p)", (__bridge void *)hs);
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
    LOG("GCDWebServer startWithPort:%lu name=%@ ok=%d realPort=%lu",
        (unsigned long)port, name, ok, (unsigned long)gServerPort);
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
    LOG("GCDWebServer stop -> gServerPort 清零 / running=NO");
}

// 兜底算地址：自己的 IP + 记录到的端口
static NSString *FallbackServerURLString(void) {
    NSString *ip = BestLocalIPAddress(NO);
    if (!ip) return nil;
    NSUInteger port = gServerPort;
    if (!port) port = 8080;
    return [NSString stringWithFormat:@"http://%@:%lu", ip, (unsigned long)port];
}

// ---------------------------------------------------------------------------
//  UI 层地址修正：不依赖 MSHookFunction / GCDWebServer 内部
// ---------------------------------------------------------------------------
//  为什么必须有这一层：
//    上面 hook _GCDWebServerGetPrimaryIPAddress 是「从源头修」，但它有两个
//    失效点任何一个命中都会让界面停留在占位文案（真机实测显示 "unknown"）：
//      1) MSHookFunction 在该 framework 上没真正打上桩（日志里仍会打 ✓ hook，
//         因为那只代表 dlsym 找到了符号）；
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

// 候选端口按可信度排序：start hook 记录 > App 文案里抠出来的 > 默认 8080。
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
    if (8080 != gServerPort && 8080 != fromText)  cand[n++] = 8080;
    if (8081 != gServerPort && 8081 != fromText)  cand[n++] = 8081;

    for (int i = 0; i < n; i++) {
        if (PortIsListeningAddr(lo, cand[i]) ||
            (hasLan && PortIsListeningAddr(lan, cand[i]))) {
            LOG("端口自证: %lu 正在 LISTEN（候选第 %d 个）",
                (unsigned long)cand[i], i + 1);
            return cand[i];
        }
    }
    // 探测不到 ≠ 服务没跑。serverURL hook 读到的实例 port / isRunning
    // 是直接证据，有其一就允许写回（用户实测「传输功能一直正常」）。
    if (gServerPort) {
        LOG("端口自证: 未探测到 LISTEN，但实例 port=%lu 是直接证据，采信",
            (unsigned long)gServerPort);
        return gServerPort;
    }
    if (gServerRunning) {
        LOG("端口自证: 未探测到 LISTEN，但 isRunning=YES，用 8080 兜底");
        return 8080;
    }
    LOG("端口自证: 无端口在 LISTEN，且 port / isRunning 都无证据 → 不改地址");
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
    return !IsPlausibleIPAddress(host);
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
    if (!ip) { LOG("× 地址修正跳过：枚举不到本机 IP"); return; }

    NSUInteger port = ResolveLivePort(cur, ip);
    if (!port) return;   // 服务没在跑：unknown / 空白是正确状态，不造假地址

    NSString *url = [NSString stringWithFormat:@"http://%@:%lu", ip, (unsigned long)port];
    LOG("地址修正: 原=[%@] -> 新=[%@] (port=%lu, 已自证)",
        cur, url, (unsigned long)port);

    SEL setText = @selector(setText:);
    if ([tv respondsToSelector:setText]) {
        ((void (*)(id, SEL, id))objc_msgSend)(tv, setText, url);
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
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.6 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakSelf) RewriteAddressIfNeeded(weakSelf);
    });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.8 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakSelf) RewriteAddressIfNeeded(weakSelf);
    });
}

// 弹出结果提示
static void ShowResultAlert(NSString *urlString, BOOL copied) {
    UIWindow *win = KeyWindow();
    UIViewController *vc = win.rootViewController;
    while (vc.presentedViewController) vc = vc.presentedViewController;
    if (!vc) return;

    NSString *msg = urlString.length ? urlString : @"已在后台开启，请在 App 的 WiFi 传输页查看地址";
    if (copied && urlString.length) msg = [msg stringByAppendingString:@"\n\n（地址已复制到剪贴板）"];

    UIAlertController *ac = [UIAlertController alertControllerWithTitle:@"WiFi 传输已开启"
                                                              message:msg
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [ac addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleCancel handler:nil]];
    if (urlString.length) {
        [ac addAction:[UIAlertAction actionWithTitle:@"再复制一次" style:UIAlertActionStyleDefault
                                            handler:^(UIAlertAction *a) {
            UIPasteboard.generalPasteboard.string = urlString;
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
        LOG("× 没有可用页面，无法开启");
        if (fromShortcut) ShowResultAlert(nil, NO);
        return;
    }

    // 已经开着就别再点一次（再点一次是"停止"，会把用户正在传的文件中断）
    NSString *existing = URLTextFromPage(page);
    BOOL portLive = (gServerPort != 0);
    if (existing.length || portLive) {
        LOG("已在运行 (url=%@ port=%lu)，不重复开启",
            existing, (unsigned long)gServerPort);
        NSString *s = existing.length ? existing : FallbackServerURLString();
        if (fromShortcut && gCopyURLOnShortcut && s.length)
            UIPasteboard.generalPasteboard.string = s;
        if (fromShortcut) ShowResultAlert(s, gCopyURLOnShortcut && s.length);
        // 即便是"已开启"也要确保保活挂着（上次可能是手动开的）
        if (fromShortcut) StartBackgroundKeepAlive();
        return;
    }

    SEL toggle = NSSelectorFromString(@"changeWifiTransferStateAction:");
    if (![page respondsToSelector:toggle]) {
        LOG("页面不响应 changeWifiTransferStateAction:");
        if (fromShortcut) ShowResultAlert(nil, NO);
        return;
    }
    ((void (*)(id, SEL, id))objc_msgSend)(page, toggle, nil);
    LOG("已触发 changeWifiTransferStateAction:");

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
        if (s.length && gCopyURLOnShortcut) UIPasteboard.generalPasteboard.string = s;
        LOG("最终地址: %@ (port=%lu)", s, (unsigned long)gServerPort);
        ShowResultAlert(s, gCopyURLOnShortcut && s.length);
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
        LOG("已注入快捷菜单项 (%lu 项)", (unsigned long)items.count);
    }
    return r;
}

// App 自身没有实现 performActionForShortcutItem:，由我们补上
static void my_performAction(id self, SEL _cmd, UIApplication *app,
                            UIApplicationShortcutItem *item, void (^completion)(BOOL)) {
    if (item && [item.type isEqualToString:SHORTCUT_TYPE]) {
        LOG("快捷菜单点击 -> 开启 WiFi 传输");
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
static void HookInstanceMethod(Class cls, SEL sel, IMP newImp, void **outOrig, const char *tag) {
    if (!cls) return;
    Method m = class_getInstanceMethod(cls, sel);
    if (!m) { LOG("  × 找不到方法 %s", tag); return; }
    if (outOrig) *outOrig = (void *)method_getImplementation(m);
    method_setImplementation(m, newImp);
    LOG("  ✓ hook %s", tag);
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

static void HookClassMethod(Class cls, SEL sel, IMP newImp, void **outOrig, const char *tag) {
    if (!cls) return;
    Method m = class_getClassMethod(cls, sel);
    if (!m) { LOG("  × 找不到类方法 %s", tag); return; }
    if (outOrig) *outOrig = (void *)method_getImplementation(m);
    method_setImplementation(m, newImp);
    LOG("  ✓ hook %s", tag);
}

__attribute__((constructor))
static void OkaWiFiInit(void) {
    @autoreleasepool {
        LOG("载入 pid=%d", getpid());
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
        // ★ 不能把三元表达式直接塞进 LOG(...)：
        //   LOG 展开是 NSLog(@"[OkaWiFi] " fmt, ...)，代入后变成
        //   @"..." ? "..." : "..."，字符串字面量被当成条件 → 语法错。
        //   三元要作为**参数**传进去，而不是占满整个 fmt 位置。
        LOG("%s", gWeakLoadStrong ? "✓ 解析到 _swift_unknownObjectWeakLoadStrong"
                                  : "× 未找到 _swift_unknownObjectWeakLoadStrong（outlet 读取不可用）");

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
                LOG("✓ hook _GCDWebServerGetPrimaryIPAddress");
            } else {
                LOG("× 找不到 _GCDWebServerGetPrimaryIPAddress（IP 修正不可用）");
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
                // 同时挂 stop，用于清 gServerPort / gServerRunning
                HookInstanceMethod(gws, @selector(stop),
                                   (IMP)my_stop, (void **)&orig_stop,
                                   "-[GCDWebServer stop]");
            } else {
                LOG("× 找不到 GCDWebServer 类（serverURL 修正不可用，只剩 UI 兜底）");
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
            } else {
                LOG("× 找不到 BNHttpServerPage 类（UI 地址修正不可用）");
            }
        }

        // ---- 2a: 解蜂窝限制 ----
        if (gEnableCellular) {
            void *sym = dlsym(RTLD_DEFAULT, "SCNetworkReachabilityGetFlags");
            if (sym) {
                MSHookFunction(sym, (void *)my_SCNRGetFlags, (void **)&orig_SCNRGetFlags);
                LOG("✓ hook SCNetworkReachabilityGetFlags");
            } else {
                LOG("× 找不到 SCNetworkReachabilityGetFlags");
            }

            Class pageCls = LookupClass(PAGE_CLASS_MANGLED, PAGE_CLASS_SWIFT);
            if (pageCls) {
                LOG("页面类 = %s", class_getName(pageCls));
                HookInstanceMethod(pageCls, NSSelectorFromString(@"reachabilityChangedWithNote:"),
                                   (IMP)my_reachabilityChanged,
                                   (void **)&orig_reachabilityChanged,
                                   "-[BNHttpServerPage reachabilityChangedWithNote:]");
                HookInstanceMethod(pageCls, @selector(viewDidLoad),
                                   (IMP)my_viewDidLoad, (void **)&orig_viewDidLoad,
                                   "-[BNHttpServerPage viewDidLoad]");
            } else {
                LOG("× 找不到 BNHttpServerPage 类");
            }
        }

        // ---- 1: 快捷菜单 ----
        if (gEnableShortcut) {
            Class appDel = LookupClass(APPDEL_CLASS_MANGLED, APPDEL_CLASS_SWIFT);
            if (appDel) {
                LOG("AppDelegate = %s", class_getName(appDel));
                HookInstanceMethod(appDel,
                                   @selector(application:didFinishLaunchingWithOptions:),
                                   (IMP)my_didFinishLaunching,
                                   (void **)&orig_didFinishLaunching,
                                   "-[AppDelegate application:didFinishLaunchingWithOptions:]");

                SEL sel = @selector(application:performActionForShortcutItem:completionHandler:);
                if (!class_addMethod(appDel, sel, (IMP)my_performAction, "v@:@@@?")) {
                    Method m = class_getInstanceMethod(appDel, sel);
                    if (m) method_setImplementation(m, (IMP)my_performAction);
                    LOG("✓ 覆盖 performActionForShortcutItem:completionHandler:");
                } else {
                    LOG("✓ 新增 performActionForShortcutItem:completionHandler:");
                }
            } else {
                LOG("× 找不到 AppDelegate");
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
                LOG("网页增强注入层已挂载（JS=%lu CSS=%lu 字节）",
                    (unsigned long)strlen(kOkaWebJS), (unsigned long)strlen(kOkaWebCSS));
            } else {
                LOG("× 找不到 GCDWebServerDataResponse 类（网页增强不可用）");
            }
        } else {
            LOG("网页增强已通过 prefs 关闭");
        }

        LOG("初始化完成");
    }
}