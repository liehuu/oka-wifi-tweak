# Oka WiFi 传输增强（com.oka.wifitweak）

针对 **Oka Files / Blinker 1.7.5**（`com.blinker.documents`）的越狱插件，实现三组功能：

| # | 功能 | 效果 |
|---|---|---|
| 1 | 桌面长按快捷开启 | 长按图标出现「开启 WiFi 传输」，点一下直接启动服务，地址自动复制到剪贴板并弹窗提示。不用进 App、不用点按钮 |
| 2 | 蜂窝 / 热点可开启 | 用手机流量或个人热点时，WiFi 传输页面的「开始」按钮正常出现，可正常开启；且显示的地址是手机在热点网段的真实 IP（`172.20.10.1` 这类），不再是 `unknown` |
| 3 | 电脑网页端增强（v1.1.2） | 保留原 `Upload Files` 按钮；**整个页面就是放置区**（没有独立拖放框），把单个/多个文件、整个文件夹拖进网页任意位置即可上传（拖入时全屏提示 + 成功/失败汇总 + 失败重试）；**复用原版行末「移动」按钮与原弹窗**，增强为目录浏览 + 可勾选批量移动 + 结果反馈。v1.1.2 另加：**文件夹排在最上面**（组内按名称自然序，不再与文件穿插）、**顶部设备名大标题收成一行小字**（文件列表上移约 100px）、**上传任务面板默认折叠成一条**，要看明细再点「展开」 |

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

热点下接口名是 `bridge100`、蜂窝是 `pdp_ip0`，**都不是 `en0`** → 函数返回 nil → `serverURL` 返回 nil → `Helper.uploaderUrl` 返回常量 `"unknown"`，地址栏、二维码、复制按钮全都用它（v1.0.2 静态逆向确认，见「功能 2b」）。

> 这就是为什么单破解 WWAN 判定还不够：**必须在同一份补丁里修正 IP 获取**，否则「能开启但看不到地址」，等于没用。

### 4. 顺带确认的运行时事实

- Swift 类在 ObjC 运行时注册的名字是 **mangled 形式**：`_TtC7Blinker16BNHttpServerPage`（直接从 `class_ro_t.name` 读出来的）。用 `NSClassFromString(@"Blinker.BNHttpServerPage")` **取不到**，代码里做了 mangled → Swift 名 → 后缀遍历三级兜底。
- `BNFileSharingHelper` 自己的 `uploaderUrl` / `uploaderRunning` / `stopUploader` 是**纯 Swift 方法，没有 ObjC 导出**，但它们内部对 `ivar uploader @0x08`（`GCDWebUploader : GCDWebServer`）发送 `serverURL` / `isRunning` / `stop` 三个 **ObjC selector** —— `__objc_selrefs` 槽 `0xCA50` / `0xCA58` / `0xCA60` 已逐个解出确认。所以 hook 点要放在 `GCDWebServer` 上。
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

### 功能 2b —— 热点下 `unknown` 地址修正

#### 真正的根因（v1.0.2 静态逆向确认）

`unknown` **不是 App 渲染出的占位文案，而是 `BNFileSharingService.Helper.uploaderUrl` 的默认值**。
反汇编 `BNFileSharingService.framework` 的共享实现 `0x713C`：

```asm
0x715C  mov  x19, #0x6e75             ; "un"
0x7160  movk x19, #0x6e6b, lsl #16   ; "kn"
0x7164  movk x19, #0x776f, lsl #32   ; "ow"
0x7168  movk x19, #0x6e,   lsl #48   ; "n"   → Swift 小字符串 "unknown"
0x71D8  ldr  x20, #0xca50             ; __objc_selrefs 槽 -> "serverURL"
0x71EC  bl   objc_msgSend(uploader, serverURL)     ; 返回 NSURL?
0x7240  (serverURL == nil) → 直接返回 x19 = "unknown"
0x7268  bl   _$s10Foundation3URLVSgWOb             ; Foundation.URL? 转换
```

同 framework 的 `0xCA58 → isRunning`、`0xCA60 → stop` 也一并确认。完整链路：

```
_GCDWebServerGetPrimaryIPAddress 硬编码 strcmp(ifa_name,"en0")
  → 热点下没有 en0 → 返回 nil
  → -[GCDWebServer serverURL] 在 0xEE94 cbz 后 0xEF3C 返回 nil
  → Helper.uploaderUrl 返回常量 "unknown"
  → 「服务器地址」文字 / 二维码 / 复制按钮全部用它
```

**前两版一直在改 UI 控件（`ipAddress.text`），没打中源头**：Swift `weak` outlet 能否读到、控件用的是 `text` 还是 `attributedText`、App 何时刷新文案，全是不确定因素。

#### 主力修复：hook `-[GCDWebServer serverURL]`

