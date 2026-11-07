# Oka WiFi 传输增强（com.oka.wifitweak）

针对 **Oka Files / Blinker 1.7.5**（`com.blinker.documents`）的越狱插件，实现两个功能：

| # | 功能 | 效果 |
|---|---|---|
| 1 | 桌面长按快捷开启 | 长按图标出现「开启 WiFi 传输」，点一下直接启动服务，地址自动复制到剪贴板并弹窗提示。不用进 App、不用点按钮 |
| 2 | 蜂窝 / 热点可开启 | 用手机流量或个人热点时，WiFi 传输页面的「开始」按钮正常出现，可正常开启；且显示的地址是手机在热点网段的真实 IP（`172.20.10.1` 这类），不再是空白 |

---

## 一、逆向依据（不是猜的）

### 1. 开关逻辑本身没有网络类型限制

`-[BNHttpServerPage changeWifiTransferStateAction:]`（`0x100013480`）是薄壳，真实现 `0x100013a14`：

```asm
ldr  x20, [x20, #0x8]        ; self.httpServer
ldr  x8,  [x20]              ; vtable
ldr  x8,  [x8, #0xe0]        ; isRunning
blr  x8
tbz  w21, #0, 0x100013ba8    ; 未运行 → 启动分支
```

启动分支里只做三件事：`connectionView.hidden = NO`、`startBtn.setTitle(...)`、`httpServer.start(block)`。**整段没有任何 reachability / WWAN 判断。**

### 2. "只有 WiFi 才能开"的真正来源

`-[BNHttpServerPage reachabilityChangedWithNote:]`（`0x1000132a4`）→ 内部闭包 `0x100012ddc`：

```asm
0x100012ed4  ldr   w8, [x22, #0x58]        ; SCNetworkReachabilityFlags
0x100012ed8  tbz   w8, #1, 0x1000130c0     ; !Reachable        → 不可用
0x100012f00  tbnz  w8, #0x12, 0x1000130c0  ; ★ IsWWAN(0x40000) → 不可用
0x100012f04  cbz   w9, 0x1000130c0         ; !usable           → 不可用
```

不可用分支 `0x1000130c0` 做的是：状态文字 `ON/OFF/Waiting`、`connectionView.hidden = YES`、`qrCodeView.alpha = 0`、**`startBtn.hidden = YES`**。

> 也就是说：蜂窝下被限制的只是**开始按钮被隐藏了，用户点不到**；服务器本身完全能启动。这就是为什么功能 2 只需要「把按钮放出来」+「保证 flag 判定不被拦」。

### 3. 热点下地址是空白的真正原因

`GCDWebServer` 的 `_GCDWebServerGetPrimaryIPAddress`（`0x17ff4`）整段逻辑：

```c
getifaddrs(&ifaddr);
for (ifa = ifaddr; ifa; ifa = ifa->ifa_next) {
    if (strcmp(ifa->ifa_name, "en0")) continue;      // ★ 唯一条件，硬编码 en0
    if (!(ifa->ifa_flags & IFF_UP)) continue;
    if (sa->sa_family != AF_INET && != AF_INET6) continue;
    return GCDWebServerStringFromSockAddr(...);
}
return nil;
```

而 `-[GCDWebServer(Extensions) serverURL]`（`0xee44`）：

```asm
bl   _GCDWebServerGetPrimaryIPAddress
cbz  x0, 0xef3c        ; ← 拿到 nil 就直接返回 nil（连 port 都不拼）
```

热点下接口名是 `bridge100`、蜂窝是 `pdp_ip0`，**都不是 `en0`** → 函数返回 nil → `serverURL` 返回 nil → 页面地址栏空白、二维码内容是 `null`。

> 这就是为什么单破解 WWAN 判定还不够：**必须在同一份补丁里修正 IP 获取**，否则「能开启但看不到地址」，等于没用。

### 4. 顺带确认的运行时事实

