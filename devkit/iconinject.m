// v1.6.6 动态图标注入 helper（oka-iconinject）。
//
// 用法（deb 的 postinst / prerm 以 root 调用）：
//   oka-iconinject inject [--force]   注入备选图标 + plist 声明（幂等）
//   oka-iconinject restore            还原：删 PNG、从备份恢复 Info.plist
//
// 为什么需要一个独立工具而不是让 dylib 注入：
//   ① 切换走公开 API setAlternateIconName:，UIKit 与 SpringBoard 两处
//      都要求备选图标预声明在宿主 Info.plist、文件在 .app 内 ——
//      dylib 跑在 App 沙箱里，**写不了 .app 目录**；只有 root 能。
//   ② deb 安装（dpkg 事务）时注入一次，安装过程是原子的、失败的退出码
//      会被 postinst 容错（|| true），不像运行时写 plist 那样可能把 App
//      留在一个"改了一半"的状态。
//
// 用户三条红线如何落实：
//   · 不能导致 App 打不开：
//       - 安装形态门控（v1.6.9.2 起口径）：embedded.mobileprovision 存在
//         = 第三方签名（自签/企业签/TestFlight/重签名）才否决；无 prov 的
//         Apple 签名形态（App Store / VPP / MDM，含 iTunesMetadata 缺失的
//         真 App Store 安装）一律放行。iTunesMetadata 只是旁证，不再作为
//         否决项 —— 旧判据据此误拒过 App Store 安装的宿主（用户 2026-11-28
//         反馈「我这个就是 AppStore 安装的啊」，注入被白白跳过）。判据本体
//         在 iconinject-core.h（oka_install_form / oka_form_allows_inject），
//         与 dylib 的 OkaHostIsAppStore 共用，并有 host 单测钉住边界。
//         放行路径：--force 或 plist 写 IconInjectForce=true（自担）。
//       - plist 写坏自愈：写前备份（Preferences 目录，只活毫秒级），写后读回
//         校验（层级/键值/标记全对才算成功），任一不过立即从备份还原。
//       - 全程只动两个文件：两张新 PNG + Info.plist。主可执行文件、
//         签名、任何原有资源一律不碰 —— 启动校验只针对主二进制。
//   · 不能时灵时不灵：
//       - 注入是一次性的；切换是系统 setAlternateIconName 标准行为。
//       - 已知失效场景全部显式：App 更新/重装宿主 App 后注入丢失，
//         此时 dylib 静默不切（桌面保持默认图标 = 语义安全的"关"），
//         面板里有只读状态行告诉用户"未启用"。重装本 deb 即恢复。
//   · 关=默认原图标、开=红色染绿版：
//       - 注入的 alternate 名 OKA_ICON_NAME_GREEN；"关"是 primary（nil）。
//       - 主图标里检测不到足够红色像素时拒绝注入（染不出差别 = 无意义，
//         拒绝好过给用户一个"看起来没变"的假功能）。
//
// 退出码：0=成功或已注入；1=真失败（postinst 会提示但继续装）；
//         2=安全跳过（门控拒绝/无红色主色/找不到 App，均**不动任何文件**）。
//
// ★ v1.6.6 文件卫生（用户红线「不要制造过多垃圾文件，要能自动清理」）：
//   原则：.app 目录里**只留该留的**（两张功能 PNG），其余用完即删：
//    · *.oka-tmp（原子写临时文件）：写入前先清同名旧残留（防上次失败
//      留下、被本次误用），失败 return 前也清。
//    · Info.plist 备份：只存在"改 plist 那一瞬间"，成功/失败两条出口
//      都删；存放选 /var/mobile/Library/Preferences/（插件自己的家），
//      **不放 .app**，不脏宿主 bundle。写 plist 瞬间掉电这种极端残留
//      由 prerm restore 顺手清。
//    · OkaIconGreen@2x/@3x.png 是功能文件（切换的图标本体），非垃圾，
//      由 prerm restore 统一删除。