`serverURL` 是 ObjC 方法，`method_setImplementation` 一定命中（`GCDWebUploader` 继承 `GCDWebServer`），不依赖 `MSHookFunction` 对 C 函数是否打桩成功。原 URL 的 host 是合法 IP 时原样返回，**WiFi 下零副作用**；URL 为 nil 或 host 非法时，用 `getifaddrs` 选出的本机 IP 拼一个新 NSURL 返回。端口与运行状态直接从实例取，不用猜、不用扫：

| 取值 | 来源（按优先级） |
|---|---|
| 端口 | `[self port]`（`GCDWebServer` 公开 `NSUInteger`）→ `[url port]` → `gServerPort` → 8080 |
| 是否运行 | `[self isRunning]`（Helper 自己也在用这个 selector）|

地址文字、二维码、复制按钮**一次性全部正确**；`my_stop` 会清掉端口/运行状态，停服后不会凭空造地址。
日志：`serverURL 修正: 原=[(null)] -> 新=[http://172.20.10.1:8080/] (port=8080 running=1)`。

#### 保留的两层兜底

1. `_GCDWebServerGetPrimaryIPAddress` hook：只采信 `inet_pton` 能解析的 IP（v1.0.1 的修正）。日志 `GetPrimaryIPAddress orig=... (判定=IP/非IP/空)` 用来区分「没打上桩」与「原函数返回垃圾」。
2. UI 层写回（v1.0.1）：`changeWifiTransferStateAction:` / `viewDidLoad` / reachability 之后延迟检查 `ipAddress.text` 并校正。**v1.0.2 起降为兜底**，且放宽了端口门槛——connect 探测只用于优先拿真实端口，只要 `port` / `isRunning` 有证据，探测不到也允许写回，避免把「传输本来就正常」的场景硬卡死。

本机 IP 按接口名打分（`getifaddrs`）：

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
   - 找不到就 `[[BNHttpServerPage alloc] init]` 造一个**不显示**的实例（`RetainObject` 保活），只校验 `httpServer` 非空（其余 6 个是 Swift `weak` outlet，页面没上屏时必然为 nil，不能拿来当校验条件）；
   - 若服务未运行 → 调 `changeWifiTransferStateAction:` 启动；
   - 挂**后台保活**（`AVAudioSession` Playback + 循环播放 App 自带的 `Happy Dreams.mp3`，音量 0）——否则切到后台几秒后进程被挂起、端口就断了；
   - 等 1.5s 读 App 自己写进 `ipAddress.text` 的 URL（`Helper` 是纯 Swift 类，ObjC runtime 反射不到 `uploader`）；读不到再用「本机 IP + 端口」兜底，复制到剪贴板并弹窗显示。

### 功能 3 —— 电脑网页端增强（v1.1.2）

**约束**：不能改 App 包内 `GCDWebUploader.bundle/Contents/Resources/` 的任何文件，那会破坏代码签名。所以采用「构建期把前端资源编进 dylib + 运行时注入 HTML」。

**注入点是从二进制确认的，不是猜的**：

```
0x1D1E4  dictionaryWithObjects:forKeys:count:  (w4 = 6)
0x26D40  6 个 key：device / title / header / prologue / epilogue / footer
0x1D210  ldr x1, #0x2fbc8   ; SEL "responseWithHTMLTemplate:variables:"
         mov x2, x26        ; template path
         mov x3, x27        ; variables 字典
         bl  objc_msgSend
```

`index.html` 第 92 行有 `%epilogue%`，正好在文件列表面板之后、footer 之前。插件 hook `+[GCDWebServerDataResponse(Extensions) responseWithHTMLTemplate:variables:]`（同时 hook 内部的 `-initWithHTMLTemplate:variables:` 兜底），把 `variables[@"epilogue"]` 追加一段 `<style>` + `<script>` + DOM。只有带 `epilogue` key 或模板是 `index.html` 的响应会被改，App 其它 HTML 响应不受影响；双 hook 同时命中时用 `OKA-WIFI-INJECTED` 标记保证只注入一次。

**服务端接口的实测结论**（`xhub/work/webapi-dis.py`，capstone 反汇编 `GCDWebServer`）：

| 接口 | 参数 | 行为 |
|---|---|---|
| `GET list?path=` | 目录（`/` 结尾） | JSON 数组；目录项 `path` 以 `/` 结尾，文件项不带 |
| `POST upload` | `path` = **已存在的目标目录** + multipart `files[]` | 先 `fileExistsAtPath:isDirectory:`，目录不存在直接失败 |
| `POST create` | `path` = 新目录完整路径（不带尾斜杠） | `createDirectoryAtPath:withIntermediateDirectories:` 的实参是 **`mov w3, #0`（NO）** → 父目录必须已存在、目标不能已存在 |
| `POST move` | `oldPath` / `newPath`（完整目标路径） | 目标已存在报 `Destination ... already exists` |
| `POST delete` / `GET download` | `path` | 原页面逻辑，不改 |