- Swift 类在 ObjC 运行时注册的名字是 **mangled 形式**：`_TtC7Blinker16BNHttpServerPage`（直接从 `class_ro_t.name` 读出来的）。用 `NSClassFromString(@"Blinker.BNHttpServerPage")` **取不到**，代码里做了 mangled → Swift 名 → 后缀遍历三级兜底。
- `BNFileSharingHelper` 的 `isRunning` / `serverURL` / `stop` 是**纯 Swift 方法，没有 ObjC 导出**（方法表里只有 delegate 方法）。但它 `ivar uploader @0x08` 是 `GCDWebUploader : GCDWebServer`，后者有完整 ObjC 接口（`isRunning` / `port` / `serverURL` / `stop`），所以一律走 uploader。
- `BNHttpServerPage` 的 `changeWifiTransferStateAction:` 里有大量 Swift 强制解包，`0x100013e24` 起全是 `brk #1`。outlet 缺一个就崩，所以「无界面创建页面」时逐个校验 6 个 outlet 全部非空才继续。
- `Info.plist` 没有 `UIApplicationShortcutItems`，也不能改（破坏签名）→ 用运行时 `UIApplication.sharedApplication.shortcutItems` 注入动态菜单项。
- `AppDelegate` **没有**实现 `application:performActionForShortcutItem:completionHandler:` → 由插件 `class_addMethod` 补上。
- `AppDelegate` 类名同样是 mangled：`_TtC7Blinker11AppDelegate`。

---

## 二、实现要点

### 功能 2a —— 解除蜂窝限制（两层）

**层 1（根因）** `MSHookFunction` 挂 `SCNetworkReachabilityGetFlags`，**只清 `IsWWAN` 这一位**：

```c
f &= ~(1u << 18);   // kSCNetworkReachabilityFlagsIsWWAN
```

按 `0x100012ee4~0x100012f04` 的汇编推演，判定链是：
```
w9  = !TransientConnection
w10 = ConnectionRequired ? w9 : 1
w9  = (OnDemand|OnTraffic) ? w10 : w9
if (IsWWAN) -> 不可用;  if (!w9) -> 不可用
```
蜂窝下实际 flags = `Reachable(0x2) | IsWWAN(0x40000)`，`TransientConnection` 并不置位，`w9` 本来就是 1。清掉 bit18 后整条判定直接走「可用」。

> 刻意**不改** `TransientConnection` / `ConnectionRequired` 等其它位 —— 那些位会被 App 的 SMB、网盘、下载等其它联网逻辑共用，乱改只会引入副作用。

**层 2（兜底）** hook `-reachabilityChangedWithNote:` 与 `-viewDidLoad`，跑完原逻辑后强制 `startBtn.hidden = NO`、`connectionView.hidden = NO`。即便某系统版本 flag 语义有差异，按钮也一定可点。

### 功能 2b —— 热点 IP 修正

hook `_GCDWebServerGetPrimaryIPAddress(BOOL)`：原函数返回非空就直接透传（WiFi 场景完全不受影响）；返回空时自己遍历 `getifaddrs`，按接口名打分挑地址：

| 接口 | 分数 | 说明 |
|---|---|---|
| `bridge*` | 100 | 个人热点，电脑连的就是这个网段 |
| `en*` | 90 | Wi-Fi |
| `pdp_ip*` | 80 | 蜂窝 |
| 其它 | 10 | 兜底 |
| `lo0` / `utun*` / `ipsec*` / `awdl*` / `llw*` | 排除 | 回环、VPN 隧道、AirDrop，电脑连不上 |

### 功能 1 —— 桌面长按

1. hook `application:didFinishLaunchingWithOptions:`，在原实现跑完后合并注入 `UIApplicationShortcutItem`（type = `com.oka.wifi.start`，标题「开启 WiFi 传输」，图标 `wifi`）。
2. `class_addMethod` 补 `application:performActionForShortcutItem:completionHandler:`。
3. 点击后：
   - 先在当前视图层级里 DFS 找现成的 `BNHttpServerPage`；
   - 找不到就从 `BNHttpServerPage.nib` 造一个**不显示**的实例（`RetainObject` 保活），校验 6 个 outlet 齐全；
   - 若服务未运行 → 调 `changeWifiTransferStateAction:` 启动；
   - 挂**后台保活**（`AVAudioSession` Playback + 循环播放 App 自带的 `Happy Dreams.mp3`，音量 0）——否则切到后台几秒后进程被挂起、端口就断了；
   - 等 1.5s 读 `uploader.serverURL`，复制到剪贴板并弹窗显示。

---

## 三、编译

**不需要 Theos。** 构建是一台装了 Xcode 的 macOS（或 GitHub Actions 的 `macos-latest`）上跑三条命令：

