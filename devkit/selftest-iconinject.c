// iconinject-core.h 纯函数 host 单测（红→绿映射 v1.6.6；装态门控 v1.6.9.2）。
// 编译：clang -O2 -Wall devkit/selftest-iconinject.c -o /tmp/sti && /tmp/sti
// 无需任何 SDK / framework：只测 iconinject-core.h 的纯函数。
//
// 这个测试存在的原因（两类判定边界，同一个理由）：
//   ① 红→绿映射的判定边界（什么算红、映射后是什么绿）是用户指定的视觉
//      规格。边界一旦被改坏（比如把品红也染了、或者把阈值放宽到白色都
//      中招），运行时表现只是"图标颜色怪"，没有任何报错。
//   ② 安装形态门控的判定边界（v1.6.9.2）直接决定"装 deb 时注不注入"。
//      旧判据把 iTunesMetadata 缺失也当否决项，真机把 App Store 安装的
//      宿主误判成"非原版装态"、注入被白白跳过 —— 用户看到的只有一句
//      文案，同样没有任何报错。
//   只有纯函数单测能把这些边界钉住。
#include <stdio.h>
#include <string.h>

#include "iconinject-core.h"

static int fails = 0;

static void expect_tint(const char *name, uint8_t r, uint8_t g, uint8_t b,
                         uint8_t a, int wantChanged,
                         int wantRmin, int wantGmin, int wantBmax) {
    uint8_t px[4] = {r, g, b, a};
    int changed = oka_tint_rgba(px);
    if (changed != wantChanged) {
        printf("  ✗ [%s] changed=%d 期望 %d\n", name, changed, wantChanged);
        fails++;
        return;
    }
    if (!wantChanged) return;
    if (px[0] > (uint8_t)wantBmax || px[1] < (uint8_t)wantGmin || px[0] > (uint8_t)wantRmin) {
        printf("  ✗ [%s] 输出 R=%u G=%u B=%u（期望 R<=%d G>=%d B<=%d）\n",
               name, px[0], px[1], px[2], wantRmin, wantGmin, wantBmax);
        fails++;
        return;
    }
    if (px[3] != a) {
        printf("  ✗ [%s] alpha 被改动：%u -> %u\n", name, a, px[3]);
        fails++;
        return;
    }
}

int main(void) {
    // --- 该染的：纯红系 ---
    expect_tint("纯红不透明", 255, 0, 0, 255, 1, 40, 200, 40);
    expect_tint("朱红(g占比0.31)", 200, 60, 10, 255, 1, 40, 200, 40);
    expect_tint("半透明红保alpha", 255, 0, 0, 128, 1, 40, 200, 40);
    expect_tint("阈值下缘r=89", 89, 0, 0, 255, 1, 40, 90, 40);

    // --- 不该染的：暗红 / 非红 ---
    expect_tint("暗红r=88不染", 88, 0, 0, 255, 0, 0, 0, 0);
    expect_tint("纯白不染", 255, 255, 255, 255, 0, 0, 0, 0);
    expect_tint("品红不染", 255, 0, 255, 255, 0, 0, 0, 0);
    expect_tint("橙不染", 255, 165, 0, 255, 0, 0, 0, 0);
    expect_tint("纯绿本身不染", 0, 255, 0, 255, 0, 0, 0, 0);
    expect_tint("黑不染", 0, 0, 0, 255, 0, 0, 0, 0);

    // --- 判定函数的直接断言（防止 expect_tint 的间接性掩盖边界）---
    if (!oka_is_red(89, 0, 0)) { printf("  ✗ is_red(89,0,0) 应为真（阈值含89）\n"); fails++; }
    if (oka_is_red(88, 0, 0)) { printf("  ✗ is_red(88,0,0) 应为假\n"); fails++; }
    // r > g*1.7 边界：g=14 时 r=238 为真（238 > 23.8）；g=141 时 r=239 为假（239 < 239.7）
    if (!oka_is_red(238, 14, 0)) { printf("  ✗ is_red(238,14,0) 应为真\n"); fails++; }
    if (oka_is_red(239, 141, 0)) { printf("  ✗ is_red(239,141,0) 应为假（G 占比过高）\n"); fails++; }

    // --- v1.6.9.2：安装形态门控断言（用户实测误判的回归点） ---
    // 旧判据 `has_meta && !has_prov` 把 iTunesMetadata 缺失也当否决项，
    // 真机把一个 App Store 安装的宿主误判成「非原版装态」、注入被白白
    // 跳过（用户 2026-11-28：「我这个就是 AppStore 安装的啊」）。
    // 这条边界改坏了没有任何运行时报错 —— 下面四组输入就是它的钉子。
    if (oka_form_allows_inject(oka_install_form(1, 0)) != 1) {
        printf("  ✗ App Store 装态（metadata 在、无 prov）必须放行\n"); fails++;
    }
    if (oka_install_form(1, 0) != OKA_FORM_APPSTORE) {
        printf("  ✗ metadata 在 + 无 prov 应判 AppStore\n"); fails++;
    }
    // ★ 回归点：metadata 缺失 + 无 prov = 真 App Store 安装被清理掉旁证的
    //   形态，必须放行（旧判据在这里误拒，这正是 v1.6.9.2 要修的）。
    if (oka_install_form(0, 0) != OKA_FORM_APPLE_SIGNED) {
        printf("  ✗ 无 metadata + 无 prov 应判 AppleSigned\n"); fails++;
    }
    if (oka_form_allows_inject(oka_install_form(0, 0)) != 1) {
        printf("  ✗ 无 metadata + 无 prov 必须放行（v1.6.9.2 回归点："
               "旧判据误拒真 App Store 安装）\n"); fails++;
    }
    // 有 embedded.mobileprovision 一律拒绝（红线：不破坏第三方签名）
    if (oka_install_form(0, 1) != OKA_FORM_THIRD_PARTY ||
        oka_install_form(1, 1) != OKA_FORM_THIRD_PARTY) {
        printf("  ✗ 有 embedded.mobileprovision 必须判 ThirdParty（无论 metadata）\n"); fails++;
    }
    if (oka_form_allows_inject(oka_install_form(0, 1)) != 0 ||
        oka_form_allows_inject(oka_install_form(1, 1)) != 0) {
        printf("  ✗ 第三方签名装态必须拒绝注入（红线）\n"); fails++;
    }
    if (strcmp(oka_form_name(OKA_FORM_THIRD_PARTY), "ThirdParty") != 0) {
        printf("  ✗ 形态名 ThirdParty 变了（诊断输出与单测要对齐）\n"); fails++;
    }

    if (fails) {
        printf("  共 %d 条断言失败\n", fails);
        return 1;
    }
    printf("  ✓ 红→绿映射断言全部通过（该染的染、不该染的不染、alpha 不变）\n");
    printf("  ✓ 装态门控断言全部通过（metadata 缺失不否决；embedded.mobileprovision 才否决）\n");
    return 0;
}