路径都经 `_GCDWebServerNormalizePath` 规范化。因为 `create` **不递归**，文件夹上传必须把目录按深度升序**逐级创建**，再逐个上传文件到对应子目录；重复拖入同名文件夹时，`create` 会失败，此时用 `list` 反查同名目录是否真的已存在，存在就当"目录已就绪"继续走，不吞掉真实失败。

#### v1.1.2 三项补齐（按真机使用反馈改的）

| 反馈 | 原因 | 改法 |
|---|---|---|
| 文件夹和文件穿插着排，看着乱 | 原版 `_reload()` 拿到 `GET list` 的数组后**按索引直接 append 行，自己不排序**；服务端给的是文件系统顺序 | 在 `$.ajax` 的 jqXHR 上注册**第一个** `done` 回调，对数组**原地 `sort`**：目录组在前、文件组在后，组内按名称自然序（`localeCompare` 带 `numeric:true`，`2.log` 排在 `10.log` 前）。原版随后注册的 `.done` 拿到的是同一个数组对象，渲染出来就是有序的。**必须在 IIFE 顶层包装**，因为原版 ready 回调里就会调 `_reload("/")`；注入脚本在 epilogue（第 92 行），晚于 index.js（第 53 行）但早于 DOMContentLoaded，能覆盖首屏。只认「每项都是 `{path,name,size}` 的数组」这一结构特征，不枚举 URL 白名单，不会误伤 upload/create/move 的对象响应 |
| 顶部「xhub」那几个字太占地方，文件都显示在很下面 | Bootstrap 3 的 `.page-header` 是 `margin:40px 0 20px` + 36px `h1`，`index.css` 又给 `.btn-toolbar` 加了 `margin-top:30px`，合计约 100px | JS 在 `body` 上加 `oka-compact`，CSS 用 `body.oka-compact .container > ...` 作用域收紧：标题保留但降到 20px 一行，标题区/工具栏/面板边距一并压缩。**不全局覆盖 Bootstrap 原类**，关掉 `EnableWebEnhance` 页面自然回到原版 |
| 上传任务那个框太占地方 | v1.1.0/1.1.1 有任务时就展开整块明细列表 | 任务面板**默认折叠**：只显示一条头部（汇总 + 「展开」按钮 + 清除/重试/停止三个操作），明细区 `display:none`，要看逐项进度再点「展开」。每批上传开始都回到折叠；结果条仍在原 `#alerts`，折叠状态下反馈与重试都可用 |

#### v1.1.1 的交互定型（按真机使用反馈改的）

v1.1.0 曾单独放了一块占大半个页面的虚线拖放框，又另起了一个「移动选中项」按钮和弹窗。这两处都不合理，v1.1.1 全部去掉：

| v1.1.0 | v1.1.1 |
|---|---|
| 独立拖放框，常驻占版面 | **整个页面本身就是放置区**，不新增任何常驻可见区块；只有拖入时才浮出全屏「松开即可上传」提示 |
| 独立的「移动选中项」按钮 + 独立弹窗 | **复用原版行末 `.button-move` 与原版 `#move-modal`**，把「只能手填地址」替换成目录浏览 + 批量选中 + 结果反馈 |
| 独立结果容器 | 结果条插进原页面的 `#alerts`，与原版告警同款 |
| 常驻上传任务面板 | 任务面板默认 `display:none`，只有真正开始上传才出现；「全选 / 取消选择」塞进原 `.btn-toolbar` 同一行 |

**前端（`web/oka-wifi.js` / `web/oka-wifi.css`）**：

- 原 `Upload Files` 按钮、`#fileupload` 的 `change` 流程、`#move-input`、`#move-confirm`（按钮换绑，元素仍是原版那个）、`_reload()`、`#listing` 渲染**全部保留**；
- 拖放在 `document` 的**捕获阶段**接管：`preventDefault` + `stopPropagation` 既阻止浏览器打开文件，也阻止事件冒泡到 jQuery File Upload 绑在 `$(document)` 上的 `dropZone` handler，避免双份上传；改成自己的上传管线，因为原 `add` 回调把 `formData.path` 写死为 `_path`，无法给子目录指定路径；
- 用 `DataTransferItem.webkitGetAsEntry()` 递归展开文件夹（`readEntries` 单次最多 100 条，循环读到空为止）；浏览器不给 entry API 时降级为 `dataTransfer.files`（单/多文件，无目录结构）；
- 上传用 `XMLHttpRequest` + `FormData`，`xhr.upload.onprogress` 更新逐项进度条；串行执行，任务面板显示等待/进行中/完成/失败/跳过与汇总，支持「重试失败项」和「停止剩余」，结束调用原 `_reload(_path)` 刷新；
- 列表多选只用 `MutationObserver` 往新行**前面追加一个复选框 td**，不动原渲染；
- **移动入口接管**：原版 `.button-move` 是每次 `_reload` 直接绑到按钮上的，冒泡阶段拦不住，所以在 `document` 的**捕获阶段** `stopPropagation()`，改为打开同一个 `#move-modal`。原版 `#move-confirm` 的直接 `.click()` handler 用 `.off('click')` 摘掉，换成批量 `POST move`；`#move-input` 的 Enter → `$("#move-confirm").click()` 原样复用，目录浏览点一下就把路径写进这个输入框，也可以继续手填。未勾选时只移动你点的那一项，勾选多个则整批移动。完成后结果留在弹窗内并刷新列表，确认按钮保持禁用直到下次打开，避免重复提交。