```bash
sh devkit/build.sh arm64e iphoneos-arm64e 1.0.0   # RootHide / roothide
sh devkit/build.sh arm64  iphoneos-arm64  1.0.0   # rootless（Dopamine / palera1n）
sh devkit/build.sh arm64  iphoneos-arm    1.0.0   # 传统 rootful
```

产物在 `.build/out/com.oka.wifitweak_<ver>_<arch>.deb`。

### 为什么不用 Theos

实测 `Setup Theos`（clone 主仓库 + 全部 submodule + 下载 iOS SDK）要 **680 秒**，
而本工程**没有任何 `%hook` / Logos 语法**——全部 hook 走 ObjC runtime
（`method_setImplementation` / `class_addMethod`）加一处 `MSHookFunction`
（对 C 函数打点）。所以 `logos.pl` 用不上，Theos 的 makefile 体系也用不上。
整套 Theos 只为跑一次 `dpkg-deb` 不值得。零 Theos 全流程约 **20 秒**。

替换链路：

| 环节 | Theos 方案 | 本工程方案 |
|---|---|---|
| 编译 | `make`（logos.pl → clang） | `xcrun clang -target ... -dynamiclib` |
| 签名 | ldid | `codesign --force --sign -` |
| 打包 | `dpkg-deb -b` | `devkit/mkdeb.sh`（纯 Python 拼 ar 归档） |
| substrate.h | `$THEOS/vendor/include/` | 仓库自带 `devkit/include/substrate.h` |

### 零 Theos 下踩过的坑（都是 Theos 环境替你掩盖的）

1. **`<substrate.h>` 找不到。** 这个头是 Theos 提供的。零 Theos 直接
   `fatal error: 'substrate.h' file not found`。→ 仓库自带最小声明（只声明
   `MSHookFunction`）+ 编译时 `-I devkit/include`。

2. **架构闸门假通过。** 旧写法 `lipo -info | grep -q arm64`：arm64e 的输出是
   `... is architecture: arm64e`，**包含** `arm64` 子串 → grep 直接过。
   于是「arm64e 变体被误编成 arm64」这个最致命的错误查不出来（症状是装上了
   但 dylib 不被加载，系统不报错）。→ 改用 `lipo -archs` + 整词相等 + 单切片校验。

3. **`-fobjc-exceptions` 与 `<dispatch/dispatch.h>`。** Theos 有 `prefix.pch`
   兜底，`xcrun clang` 单文件直接编没有，`@try/@catch` 与 `dispatch_after` 会报错。

4. **`DEBIAN/` 绝不能进 `data.tar`。** dpkg 只从 `control.tar` 读控制文件。
   若 `data.tar` 里带 `./DEBIAN/postinst`，会解到根目录的 `/DEBIAN`——
   rootless 上 `/` 只读 → 解包失败 → **整个包装不上**。（已由 `mkdeb.sh` 剪枝 + 闸门守住。）

5. **rootless 的 `data.tar` 不能同时出现 `./Library/...` 和 `./var/jb/...`。**
   原本无条件 `mkdir "$STAGE/Library/..."`，rootless 变体就会多出一整棵空的
   `./Library` 树。空目录也是 tar 成员，不会因为"里面没文件"自动消失；
   rootless 上解它写 `/` → 只读 → 装不上。→ 只建 `$DEST`，并加路径前缀闸门。

6. **tar 必须 `GNU_FORMAT`。** Python 3.8+ `tarfile.open(mode='w')` 默认
   `PAX_FORMAT`，遇小数秒 mtime 会在**每个成员前**插 `typeflag='x'` 扩展头，
   dpkg 报 `unsupported PAX tar header type 'x'`。→ 锁 `GNU_FORMAT` + mtime 取整。

7. **dylib 必须 755 且带 `LC_CODE_SIGNATURE`。** 644 或无签名的 dylib 会被
   ellekit / RootHide 加载器静默跳过，表现是「装了完全没反应」而 dpkg 不报错。

`devkit/build.sh` 里这四条闸门全部是**硬失败**（`exit 1`），不是警告。
且都做过反向注入验证：注入缺陷 → 必须报错且不产出坏包 → 还原 → 复跑干净。

### GitHub Actions

推上去自动跑三变体矩阵（`rootful` / `rootless` / `roothide`），
`.github/workflows/build.yml`。手动触发可指定版本号。