#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ImageIO/ImageIO.h>
// kUTTypePNG 声明在 MobileCoreServices/UTCoreTypes.h —— ImageIO.h 不会
// 间接带上它（run 43 实测：host 单测走 macOS 系统 SDK 看得见，helper 走
// iPhoneOS sysroot 直接 use of undeclared identifier）。
#import <MobileCoreServices/MobileCoreServices.h>
#import "oka-icon-common.h"
#import "iconinject-core.h"

static const char *kGreenName = OKA_ICON_NAME_GREEN;

// 输出统一走 printf：不用 NSLog（输出会带时间戳/进程前缀，且本项目产物
// 纪律禁止 NSLog；postinst 需要的是能读的纯文本行）。
static void say(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
}

// 静默删（清理路径上的标准动作：失败也无所谓，下一步还有闸门/校验兜）。
static void RemoveQuiet(NSString *p) {
    [[NSFileManager defaultManager] removeItemAtPath:p error:nil];
}

static NSString *PrefsPlistPath(void) {
    return @"/var/mobile/Library/Preferences/com.oka.wifitweak.plist";
}

// Info.plist 备份的落点：**不放 .app**（那是宿主的地盘，只留功能文件）。
static NSString *InfoBackupPath(void) {
    return @"/var/mobile/Library/Preferences/com.oka.wifitweak.iconbak";
}

// plist 里的强制注入开关（自签环境用户自担风险时用）。
static BOOL ForceFlagFromPrefs(void) {
    NSDictionary *d = [NSDictionary dictionaryWithContentsOfFile:PrefsPlistPath()];
    return [d[@"IconInjectForce"] boolValue];
}

// 定位宿主 .app 目录。遍历 Bundle/Application/*/ 匹配 bundle id。
static NSString *FindHostAppBundle(void) {
    NSString *root = @"/var/containers/Bundle/Application";
    NSFileManager *fm = [NSFileManager defaultManager];
    NSError *err = nil;
    NSArray<NSString *> *uuids = [fm contentsOfDirectoryAtPath:root error:&err];
    if (!uuids) {
        say("[inject] 读不到 %s: %s", root.fileSystemRepresentation,
            err.localizedDescription.UTF8String ?: "");
        return nil;
    }
    for (NSString *u in uuids) {
        NSString *dir = [root stringByAppendingPathComponent:u];
        NSArray<NSString *> *entries = [fm contentsOfDirectoryAtPath:dir error:nil];
        if (!entries) continue;
        for (NSString *e in entries) {
            if (![e hasSuffix:@".app"]) continue;
            NSString *app = [dir stringByAppendingPathComponent:e];
            NSString *pl = [app stringByAppendingPathComponent:@"Info.plist"];
            NSDictionary *info = [NSDictionary dictionaryWithContentsOfFile:pl];
            if ([info[@"CFBundleIdentifier"] isEqualToString:@OKA_TARGET_BUNDLE_ID]) {
                return app;
            }
        }
    }
    return nil;
}

// ★ v1.6.9.2：安装形态判定改为「只有 embedded.mobileprovision 才是硬否决项」。
//   旧判据（iTunesMetadata 存在且无 prov）把 metadata 缺失也当否决 —— 真机
//   实测：App Store 安装的宿主也可能没有 iTunesMetadata.plist（用户
//   2026-11-28：「我这个就是 AppStore 安装的啊」被旧判据误判成「非原版
//   装态」，注入被白白跳过）。只有 embedded.mobileprovision 是第三方签名
//   （自签/企业签/TestFlight/重签名）的硬信号，才对应「改 bundle 会破坏
//   签名」红线；metadata 只是旁证，缺失不否决。判据本体在
//   iconinject-core.h（与 dylib 的 OkaHostIsAppStore 共用 + host 单测）。
//   has_meta / has_prov 是设备上的事实（文件存在性），不是推断。
static OkaInstallForm InstallFormOf(NSString *app, BOOL *hasMeta, BOOL *hasProv) {
    NSFileManager *fm = [NSFileManager defaultManager];
    *hasMeta = [fm fileExistsAtPath:
                [app stringByAppendingPathComponent:@"iTunesMetadata.plist"]];
    *hasProv = [fm fileExistsAtPath:
                [app stringByAppendingPathComponent:@"embedded.mobileprovision"]];
    return oka_install_form(*hasMeta, *hasProv);
}