**构建期**：`devkit/gen-webassets.py` 把 JS/CSS 按 UTF-8 字节转成 C 字符串常量写到 `.build/OkaWebAssets.h`（非可打印字节用固定 3 位八进制，避免 `\xNN` 吞后续十六进制字符），并做**往返解码自检**；`build.sh` 用 `-I .build` 编进 dylib，再用功能指纹闸门确认注入点 selector 与 v1.1.1 新组件标记都真的在产物里，**外加一条负向闸门**：v1.1.0 的独立拖放框 / 独立弹窗 class/id 只要还出现在产物字节里就直接 `exit 1`，防止旧交互回流。

---

## 三、编译

**不需要 Theos。** 构建是一台装了 Xcode 的 macOS（或 GitHub Actions 的 `macos-latest`）上跑三条命令：

```bash
sh devkit/build.sh arm64e iphoneos-arm64e 1.1.2   # RootHide / roothide
sh devkit/build.sh arm64  iphoneos-arm64  1.1.2   # rootless（Dopamine / palera1n）
sh devkit/build.sh arm64  iphoneos-arm    1.1.2   # 传统 rootful
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

8. **`$CPU，` 在 macOS 上会 `unbound variable`。** 这是 CI run `42346759489`
   的真实失败：编译和 codesign 都成功了，卡在 `build.sh` 的一行 `echo`。

   ```sh
   echo "✓ 架构精确匹配（$CPU，单切片）"     # ✗ 炸
   echo "✓ 架构精确匹配（${CPU}，单切片）"   # ✓ 好
   ```

   bash 3.2（macOS 自带）用 `legal_variable_char()` → BSD libc 的 `isalnum()`
   判断变量名边界，UTF-8 locale 下把中文全角标点的首字节 `0xEF` 判成「字母」，
   于是变量名被解析成 `CPU\xEF`；`set -u` 一查没有这个变量就退出。
   报错文本里 `0xEF` 不可打印，显示成 `CPU: unbound variable`，
   看起来就像变量名本来就叫 `CPU`，极难定位。
   **本机 bash 5 / dash 的变量名解析是严格 ASCII，本地跑不出来**，
   只有 macOS 会炸。→ 一律写 `${VAR}`；并加 `devkit/check-shell.py` 静态兜住。

9. **YAML 里不能内嵌带缩进的 Python heredoc。** 曾经把 deb 校验写在
   `run: |` 的 `<<'PY'` 里，两个要求互斥：
   YAML block scalar 以「第一个非空行的缩进」为基准，内容顶格会让块立即结束
   （`ScannerError`）；而 Python heredoc 体带缩进又必然
   `IndentationError`。→ 校验逻辑外置成 `devkit/verify-deb.py`，
   workflow 里只留一行调用，本地也能直接跑。

### 闸门（全部硬失败，不是警告）

| 脚本 | 作用 | 在哪跑 |
|---|---|---|
| `devkit/check-shell.py` | 拦 `$VAR` 后紧跟非 ASCII、以及 bash 4+ 专有语法（`declare -A`、`${v,,}`、`&>>`、`\|&`、`mapfile`、`wait -n`） | CI 独立步骤；本地可跑 |
| `devkit/verify-deb.py --check-macho` | 编译后立刻读 Mach-O 头的 `cputype/cpusubtype` 做**整数比较**，并检查 `LC_CODE_SIGNATURE` | `build.sh` 内；CI 同代码 |
| `devkit/verify-deb.py <deb> <scheme> <arch>` | 校验 deb 的 ar 结构、Architecture、载荷路径、DEBIAN 隔离、PAX 头、架构、签名、权限（共 22 项） | CI 的 Verify deb 步骤；本地可跑 |
| `devkit/selftest-verify.py` | **证明上面这个校验器会失败**：15 个用例（正向 3 / 反向 12），用代码拼出的 Mach-O 与 deb，含「arm64e 冒充 arm64」「缺签名」「PAX」「权限错」等 | CI 独立步骤；本地可跑 |
| `devkit/gen-webassets.py` | 生成内联前端资源头；含危险字面量检查（`</script`、`%key%` 形状）与**往返解码自检**（生成后把 C 字面量解回字节，与源文件 sha256 比对，落盘复读再比一次） | `build.sh` 内；CI 独立步骤也跑一次 |
| `devkit/selftest-webassets.py` | **证明上面这个资源闸门会失败**：7 个用例，含注入 `</script`、注入 `%epilogue%`、删文件、空文件、篡改 C 转义应被解码检出、还原后复跑干净 | CI 的 Web assets gate；本地可跑 |
| `node --check web/oka-wifi.js` | 用真实 JS 解析器验语法（不是正则数括号）；runner 缺 node 时**明确报错**，不静默跳过 | CI 的 Web assets gate |

`verify-deb.py` 用纯标准库自己解析 Mach-O，不调 `lipo`/`otool`：
`lipo -info` 输出的是句子，`grep -q arm64` 对 `arm64e` 必然假通过（坑 2），
而且 `lipo` 只有 macOS 有，判据等于只在 CI 生效。`--check-macho` 与 CI 的
deb 校验共用同一份代码，不会出现「本地过了 CI 挂」或「CI 过了真机不加载」。

所有闸门都做过**反向注入三段式**：注入缺陷 → 必须报错且不产出坏包 →
还原 → 复跑干净。`selftest-verify.py` 把这件事固化成可重复执行的测试。

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
#       [OkaWiFi] ✓ hook -[GCDWebServer serverURL]        ← v1.0.2 主力，必须有
#       [OkaWiFi] ✓ hook -[GCDWebServer startWithPort:bonjourName:]
#       [OkaWiFi] ✓ hook -[GCDWebServer stop]
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

# 3) 功能 2：关 WiFi，开个人热点，进 App 的 WiFi 传输页，点「开启 WiFi 传输」
#    期望：按钮可见、状态 Connected、二维码有内容，
#          服务器地址 = http://172.20.10.1:8080/（不再是 unknown）
#    日志应有：
#       [OkaWiFi] flags 0x00040002 -> 0x00000002 (cleared IsWWAN)
#       [OkaWiFi] iface bridge100 -> 172.20.10.1 (score 100)
#       [OkaWiFi] serverURL 修正: 原=[(null)] -> 新=[http://172.20.10.1:8080/] (port=8080 running=1)

# 4) 功能 3：电脑浏览器打开 http://<手机IP>:8080
#    a) 页面默认外观应与原版几乎一致：没有独立拖放框、没有多出来的面板；
#       顶部设备名（xhub 那行）应是一行小字，文件列表明显比原版靠上；
#       只是原按钮那一行多了「全选 / 取消选择」，文件列表每行前面多了复选框。
#       原 Upload Files 按钮照常可用。
#    b) 从电脑文件管理器拖 1 个文件 / 多个文件 / 整个文件夹到页面**任意位置**
#       期望：拖入时浮出全屏「松开即可上传」提示 → 任务面板只出现**一条折叠小条**
#             （上面有汇总、展开、清除/重试/停止），点「展开」才看到逐项进度
#             → 原 #alerts 位置显示成功/失败数 → 列表自动刷新
#       文件夹：手机上应保留完整目录结构（含多级子目录）
#    c) 在文件列表勾选多项 → 点**任意一行的原版「移动」按钮** → 弹出的仍是原版弹窗，
#       但多了「本次移动 N 项」和目录浏览 → 点目录或直接填 #move-input → 点「移动」
#       期望：弹窗内显示成功/失败汇总，列表自动刷新，文件出现在目标目录
#       （不勾选时只移动你点击的那一项；移动后按钮保持置灰，关掉再打开才恢复）
#    d) 排序：随便进一个同时有文件夹和文件的目录，文件夹应全部排在文件上方，
#       组内按名称排（2.log 在 10.log 前）。新建文件/文件夹后点 Refresh 仍保持有序
#    插件加载时日志应有：
#       [OkaWiFi] ✓ hook +[GCDWebServerDataResponse responseWithHTMLTemplate:variables:]
#       [OkaWiFi] ✓ hook -[GCDWebServerDataResponse initWithHTMLTemplate:variables:]
#       [OkaWiFi] 网页增强注入层已挂载（JS=... CSS=... 字节）
#    浏览器里查看源码，<body> 里应有且仅有一处 <!-- OKA-WIFI-INJECTED -->；
#    源码里不应出现独立拖放框或第二个移动弹窗
```