手动装：

```bash
scp .build/out/*.deb root@<设备IP>:/tmp/
ssh root@<设备IP>
dpkg -i /tmp/com.oka.wifitweak_*.deb
killall -9 Blinker
```

**注意架构**：`arm64e` 与 `arm64` 是不同的 cpusubtype，混了会「装上了但不加载」。
arm64e 设备（A12 及以上 + RootHide）用 `roothide` 那条命令，
产物 `Architecture: iphoneos-arm64e`，纯 `./Library/...` 结构（jbroot 内，与 rootful 同构，
**没有** `/var/jb` 前缀）。

### legacy/

`legacy/` 下是旧的 Theos 方案（`Makefile` / `build-theos.sh` / `control`），
已弃用，仅作参考保留。**不要把 `Makefile` 放回根目录**——它会让人以为还在用 Theos。

---

## 四、验证步骤

装好后按顺序看：

```bash
# 1) 插件是否加载（关键：构造函数必须打出这行）
idevicesyslog | grep OkaWiFi
# 期望：[OkaWiFi] 载入 pid=xxxx
#       [OkaWiFi] ✓ hook _GCDWebServerGetPrimaryIPAddress
#       [OkaWiFi] ✓ hook SCNetworkReachabilityGetFlags
#       [OkaWiFi] 页面类 = _TtC7Blinker16BNHttpServerPage
#       [OkaWiFi] AppDelegate = _TtC7Blinker11AppDelegate
#       [OkaWiFi] ✓ 新增 performActionForShortcutItem:completionHandler:

# 2) 功能 1：回桌面长按图标 → 应出现「开启 WiFi 传输」
#    点击后看日志：
#       [OkaWiFi] 快捷菜单点击 -> 开启 WiFi 传输
#       [OkaWiFi] ✓ 已创建无界面 BNHttpServerPage   （或直接找到现成页面）
#       [OkaWiFi] 已触发 changeWifiTransferStateAction:
#       [OkaWiFi] 后台保活音频 已启动
#       [OkaWiFi] GCDWebServer 已启动 port=8080 ...
#       [OkaWiFi] 最终地址: http://172.20.10.1:8080

# 3) 功能 2：关 WiFi，开个人热点，进 App 的 WiFi 传输页
#    期望：开始按钮可见、状态显示 Connected、二维码有内容、地址是热点网段 IP
#    日志应有：
#       [OkaWiFi] flags 0x00040002 -> 0x00000002 (cleared IsWWAN)
#       [OkaWiFi] iface bridge100 -> 172.20.10.1 (score 100)
#       [OkaWiFi] GetPrimaryIPAddress fallback -> 172.20.10.1
```

排错对照：

| 现象 | 原因 | 处理 |
|---|---|---|
| 完全没有 `[OkaWiFi] 载入` | 插件没被注入 | 检查 `OkaWiFi.plist` 的 Bundles 过滤、确认 `dpkg -L` 里 dylib 路径与越狱类型匹配（rootless 要在 `/var/jb` 下） |
| 有「载入」但 hook 全打 `×` | 类名/符号没找到 | 看日志里打出来的实际类名，改 `PAGE_CLASS_MANGLED` |
| 长按没菜单项 | `didFinishLaunching` 没被 hook 到 | 确认 AppDelegate 类名（日志会打）；确认不是从后台恢复（此时不会重走 didFinishLaunching，重启 App 即可） |
| 点了崩溃（`brk`） | Swift 强解包 trap（`brk #1`） | 代码已按「只有 `httpServer` 必须为真」判断，其余 6 个 outlet 是 Swift `weak`，页面没上屏时本来就该是 nil。若仍崩，抓 crash log 里 `0x100013e2x` 附近的 pc 定位到具体强解包点 |
| 能开但地址空 | IP hook 没生效 | 日志里应有 `GetPrimaryIPAddress fallback`；没有说明 `_GCDWebServerGetPrimaryIPAddress` 没 dlsym 到 |
| 开了但电脑连不上 | 进程被挂起 | 看是否有「后台保活音频 已启动」；没有说明 `Happy Dreams.mp3` 找不到，需换素材 |
| 蜂窝下按钮还是不出现 | flags 没被改写 | 看是否有 `flags 0x... -> ...` 日志；没有说明 `SCNetworkReachabilityGetFlags` 的 hook 没命中（可能是 App 静态链接了 SystemConfiguration 的私有副本，改用层 2 兜底，看 `viewDidLoad` 是否被 hook） |
| 日志没有 `✓ 解析到 _swift_unknownObjectWeakLoadStrong` | libswiftCore 没解析到该符号 | 此时所有 Swift weak outlet 都读不到（`GetIvar` 返回 nil），功能降级为「仅按钮放行」，不崩。检查 App 是否确实用 Swift weak 存 outlet（1.7.5 是） |
| 快捷菜单点了没反应，日志停在「已在运行」 | `gServerPort` 未清零 | 已 hook `-[GCDWebServer stop]` 清零。若 App 走了别的停服路径，会误判为已在运行；日志会打 `已在运行 (url=... port=...)`，据此判断 |

