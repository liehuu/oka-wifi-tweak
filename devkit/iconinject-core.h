// v1.6.6 动态图标注入 helper 的**纯函数核心**（无任何 SDK 依赖）。
//
// 为什么单独拆出来：红→绿的像素映射是用户指定的视觉规格（"把现在默认
// 图标上的红色替换为绿色"），它的判定边界（什么算红、映射后是什么绿）
// 必须是可单测的纯函数。放在 CLI 主逻辑里就只能整机跑 iOS 二进制才测
// 得到 —— 那违反了"检查项必须能失败"：边界改坏了没人会发现。
//
// 本文件被两处 include：
//   · devkit/iconinject.m    —— iOS CLI 工具（装 deb 时 root 运行）
//   · devkit/selftest-iconinject.c —— host 单测（macOS/Linux 直接编，无需 SDK）
#ifndef OKA_ICONINJECT_CORE_H
#define OKA_ICONINJECT_CORE_H

#include <stdint.h>

// 判定一个像素是否"够红"。两条阈值都有明确理由：
//   ① r < 89（89/255≈0.35）不算：暗红/深红描边接近黑，染绿只会得到
//      脏绿色斑，视觉上像坏点。
//   ② r 必须显著大于 G 和 B（> 1.7 倍）才算：
//        · 品红（R、B 双高）不染 —— 染了会破坏原图的品红元素；
//        · 橙/黄（R、G 双高）不染 —— 用户要的是"红色元素变绿"，
//          不是整个暖色调位移。
//   用整数运算（*10 / *17）避免浮点；uint8 最大值下 255*17=4335，
//   用 uint32 不会溢出。
static inline int oka_is_red(uint8_t r, uint8_t g, uint8_t b) {
    if (r < 89) return 0;
    if ((uint32_t)r * 10 <= (uint32_t)g * 17) return 0;
    if ((uint32_t)r * 10 <= (uint32_t)b * 17) return 0;
    return 1;
}

// 红 → 绿 映射：把红色的"强度"原样搬到 G 通道（换色不变暗，这是
// 视觉上最接近"换肤"的做法），R/B 压到 10~12% 留一点冷色底，
// 避免出现刺眼的荧光绿。alpha 不动（调用方保留原值）。
static inline void oka_map_red_to_green(uint8_t r,
                                        uint8_t *outR, uint8_t *outG, uint8_t *outB) {
    uint32_t rr = r;
    uint32_t gg = rr * 106 / 100;
    if (gg > 255) gg = 255;
    *outR = (uint8_t)(rr * 12 / 100);
    *outG = (uint8_t)gg;
    *outB = (uint8_t)(rr * 10 / 100);
}

// 整像素入口：px 是 RGBA 四字节（R,G,B,A 顺序，与 CG 的
// kCGImageAlphaPremultipliedLast|ByteOrderDefault 一致）。
// 非红像素原样返回；红像素换绿。返回值 1 = 改动过，0 = 未动。
static inline int oka_tint_rgba(uint8_t *px) {
    if (!oka_is_red(px[0], px[1], px[2])) return 0;
    uint8_t o[3];
    oka_map_red_to_green(px[0], &o[0], &o[1], &o[2]);
    px[0] = o[0];
    px[1] = o[1];
    px[2] = o[2];
    return 1;
}

// ---- v1.6.9.2：安装形态门控的纯函数核心 ----
//
// 为什么也放进这个"无 SDK 依赖"的头：
//   这条判定直接决定「deb 安装期注不注入桌面动态图标」，helper
//   （devkit/iconinject.m）与 dylib（Tweak.xm 的 OkaHostIsAppStore）
//   共用同一份判据 —— 两份各写一遍必然漂移，漂移的症状是「面板说
//   一套、安装期做另一套」，运行时零报错。
//
// 为什么必须是纯函数（与红→绿边界同理）：
//   旧判据 `iTunesMetadata 存在 且 无 embedded.mobileprovision` 把
//   「metadata 缺失」也当否决项。真机实测（用户 2026-11-28 反馈
//   「我这个就是 AppStore 安装的啊」）：App Store 安装的宿主同样
//   可能没有 iTunesMetadata.plist，被旧判据误判成「非原版装态」，
//   安装期注入被白白跳过、面板显示未启用 —— 用户看到的只有一句
//   文案，没有任何报错。判定边界坏了没人会发现，必须能被 host
//   单测钉住（devkit/selftest-iconinject.c）。
//
// 语义（两个输入都是设备上的事实）：
//   has_meta = .app 里有 iTunesMetadata.plist（App Store 安装的旁证）
//   has_prov = .app 里有 embedded.mobileprovision（第三方签名的硬信号）
//   · 有 prov  → 第三方签名（自签/企业签/TestFlight/重签名）→ 拒绝：
//     这类装态改 bundle 会破坏签名，「不能导致 App 打不开」红线命中；
//   · 无 prov  → Apple 签名形态（App Store / VPP / MDM，含 metadata
//     被清理掉的真 App Store 安装）→ 放行；metadata 只在报告里区分，
//     **不作为否决项**。
typedef enum {
    OKA_FORM_APPSTORE = 0,    // iTunesMetadata 在、无 embedded.mobileprovision
    OKA_FORM_APPLE_SIGNED,    // 无 iTunesMetadata、无 embedded.mobileprovision
    OKA_FORM_THIRD_PARTY,     // 有 embedded.mobileprovision
} OkaInstallForm;

static inline OkaInstallForm oka_install_form(int has_meta, int has_prov) {
    if (has_prov) return OKA_FORM_THIRD_PARTY;
    if (has_meta) return OKA_FORM_APPSTORE;
    return OKA_FORM_APPLE_SIGNED;
}

// 放行判据：只有第三方签名形态拒绝。v1.6.9.2 修复的正是 metadata
// 缺失不再否决（旧实现 has_meta && !has_prov 会误拒真 App Store 安装）。
static inline int oka_form_allows_inject(OkaInstallForm f) {
    return f != OKA_FORM_THIRD_PARTY;
}

// 形态名：报告 / 诊断输出用（host 单测与设备输出对齐）。
static inline const char *oka_form_name(OkaInstallForm f) {
    switch (f) {
        case OKA_FORM_APPSTORE:     return "AppStore";
        case OKA_FORM_APPLE_SIGNED: return "AppleSigned";
        default:                    return "ThirdParty";
    }
}

#endif /* OKA_ICONINJECT_CORE_H */