排错对照：

| 现象 | 原因 | 处理 |
|---|---|---|
| 完全没有 `[OkaWiFi] 载入` | 插件没被注入 | 检查 `OkaWiFi.plist` 的 Bundles 过滤、确认 `dpkg -L` 里 dylib 路径与越狱类型匹配（rootless 要在 `/var/jb` 下） |
| 有「载入」但 hook 全打 `×` | 类名/符号没找到 | 看日志里打出来的实际类名，改 `PAGE_CLASS_MANGLED` |
| 长按没菜单项 | `didFinishLaunching` 没被 hook 到 | 确认 AppDelegate 类名（日志会打）；确认不是从后台恢复（此时不会重走 didFinishLaunching，重启 App 即可） |
| 点了崩溃（`brk`） | Swift 强解包 trap（`brk #1`） | 代码已按「只有 `httpServer` 必须为真」判断，其余 6 个 outlet 是 Swift `weak`，页面没上屏时本来就该是 nil。若仍崩，抓 crash log 里 `0x100013e2x` 附近的 pc 定位到具体强解包点 |
| 能开但地址显示 `unknown` | `serverURL` hook 没生效 / 类没找到 | 第一判据是日志里有没有 `✓ hook -[GCDWebServer serverURL]`；有 hook 再看有没有 `serverURL 修正: 原=[(null)] -> 新=[http://...]`。这两行齐了，地址文字、二维码、复制按钮会一起正确。UI 层写回只是兜底，不必看它 |
| 开了但电脑连不上 | 进程被挂起 | 看是否有「后台保活音频 已启动」；没有说明 `Happy Dreams.mp3` 找不到，需换素材 |
| 蜂窝下按钮还是不出现 | flags 没被改写 | 看是否有 `flags 0x... -> ...` 日志；没有说明 `SCNetworkReachabilityGetFlags` 的 hook 没命中（可能是 App 静态链接了 SystemConfiguration 的私有副本，改用层 2 兜底，看 `viewDidLoad` 是否被 hook） |
| 日志没有 `✓ 解析到 _swift_unknownObjectWeakLoadStrong` | libswiftCore 没解析到该符号 | 此时所有 Swift weak outlet 都读不到（`GetIvar` 返回 nil），功能降级为「仅按钮放行」，不崩。检查 App 是否确实用 Swift weak 存 outlet（1.7.5 是） |
| 快捷菜单点了没反应，日志停在「已在运行」 | `gServerPort` 未清零 | 已 hook `-[GCDWebServer stop]` 清零。若 App 走了别的停服路径，会误判为已在运行；日志会打 `已在运行 (url=... port=...)`，据此判断 |
| CI 报 `CPU: unbound variable`，但编译和签名都成功了 | 某行 `echo` 里写了 `$CPU，`（后跟全角标点） | macOS 的 bash 3.2 把 `0xEF` 吞进变量名。改 `${CPU}`，并跑 `python3 devkit/check-shell.py .` 扫全仓库 |
| CI 的 Verify deb 步骤报 `IndentationError` 或 YAML `ScannerError` | 在 `run: \|` 里内嵌了 Python heredoc | 这两个错误互斥、无解：YAML 要缩进、Python 要顶格。校验逻辑必须外置成 `.py` 文件 |
| 蜂窝/热点下能开启，但服务器地址显示 `unknown` | `Helper.uploaderUrl` 在 `serverURL == nil` 时返回 Swift 常量 `"unknown"`（framework `0x713C` 的 `mov/movk` 立即数已确认）。前两版改 UI 控件没打中源头 | **v1.0.2 直接 hook `-[GCDWebServer serverURL]`** 返回正确 NSURL。日志应有 `✓ hook -[GCDWebServer serverURL]` 与 `serverURL 修正: 原=[(null)] -> 新=[http://172.20.10.1:8080/] (port=8080 running=1)`。应急仍可用电脑浏览器直接开 `http://172.20.10.1:8080` |
| 日志里没有 `GetPrimaryIPAddress orig=...` | `MSHookFunction` 在该 framework 上没真正打上桩（`✓ hook` 只代表 `dlsym` 找到符号） | **v1.0.2 起不影响主修复**：`serverURL` 是 ObjC 方法，走 `method_setImplementation`，与本 C 函数 hook 完全独立 |
| 日志没有 `✓ hook -[GCDWebServer serverURL]` | `GCDWebServer` 类没找到（framework 未加载 / 类名不同） | 构造函数已改为 `dlopen(..., RTLD_LAZY)` 主动加载两种路径。若仍缺，看是否打印 `× 找不到 GCDWebServer 类`；此时只剩 UI 层写回兜底 |
| 日志出现 `端口自证: 无端口在 LISTEN，且 port / isRunning 都无证据 → 不改地址` | 服务确实没在跑，或 hook 没拿到实例状态 | 这是**故意不写假地址**。v1.0.2 起端口由 `[self port]` 直取，探测不到也会采信；若电脑实测能连上却仍显示 unknown，把整段日志发来改判据 |
| 网页里没有复选框 / 拖了没反应 | 模板注入没生效（v1.1.1 默认外观本就与原版几乎一致，**不要拿「有没有新增面板」当判据**） | 先看日志有没有两条 `✓ hook ...HTMLTemplate:variables:` 与 `网页增强注入层已挂载`；再在浏览器「查看网页源代码」搜 `OKA-WIFI-INJECTED`，并确认 `#listing tr.row-file` 行首有没有 `.oka-checkbox`。日志齐但页面没有 → 强刷（Ctrl+F5）排除缓存；日志缺 → 说明 `GCDWebServerDataResponse` 类没找到，看是否打印 `× 找不到 ...` |
| 文件夹和文件还是穿插着排 | 列表排序没生效 | 先在控制台确认 `window.__okaWiFiLoaded` 为真，再在网页源码里搜 `sortListingInPlace`；都没有说明注入层根本没加载。**首屏就乱**通常是包装 `$.ajax` 的时机被改到了 `$(document).ready()` 里（必须留在 IIFE 顶层，才能赶在原版 ready 里的 `_reload("/")` 之前）。若只有某个目录乱，把该目录 `GET list` 的原始 JSON 发来——排序只认「每项都有 `path`/`name` 字符串」的数组，结构不符会原样放过 |
| 顶部还是很空 / 上传任务框还是占地方 | `body.oka-compact` 没加上，或注入的 CSS 被缓存了 | 控制台看 `document.body.classList.contains('oka-compact')`；为真但样式没变就强刷（Ctrl+F5）。任务面板的折叠由 `setTasksExpanded(false)` 控制，若上传后明细直接展开，检查 `startUpload()` 里那行是否被删；也可直接点头部「展开 / 收起」验证切换是否可用 |
| 页面源码里出现两次 `OKA-WIFI-INJECTED` | 幂等标记失效（类方法 + 实例方法双 hook 都注入了） | 正常情况下 `OkaAugmentVariables` 会在第二次命中时直接返回原 variables。若重复，检查 `rangeOfString:@"OKA-WIFI-INJECTED"` 是否被改掉 |
| 拖入文件夹后只剩顶层文件、子目录丢了 | `create` 用 `withIntermediateDirectories:NO`，父目录不存在时整条链会失败 | 任务面板会显示每个目录的失败原因。代码已按深度升序逐级创建并对「目录已存在」做 `list` 反查；若仍失败，把任务面板的失败明细发来 |
| 拖放后出现两份上传 / 进度条乱跳 | 捕获阶段的 `stopPropagation` 没挡住 jQuery File Upload 在 `$(document)` 上的 drop handler | 检查是否有其它插件/浏览器扩展改写了 drag/drop；本插件的 `onDrop` 必须同时 `preventDefault()` 与 `stopPropagation()` |
| 点行末「移动」按钮还是原版那个只有路径输入框的弹窗 | 捕获阶段接管没生效（v1.1.1 仍复用原版 `#move-modal`，**弹窗长得很像原版是正常的**，判据是里面有没有「本次移动 N 项」和目录浏览） | 先在浏览器控制台确认 `window.__okaWiFiLoaded` 为真；再看源码里有没有 `onMoveButtonCapture`。若原版 handler 仍执行，会出现「弹窗立刻自动关闭 + 只发 1 个 POST move」——这是 `.off('click')` 没摘掉 `#move-confirm` 旧绑定的特征 |
| 移动完成后按钮点不动 | 故意设计：结果留在弹窗里给用户看，确认按钮保持禁用以防重复提交 | 关掉弹窗，重新点任意一行的「移动」按钮即可恢复 |
| 批量移动全部报失败 | `newPath` 目标已存在，或目标目录不存在 | 弹窗里逐项列出失败原因。服务端 `move` 在目标已存在时返回 `Destination ... already exists`；目录浏览只列 `GET list` 里真实存在的目录，手动输入路径时先确认它存在 |