---

## 五、已知限制（如实说明）

1. **编译环境是 macOS，本机（Windows）无法验证真实编译。** 代码做了静态审查（括号平衡、函数定义顺序、函数指针签名与赋值、ARC 桥接配对、格式串类型），`devkit/build.sh` / `devkit/mkdeb.sh` 的 shell 语法与打包链路已在本地实测通过（三种 scheme 的 deb 结构、Architecture 字段、载荷路径、权限全部核验），但 `clang` 编译与 `codesign` 只能在 macOS 上发生。首次 CI 可能还有个别 warning 级别的类型问题。
2. **后台保活依赖音频后台模式。** App 的 `UIBackgroundModes` 是 `audio`/`fetch`，用音频钉住进程是可行的，但 iOS 会占一个「正在播放」状态（音量 0，听不到）。若你介意，可在 prefs 里关掉——但那会导致切后台后端口失效。
3. **`_GCDWebServerGetPrimaryIPAddress` 靠符号名 hook。** 若 App 后续版本换了 GCDWebServer 版本或静态链接，符号可能找不到（日志会明确报 `×`）。此时功能 2b 降级为「能开但地址空」，功能 2a 与功能 1 不受影响。
4. **地址优先级是启发式。** `bridge* > en* > pdp_ip*` 覆盖了绝大多数场景；若你同时开了 VPN 且 VPN 也建了 `bridge` 接口，可能选错。日志会打印所有候选接口和分数，便于判断。
5. **端口是 App 自己定的。** 插件只读 `uploader.port`，不改。如果 App 用随机端口，每次可能不同——这正是「点一下就把地址复制到剪贴板」的原因。

---

## 六、文件清单

```
wifitweak/
├── Tweak.xm                    # 全部实现（约 860 行，含逐条逆向依据注释）
├── OkaWiFi.plist               # CydiaSubstrate 过滤：只注入 com.blinker.documents
├── devkit/
│   ├── build.sh                # 零 Theos 编译：xcrun clang + codesign + 4 组闸门
│   ├── mkdeb.sh                # 纯 Python 拼 ar 归档（不依赖 dpkg-deb）
│   └── include/
│       └── substrate.h         # 最小 MSHookFunction 声明（Theos 的 vendor/include 替代）
├── .github/workflows/build.yml # 三变体矩阵 CI
├── legacy/                     # 旧 Theos 方案，已弃用仅作参考
│   ├── Makefile
│   ├── build-theos.sh
│   └── control
├── .gitattributes              # 锁 LF（Makefile recipe 行尾 CRLF 会让 make 报 missing separator）
├── .gitignore                  # 含 .build/ 与 .verify/
└── README.md                   # 本文件
```

### 构建产物

```
.build/
├── OkaWiFi.dylib               # 编译出的中间产物（已签名）
├── stage-<cpu>/                # 打包暂存目录
└── out/
    └── com.oka.wifitweak_<ver>_<arch>.deb   # 最终产物
```

### 可选：偏好设置

插件会读 `/var/mobile/Library/Preferences/com.oka.wifitweak.plist`，四个开关，缺省全开：

```xml
<dict>
    <key>EnableShortcut</key>    <true/>   <!-- 功能 1：长按菜单 -->
    <key>EnableCellular</key>    <true/>   <!-- 功能 2a：蜂窝/热点可开 -->
    <key>EnableIPFix</key>       <true/>   <!-- 功能 2b：热点 IP 修正 -->
    <key>CopyURLOnShortcut</key> <true/>   <!-- 开启后复制地址到剪贴板 -->
</dict>
```