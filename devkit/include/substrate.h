/* ===========================================================================
 *  substrate.h  —  最小兼容声明（零 Theos 构建用）
 *
 *  为什么需要这份文件：
 *    原先 Tweak.xm 里写的是 #import <substrate.h>，这个头由 Theos 的
 *    $THEOS/vendor/include/substrate.h 提供。零 Theos 构建时该路径不存在，
 *    编译器直接 fatal error: 'substrate.h' file not found。
 *
 *  为什么只声明 MSHookFunction：
 *    本工程不用 %hook / Logos 语法，也不用 MSHookMessageEx / MSHookIvar，
 *    全部 hook 走 objc runtime（method_setImplementation / class_addMethod）
 *    加一处 MSHookFunction（对 C 函数打点）。只声明真正用到的符号，
 *    避免和 ElleKit 自带的完整 substrate.h 产生声明冲突。
 *
 *  链接期为什么不报未定义：
 *    build.sh 用了 -undefined dynamic_lookup，MSHookFunction 由宿主进程
 *    里已加载的 CydiaSubstrate / libhooker / ElleKit 在运行时解析。
 *
 *  真实签名（三家的 ABI 一致）：
 *    void MSHookFunction(void *symbol, void *replace, void **result);
 * =========================================================================== */
#ifndef OKA_MINIMAL_SUBSTRATE_H
#define OKA_MINIMAL_SUBSTRATE_H

#ifdef __cplusplus
extern "C" {
#endif

/* symbol : 被 hook 的函数地址（dlsym 拿到的裸地址）
 * replace: 替换函数指针
 * result : 出参，回填原函数指针，可传 NULL
 */
extern void MSHookFunction(void *symbol, void *replace, void **result);

#ifdef __cplusplus
}
#endif

#endif /* OKA_MINIMAL_SUBSTRATE_H */