---

## 五、已知限制（如实说明）

1. **编译环境是 macOS，本机（Windows）无法验证真实编译。** 代码做了静态审查（括号平衡、函数定义顺序、函数指针签名与赋值、ARC 桥接配对、格式串类型）。本机已用「桩替换 `xcrun`」把 `build.sh` 三个变体从编译一路跑到打包全部 `exit=0`，并在本地用 `verify-deb.py` 把三个 deb 的 22 项检查全部跑通；`shell` 语法、打包链路、校验判据的正反方向都已实测。但**真正的 `clang` 编译与 `codesign` 只能在 macOS 上发生**——桩产出的 dylib 不是可运行代码，本地 deb 也**不能装到手机上**。唯一可交付产物必须来自 CI。
2. **后台保活依赖音频后台模式。** App 的 `UIBackgroundModes` 是 `audio`/`fetch`，用音频钉住进程是可行的，但 iOS 会占一个「正在播放」状态（音量 0，听不到）。若你介意，可在 prefs 里关掉——但那会导致切后台后端口失效。
3. **`_GCDWebServerGetPrimaryIPAddress` 靠符号名 hook。** 若 App 换 GCDWebServer 版本或静态链接，符号可能找不到（日志会报 `×`）。这层失效**不影响 v1.0.2 的主力修复**（`serverURL` 是 ObjC 方法 hook）；只有 `GCDWebServer` 类本身找不到时才会退化到 UI 层写回。功能 2a 与功能 1 始终不受影响。
4. **地址优先级是启发式。** `bridge* > en* > pdp_ip*` 覆盖了绝大多数场景；若你同时开了 VPN 且 VPN 也建了 `bridge` 接口，可能选错。日志会打印所有候选接口和分数，便于判断。
5. **端口是 App 自己定的，插件不改。** v1.0.2 直接从 `[self port]` 读真实端口（`GCDWebServer` 公开属性）、`isRunning` 读运行状态，`stop` 时清零；不再靠猜 8080，也不再依赖 `startWithPort:` 是否被命中。端口 LISTEN 探测只保留为 UI 兜底层的补充手段。
6. **网页增强依赖电脑浏览器的 `DataTransferItem.webkitGetAsEntry()`。** Chrome / Edge / Safari 支持整个文件夹递归；不提供该 API 的浏览器会自动降级为「单文件 / 多文件」拖放上传，文件夹结构无法保留（点 `Upload Files` 选文件始终可用）。另外服务端 `create` 不递归、也不允许目标已存在，所以文件夹上传是**串行**的：先逐级建目录，再逐个传文件，大文件夹会比一次多文件选择慢。
7. **注入层依赖 `responseWithHTMLTemplate:variables:` 这条渲染路径。** 已在 1.7.5 的 `GCDWebServer` 二进制里反汇编确认（6 个 variables key 的字典 + `0x1D210` 的类方法调用）。若 App 升级换了 GCDWebServer 版本、改走别的 HTML 渲染方法，注入会静默不生效——但功能 1 / 2 完全不受影响，网页也会保持原版可用状态。