// 从 Info.plist 找主图标 basename（CFBundleIcons→CFBundlePrimaryIcon→CFBundleIconFiles）。
static NSString *PrimaryIconBaseName(NSDictionary *info) {
    NSDictionary *icons = info[@"CFBundleIcons"];
    if ([icons isKindOfClass:[NSDictionary class]]) {
        NSDictionary *pri = icons[@"CFBundlePrimaryIcon"];
        if ([pri isKindOfClass:[NSDictionary class]]) {
            NSArray *files = pri[@"CFBundleIconFiles"];
            if ([files isKindOfClass:[NSArray class]] && files.count) {
                for (NSString *n in files) {
                    if ([n isKindOfClass:[NSString class]] && n.length) return n;
                }
            }
        }
        NSArray *legacy = icons[@"CFBundleIconFiles"];
        if ([legacy isKindOfClass:[NSArray class]] && legacy.count) {
            for (NSString *n in legacy) {
                if ([n isKindOfClass:[NSString class]] && n.length) return n;
            }
        }
    }
    return nil;   // Assets.car 内的图标拿不到 —— 由调用方安全拒绝
}

// 按 basename 在 .app 里找实际 PNG（@3x 优先，其次无后缀、@2x）。
static NSString *FindIconFile(NSString *app, NSString *base) {
    NSFileManager *fm = [NSFileManager defaultManager];
    NSArray *cands = @[[base stringByAppendingString:@"@3x.png"],
                       [base stringByAppendingString:@".png"],
                       [base stringByAppendingString:@"@2x.png"]];
    for (NSString *c in cands) {
        NSString *p = [app stringByAppendingPathComponent:c];
        if ([fm fileExistsAtPath:p]) return p;
    }
    return nil;
}

// 读 PNG → RGBA 非预乘 context（红染绿只处理非预乘，避免半透明像素的
// 相乘失真）。缩放由 context 尺寸决定（drawImage 自动插值）。
static CGContextRef CreateRGBAContext(CGImageRef img, size_t w, size_t h) {
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef ctx = CGBitmapContextCreate(NULL, w, h, 8, 0, cs,
                                             kCGImageAlphaNoneSkipLast |
                                             kCGBitmapByteOrder32Big);
    CGColorSpaceRelease(cs);
    if (!ctx) return NULL;
    CGContextDrawImage(ctx, CGRectMake(0, 0, w, h), img);
    return ctx;
}

// 整图红染绿。返回染过的像素数（0 = 这张图里没有红色，无注入意义）。
static long TintImageRedToGreen(CGImageRef src, CGImageRef *out,
                                 size_t w, size_t h) {
    CGContextRef ctx = CreateRGBAContext(src, w, h);
    if (!ctx) return -1;
    uint8_t *data = CGBitmapContextGetData(ctx);
    size_t row = CGBitmapContextGetBytesPerRow(ctx);
    long changed = 0;
    for (size_t y = 0; y < h; y++) {
        uint8_t *px = data + y * row;
        for (size_t x = 0; x < w; x++) {
            changed += oka_tint_rgba(px);
            px += 4;
        }
    }
    if (changed > 0) {
        *out = CGBitmapContextCreateImage(ctx);
    }
    CGContextRelease(ctx);
    return changed;
}

