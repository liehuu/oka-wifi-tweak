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

#endif /* OKA_ICONINJECT_CORE_H */