---

## 六、文件清单

```
wifitweak/
├── Tweak.xm                    # 全部实现（约 1230 行，含逐条逆向依据注释）
├── OkaWiFi.plist               # CydiaSubstrate 过滤：只注入 com.blinker.documents
├── web/                        # v1.1.2 电脑网页端增强前端资源（构建期内联进 dylib）
│   ├── oka-wifi.js             # 列表排序 / 全页拖放 / 文件夹递归 / 折叠任务面板 / 复用原版移动弹窗
│   └── oka-wifi.css            # oka- 前缀样式 + body.oka-compact 作用域的顶部紧凑布局
├── devkit/
│   ├── build.sh                # 零 Theos 编译：资源内联 + xcrun clang + codesign + 闸门
│   ├── mkdeb.sh                # 纯 Python 拼 ar 归档（不依赖 dpkg-deb）
│   ├── gen-webassets.py        # JS/CSS -> .build/OkaWebAssets.h（含往返解码自检）
│   ├── selftest-webassets.py   # 上面这个资源闸门的自测：7 用例，证明它会失败
│   ├── check-shell.py          # 静态闸门：bash 3.2 兼容性（本地也能跑）
│   ├── verify-deb.py           # deb / 裸 dylib 校验（纯标准库，自己读 Mach-O）
│   ├── selftest-verify.py      # 上面这个校验器的自测：15 用例，证明它会失败
│   └── include/
│       └── substrate.h         # 最小 MSHookFunction 声明（Theos 的 vendor/include 替代）
├── .github/workflows/build.yml # 三变体矩阵 CI（不内嵌 Python heredoc）
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
├── OkaWebAssets.h             # gen-webassets.py 生成的内联前端资源（.gitignore 忽略）
├── OkaWiFi.dylib               # 编译出的中间产物（已签名）
├── stage-<cpu>/                # 打包暂存目录
└── out/
    └── com.oka.wifitweak_<ver>_<arch>.deb   # 最终产物
```

### 可选：偏好设置

插件会读 `/var/mobile/Library/Preferences/com.oka.wifitweak.plist`，五个开关，缺省全开：

```xml
<dict>
    <key>EnableShortcut</key>    <true/>   <!-- 功能 1：长按菜单 -->
    <key>EnableCellular</key>    <true/>   <!-- 功能 2a：蜂窝/热点可开 -->
    <key>EnableIPFix</key>       <true/>   <!-- 功能 2b：热点 IP 修正 -->
    <key>EnableWebEnhance</key>  <true/>   <!-- 功能 3：网页拖放上传 / 批量移动 -->
    <key>CopyURLOnShortcut</key> <true/>   <!-- 开启后复制地址到剪贴板 -->
</dict>
```

把 `EnableWebEnhance` 设为 `false` 可让网页回到原版（原 `Upload Files`、原 `Move Item` 弹窗照常可用）。