// 写 PNG 到路径（ImageIO，不依赖 UIKit）。
static BOOL WritePNG(CGImageRef img, NSString *path) {
    CFMutableDataRef data = CFDataCreateMutable(kCFAllocatorDefault, 0);
    CGImageDestinationRef dst =
        CGImageDestinationCreateWithData(data, kUTTypePNG, 1, NULL);
    if (!dst) { CFRelease(data); return NO; }
    CGImageDestinationAddImage(dst, img, NULL);
    BOOL ok = CGImageDestinationFinalize(dst);
    CFRelease(dst);
    if (!ok) { CFRelease(data); return NO; }
    // 原子写：先写 .tmp 再 rename，避免 dpkg 中断留下半个 PNG。
    // 写前清同名旧 tmp（上次失败残留）+ 失败路径也清 —— 不留 .oka-tmp。
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *tmp = [path stringByAppendingString:@".oka-tmp"];
    RemoveQuiet(tmp);
    ok = [(__bridge NSData *)data writeToFile:tmp atomically:YES];
    if (ok) {
        RemoveQuiet(path);
        ok = [fm moveItemAtPath:tmp toPath:path error:nil];
    }
    if (!ok) RemoveQuiet(tmp);
    CFRelease(data);
    return ok;
}

// ---- plist 注入（带自愈） ----

static NSDictionary *ReadPlist(NSString *path) {
    return [NSDictionary dictionaryWithContentsOfFile:path];
}

// 写后读回校验：层级、键值、标记三个都必须对上，才算注入成功。
static BOOL VerifyInjectedPlist(NSString *path) {
    NSDictionary *d = ReadPlist(path);
    if (![d isKindOfClass:[NSDictionary class]]) return NO;
    if (![d[@"OkaIconInjected"] boolValue]) return NO;
    NSDictionary *icons = d[@"CFBundleIcons"];
    if (![icons isKindOfClass:[NSDictionary class]]) return NO;
    NSDictionary *alt = icons[@"CFBundleAlternateIcons"];
    if (![alt isKindOfClass:[NSDictionary class]]) return NO;
    NSDictionary *green = alt[@(kGreenName)];
    if (![green isKindOfClass:[NSDictionary class]]) return NO;
    NSArray *files = green[@"CFBundleIconFiles"];
    if (![files isKindOfClass:[NSArray class]]) return NO;
    BOOL hasName = NO;
    for (NSString *n in files) {
        if ([n isKindOfClass:[NSString class]] && [n isEqualToString:@(kGreenName)]) {
            hasName = YES;
        }
    }
    return hasName;
}

static BOOL WriteBinaryPlist(id plist, NSString *path) {
    NSError *err = nil;
    NSData *data = [NSPropertyListSerialization dataWithPropertyList:plist
                                                              format:NSPropertyListBinaryFormat_v1_0
                                                             options:0
                                                               error:&err];
    if (!data) return NO;
    NSString *tmp = [path stringByAppendingString:@".oka-tmp"];
    RemoveQuiet(tmp);                       // 先清上次失败残留
    if (![data writeToFile:tmp atomically:YES]) { RemoveQuiet(tmp); return NO; }
    RemoveQuiet(path);
    BOOL ok = [[NSFileManager defaultManager] moveItemAtPath:tmp toPath:path error:nil];
    if (!ok) RemoveQuiet(tmp);              // 失败也清，不留 .oka-tmp
    return ok;
}

// ---- 子命令 ----

static int DoRestore(NSString *app) {
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *pl = [app stringByAppendingPathComponent:@"Info.plist"];
    RemoveQuiet(InfoBackupPath());   // 顺手清极端残留（写 plist 瞬间掉电才会留下）
    BOOL ok = YES;
    // 没有常驻备份可还原（导出设计：备份只活毫秒级）。restore 走
    // 「手工摘键」：注入只加过两个键（顶层 OkaIconInjected +
    // CFBundleIcons→CFBundleAlternateIcons→OkaIconGreen），逐键摘除即等价还原。
    NSMutableDictionary *d = [ReadPlist(pl) mutableCopy];
    if (d) {
        [d removeObjectForKey:@"OkaIconInjected"];
        NSMutableDictionary *icons = [d[@"CFBundleIcons"] mutableCopy];
        if (icons) {
            NSMutableDictionary *alt = [icons[@"CFBundleAlternateIcons"] mutableCopy];
            if (alt) {
                [alt removeObjectForKey:@(kGreenName)];
                if (alt.count) icons[@"CFBundleAlternateIcons"] = alt;
                else [icons removeObjectForKey:@"CFBundleAlternateIcons"];
            }
            if (icons.count) d[@"CFBundleIcons"] = icons;
            else [d removeObjectForKey:@"CFBundleIcons"];
        }
        ok = WriteBinaryPlist(d, pl);
        say("[restore] Info.plist 已摘除注入键: %s", ok ? "OK" : "失败");
    }
    for (NSString *n in @[[NSString stringWithFormat:@"%s@2x.png", kGreenName],
                          [NSString stringWithFormat:@"%s@3x.png", kGreenName]]) {
        NSString *p = [app stringByAppendingPathComponent:n];
        if ([fm fileExistsAtPath:p]) {
            BOOL r = [fm removeItemAtPath:p error:nil];
            if (!r) { ok = NO; say("[restore] 删 PNG 失败: %s", n.UTF8String); }
        }
    }
    return ok ? 0 : 1;
}

static int DoInject(NSString *app, BOOL force) {
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *pl  = [app stringByAppendingPathComponent:@"Info.plist"];

    // ---- 门控：安装形态（v1.6.9.2 口径，判据本体见 iconinject-core.h）----
    // 先把两个事实与判定结果原样打出来：真机报「装完没生效」时，这一行
    // 就是第一现场证据（旧版只打一句含糊的「不是 App Store 原版装态」，
    // 分不清是 metadata 缺失还是被门控拒，用户与作者都只能靠猜）。
    BOOL hasMeta = NO, hasProv = NO;
    OkaInstallForm form = InstallFormOf(app, &hasMeta, &hasProv);
    say("[inject] 装态判定：iTunesMetadata=%s embedded.mobileprovision=%s → %s",
        hasMeta ? "在" : "无", hasProv ? "在" : "无", oka_form_name(form));
    if (form == OKA_FORM_THIRD_PARTY) {
        if (!force && !ForceFlagFromPrefs()) {
            say("[inject] 检测到 embedded.mobileprovision = 第三方签名装态（自签/企业签/TestFlight/重签名）。");
            say("[inject] 为守住「不能导致 App 打不开」红线，默认不注入。");
            say("[inject] 确认要注入：plist 写 IconInjectForce=true 或用 --force 重跑。");
            return 2;
        }
        say("[inject] 第三方签名装态，已按强制标志继续（用户自担）。");
    }

    // ---- 幂等 ----
    NSDictionary *cur = ReadPlist(pl);
    if ([cur[@"OkaIconInjected"] boolValue] && VerifyInjectedPlist(pl)) {
        say("[inject] 已注入过，跳过（幂等）。App 更新重装后此键会丢失，重装本 deb 即恢复。");
        return 0;
    }

    // ---- 找主图标 ----
    NSString *base = PrimaryIconBaseName(cur);
    NSString *iconFile = base ? FindIconFile(app, base) : nil;
    if (!iconFile) {
        say("[inject] 找不到主图标 PNG（Assets.car 内图标无法读取）。不动任何文件，安全跳过。");
        return 2;
    }
    CGImageSourceRef src = CGImageSourceCreateWithURL((CFURLRef)[NSURL fileURLWithPath:iconFile],
                                                      NULL);
    if (!src) {
        say("[inject] 主图标解码失败: %s", iconFile.UTF8String);
        return 2;
    }
    CGImageRef img = CGImageSourceCreateImageAtIndex(src, 0, NULL);
    CFRelease(src);
    if (!img) {
        say("[inject] 主图标取图失败。");
        return 2;
    }

    // ---- 红染绿 + 写两份尺寸 ----
    CGImageRef g2 = NULL, g3 = NULL;
    long n2 = TintImageRedToGreen(img, &g2, 120, 120);
    long n3 = TintImageRedToGreen(img, &g3, 180, 180);
    CGImageRelease(img);
    if (n2 < 0 || n3 < 0 || n2 == 0 || n3 == 0) {
        if (g2) CGImageRelease(g2);
        if (g3) CGImageRelease(g3);
        say("[inject] 主图标未检测到足够红色像素（染不出可见差别）。不动任何文件，安全跳过。");
        return 2;
    }
    NSString *p2 = [app stringByAppendingString:[NSString stringWithFormat:@"%s@2x.png", kGreenName]];
    NSString *p3 = [app stringByAppendingString:[NSString stringWithFormat:@"%s@3x.png", kGreenName]];
    BOOL okP = WritePNG(g2, p2) && WritePNG(g3, p3);
    CGImageRelease(g2);
    CGImageRelease(g3);
    if (!okP) {
        say("[inject] PNG 写入失败，回滚半成品。");
        [fm removeItemAtPath:p2 error:nil];
        [fm removeItemAtPath:p3 error:nil];
        return 1;
    }

    // ---- plist 注入（备份 → 改 → 校验 → 不过则还原 → 备份用完即删）----
    // 备份只活这一瞬：成功/失败两条出口都删。落点在 Preferences
    // （不放 .app，不脏宿主 bundle）。断电级残留由 restore 顺手清。
    NSString *bak = InfoBackupPath();
    if (![fm copyItemAtPath:pl toPath:bak error:nil]) {
        say("[inject] Info.plist 备份失败，放弃（不许在无备份时改它）。");
        [fm removeItemAtPath:p2 error:nil];
        [fm removeItemAtPath:p3 error:nil];
        return 1;
    }
    NSMutableDictionary *d = [ReadPlist(pl) mutableCopy];
    if (!d) {
        say("[inject] Info.plist 读不出字典，放弃。");
        return 1;
    }
    NSMutableDictionary *icons = [d[@"CFBundleIcons"] mutableCopy];
    if (!icons) icons = [NSMutableDictionary dictionary];
    NSMutableDictionary *alt = [icons[@"CFBundleAlternateIcons"] mutableCopy];
    if (!alt) alt = [NSMutableDictionary dictionary];
    alt[@(kGreenName)] = @{@"CFBundleIconFiles": @[@(kGreenName)]};
    icons[@"CFBundleAlternateIcons"] = alt;
    d[@"CFBundleIcons"] = icons;
    d[@"OkaIconInjected"] = @YES;
    BOOL okW = WriteBinaryPlist(d, pl);
    if (okW) okW = VerifyInjectedPlist(pl);
    if (!okW) {
        // 自愈：立即从备份还原，App 不可能带着坏 plist 启动。
        say("[inject] 写后校验失败，已从备份还原 Info.plist（App 不受影响）。");
        [fm removeItemAtPath:pl error:nil];
        [fm moveItemAtPath:bak toPath:pl error:nil];
        [fm removeItemAtPath:p2 error:nil];
        [fm removeItemAtPath:p3 error:nil];
        return 1;
    }
    say("[inject] 完成：alternate '%s' 已声明并写入 bundle（120/180px 双尺寸）。", kGreenName);
    RemoveQuiet(bak);   // 备份使命完成：成功出口也删（不留常驻垃圾）
    say("[inject] 生效：重开一次宿主 App 即按 WiFi 传输状态切换（开=绿 / 关=原图标）。");
    return 0;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        BOOL force = NO;
        NSString *cmd = @"inject";
        for (int i = 1; i < argc; i++) {
            NSString *a = [NSString stringWithUTF8String:argv[i]];
            if ([a isEqualToString:@"--force"]) force = YES;
            else if ([a isEqualToString:@"inject"]) cmd = @"inject";
            else if ([a isEqualToString:@"restore"]) cmd = @"restore";
        }
        NSString *app = FindHostAppBundle();
        if (!app) {
            say("[inject] 找不到宿主 App（%s）。安全跳过，不动任何文件。", OKA_TARGET_BUNDLE_ID);
            return 2;
        }
        say("[inject] 宿主: %s", app.UTF8String);
        if ([cmd isEqualToString:@"restore"]) return DoRestore(app);
        return DoInject(app, force);
    }
}