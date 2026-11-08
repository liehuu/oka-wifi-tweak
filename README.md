# Oka WiFi 传输增强（com.oka.wifitweak）

针对 **Oka Files / Blinker 1.7.5**（`com.blinker.documents`）的越狱插件，实现三组功能：

| # | 功能 | 效果 |
|---|---|---|
| 1 | 桌面长按快捷开启 | 长按图标出现「开启 WiFi 传输」，点一下直接启动服务，地址自动复制到剪贴板并弹窗提示。不用进 App、不用点按钮 |
| 2 | 蜂窝 / 热点可开启 | 用手机流量或个人热点时，WiFi 传输页面的「开始」按钮正常出现，可正常开启；且显示的地址是手机在热点网段的真实 IP（`172.20.10.1` 这类），不再是 `unknown` |
| 3 | 电脑网页端增强（v1.1.7） | 保留原 `Upload Files` 按钮；**整个页面就是放置区**（没有独立拖放框），把单个/多个文件、整个文件夹拖进网页任意位置即可上传（拖入时全屏提示 + 成功/失败汇总 + 失败重试）；**复用原版行末「移动」按钮与原弹窗**，增强为目录浏览 + 可勾选批量移动 + 结果反馈。v1.1.2：**文件夹置顶**、**顶部紧凑**、**任务面板默认折叠**；v1.1.5：文件夹琥珀黄 + 行内重命名按钮；**v1.1.6：删除按钮挪到最右、重命名在它左边；文件名左侧整片区域点一下即选中；每行新增下载按钮，文件直接下载、整个文件夹自动打成 ZIP；点行不再下载**；**v1.1.7：点文件夹行进入目录、点文件行在线预览 txt / pdf / mp4 / 图片 / 音频；目录 ZIP 显式带顶层文件夹；图标列按文件类型显示不同图标（图片 / 视频 / 音频 / 压缩包 / 文档 / 文本），不再满屏下载箭头** |

> **v1.1.7：四项网页改进。** ① **点文件夹所在行恢复进入文件夹**（v1.1.6 把行级 handler 整个删掉，连文件夹都进不去了），点文件所在行改为**在线预览**；
> ② **目录 ZIP 显式写入顶层文件夹条目**：v1.1.6 只写了 `Docs/a.txt` 这类带前缀的文件条目，部分解压工具不建隐式目录，解压出来是散装文件，现在 ZIP 第一条就是 `Docs/`；
> ③ **点文件所在行即可预览** `txt / log / json / pdf / mp4 / mp3 / png` 等，前端用原生 XHR 取回 `download` 的字节后用 Blob URL 渲染（弹窗默认隐藏、关闭即释放），不改服务端、不影响上传与移动；
> ④ **图标列按文件类型换字形**：原版对所有文件都用同一个下载箭头，看不出类型、还和每行下载按钮撞车，现在图片 / 视频 / 音频 / 压缩包 / 文档 / 文本各用各的图标并分色，目录仍是琥珀黄文件夹。
> 预览是**只读**的；「编辑完保存回手机」仍需服务端新增覆盖写回接口，见下方可行性结论。

> **v1.1.6：网页交互三项（按真机反馈改）。** ① 行末按钮重排：**删除在最右、重命名紧挨左边**，
> 并在原版「移动」按钮**左边**新增一个下载按钮；② **文件名左边所有区域（复选框列 + 图标列）
> 点击即选中**，原版「点文件图标就下载」的绑定在捕获阶段拦掉，图标只作视觉标识；
> ③ **取消点行下载**：点名称所在行不再有动作，下载统一走每行新增的按钮。
> 目录下载由浏览器端递归 `GET list` + 逐文件 `GET download` + 零依赖 ZIP 打包完成
> （服务端 `download?path=<目录>` 会返回 400，见下）。原上传 / 移动 / 排序 / 折叠面板全部保留。

> **v1.1.5：网页交互三项（按真机反馈改，其中「点行即下载」已在 v1.1.6 取消）。** ① 文件夹与文件不再「颜色太相似」：
> 目录行用 Windows 资源管理器式琥珀黄（图标按钮、文件夹图标、名称、行首色条），文件保持中性蓝灰；
> ② 原来点名称那一列会触发原版 jeditable 的重命名（名称列最宽，感觉就是「点行 = 重命名」），
> v1.1.5 曾改成**点行 = 文件下载 / 文件夹进入**（v1.1.6 一度改为点行无动作 + 独立下载按钮，v1.1.7 已恢复为目录进入 / 文件预览）；
> ③ **重命名改为每行一个按钮**，点开内联输入框，Enter 提交 / Esc 取消，提交仍走原版
> `POST move {oldPath, newPath: 当前目录 + 新名}`。原 `Upload Files`、移动弹窗、拖放上传、
> 排序、紧凑布局、折叠任务面板与 v1.1.4 的选择重置全部保留。

> **v1.1.4：修复「操作后勾选残留」。** 勾选 A 删除/移动后，下次勾选 B 点移动，
> 「已选择项目」里不会再带上 A。列表每次重建（删除/移动/上传/新建/切目录/刷新）都会重置选择，
> 打开移动弹窗时也会剔除已不存在的幽灵项；关闭弹窗同样视为一次操作结束。
> 复现方式：把 v1.1.3 的 JS 换回来跑同一批浏览器用例，8 条断言失败（删除后计数仍为 1，
> 关弹窗后累积到 3 项）；v1.1.4 下 106/106 全过。

> **v1.1.3：诊断日志已全部移除。** 插件运行时不再输出任何 `NSLog` / `[OkaWiFi] ...` 行；
> 功能 1 / 2 / 3 完全不变，该版前端资源与 v1.1.2 逐字节相同（v1.1.4 又改了 JS，见上）。
> 判断插件是否生效请直接看功能表现（长按菜单、蜂窝下能开启且地址正常、网页增强），
> 不要再去抓 syslog。构建闸门会从 `Tweak.xm` 源码和编译出的 dylib 原始字节两侧反查
> `LOG(` / `NSLog` / `[OkaWiFi]`，防止日志回流。

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
判定以界面显示为准（v1.1.3 起不再输出日志）。

#### 保留的两层兜底

1. `_GCDWebServerGetPrimaryIPAddress` hook：只采信 `inet_pton` 能解析的 IP（v1.0.1 的修正）。这层失效不影响 v1.0.2 的 `serverURL` 主力修复。
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

### 功能 3 —— 电脑网页端增强（v1.1.1 定型，v1.1.2 ~ v1.1.7 持续增强）

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
| `GET list?path=` | 目录（`/` 结尾） | JSON 数组；目录项 `path` 以 `/` 结尾，文件项不带；服务端已按 `localizedStandardCompare:` 排序，但**不区分目录/文件**，所以会穿插 |
| `GET download?path=` | 文件路径 | `GCDWebServerFileResponse ... isAttachment:YES`；**`path` 指向目录时直接 `400 Bad Request`（`"%@" is a directory`）** |
| `POST upload` | `path` = **已存在的目标目录** + multipart `files[]` | 目标路径过 `_uniquePathForPath`：同名文件自动改成 `name (1).ext`，**不覆盖** |
| `POST create` | `path` = 新目录完整路径（不带尾斜杠） | `withIntermediateDirectories:NO` → 父目录必须已存在、目标不能已存在，不递归 |
| `POST move` | `oldPath` / `newPath`（完整目标路径） | `newAbsolutePath` 同样过 `_uniquePathForPath`：目标已存在时自动改名，不覆盖也不报错 |
| `POST delete` | `path` | 原页面逻辑，不改 |

**只有以上 6 个接口**（另有用 `GCDWebUploader.m` 源码交叉核对）：没有「把文件内容 inline 读出来」、
`Content-Disposition: inline` 预览、覆盖写回、服务端打包 ZIP 之类的隐藏接口。

路径都经 `_GCDWebServerNormalizePath` 规范化。因为 `create` **不递归**，文件夹上传必须把目录按深度升序**逐级创建**，再逐个上传文件到对应子目录；重复拖入同名文件夹时，`create` 会失败，此时用 `list` 反查同名目录是否真的已存在，存在就当"目录已就绪"继续走，不吞掉真实失败。

#### v1.1.7：点目录进入 / 点文件预览 / 目录 ZIP 保留顶层文件夹 / 按类型换图标

| 反馈 | 根因 | 改法 |
|---|---|---|
| 点击文件夹所在行应该能进入文件夹，现在都进不去了 | v1.1.6 为取消「点行即下载」把**整个行级 click handler** 删掉，目录进入也一起没了 | 恢复 `$(document).on('click', '#listing tr.row-file', onRowAction)`：目录行调原版 `window._reload(d.path)`，文件行调 `openPreview(d)`。左侧热区在捕获阶段 `stopPropagation`，各按钮在自己 handler 里阻止冒泡，都不受影响；原版 `.button-delete` 没有 `stopPropagation`，所以 `onRowAction` 里显式排除 `button / a / input / select / textarea / .btn / .oka-rename-input` |
| 下载文件夹得到的是文件夹里的各个文件，没有那个文件夹 | v1.1.6 的 ZIP 只有 `Docs/notes.txt` 这类**带前缀的文件条目**，没有显式写入 `Docs/` 这条 0 字节目录条目；部分解压工具不创建隐式目录 | `downloadDirectoryAsZip()` 把 `acc.dirs` 初始化为 `[rootRel]`，ZIP 第一条就是顶层文件夹。完成汇总里的子目录数用 `acc.dirs.length - 1`，不把顶层算进去 |
| 能否点文件所在行直接打开 txt / pdf / mp4，起码预览一下 | 服务端 `download` 是 `isAttachment:YES`，直接在标签页打开只会下载；但**前端可以把字节取回页面内存**再渲染 | 原生 XHR 读 `download?path=` → `Uint8Array` → `Blob` → `URL.createObjectURL`：文本走 `<pre>`、PDF 走 `<iframe>`、图片/视频/音频走 `<img>/<video>/<audio>`；按**扩展名优先**判类型（服务端对 `.log` 常给 `application/octet-stream`），MIME 兜底。弹窗默认隐藏、关闭时清空 body + revoke Blob URL；超过 200 MB 或扩展名不认识就只给「下载原文件」，不把大文件读进内存 |

**预览不改服务端**：仍然只用现有的 `GET download`，不需要 inline 接口，也不做覆盖写回。
预览请求走原生 `XMLHttpRequest`，与被包装过的 `$.ajax` 完全隔离，不会触发排序或清空选中。
左侧热区、每行下载按钮、上传 / 移动 / 重命名 / 排序 / 折叠面板全部保持原样。

**类型判定顺序**（`previewKind(ext, mime)`）：扩展名优先，服务端 `Content-Type` 兜底。
先看扩展名是因为 GCDWebServer 对 `.log` / `.bin` 这类常用 `application/octet-stream`，
只看 MIME 会把文本误判成「不支持」。反过来，扩展名不认识时靠 MIME 兜底 ——
判定发生在 `xhr.readyState === 2`（响应头已到、正文未读）这一刻：能判出类型就继续读，
判不出就立刻 `abort`，不会为了显示一句「不支持」把几百 MB 二进制读进内存。
`Content-Length` 大于 200 MB 同样在响应头阶段就拦掉。

#### v1.1.7：图标列按文件类型换字形

| 现象 | 根因 | 改法 |
|---|---|---|
| 文件图标是个下载箭头，不合适，能不能按类型换 | 原版 `template-listing` 对**所有**文件都用 `glyphicon-download-alt`，只对目录用 `glyphicon-folder-open`。满屏都是下载箭头，且 v1.1.6 新增的**每行下载按钮**也是同一个字形，两者在视觉上撞车 | `decorateRows()` 里按扩展名换字形（`applyRowIcon` / `iconKind` / `ICON_CLASS`）：图片 `picture`、视频 `film`、音频 `music`、压缩包 `compressed`、文档 `book`、文本与未知 `file`。目录保持原版 `folder-open`（CSS 已给琥珀黄） |

只用 Bootstrap 3 自带字形（Abel 字体里逐个确认存在，见 `bootstrap.css` 的 `glyphicon-*:before`），
**不引外部图标库、不新增任何网络请求**。扩展名比较统一转小写，`x.HEIC` 与 `x.heic` 同类；
表里查不到的扩展名、以及完全没有扩展名的文件一律退回 `glyphicon-file`，不会出现空白方块。
图标列配色改用 `.column-icon` 作用域圈死（图片绿 / 视频紫 / 音频橙 / 压缩包褐 / 文档红），
并把原先那条 `.glyphicon-download-alt` 通配染色规则删掉 —— 否则它会把真正的下载按钮一起染色。

#### 交付前自检修正（同一版本内）

按「全项目自检」要求复查后修掉的问题，均已补进静态闸门与反向注入用例：

| 问题 | 影响 | 修正 |
|---|---|---|
| 目录 ZIP 部分失败时，成功文件数写成 `entries.length - acc.dirs.length - failures.length` | `entries` 已包含目录条目与成功文件，这样算会得到「成功数 − 失败数」，报出的成功数偏小甚至为负 | 改为 `acc.files.length - failures.length` |
| `openPreview()` 先用 `previewKind(ext, '')` 判 `kind === 'other'` 就返回 | `previewKind` 的 MIME 兜底成了死代码：未知扩展名但服务端返回 `text/*` / `image/*` 的文件永远无法预览 | 删掉提前返回，类型判定统一移到 `fetchPreview()` 的响应头阶段，判不出才 abort 并回报 `code='unsupported'` |
| `dirAlreadyExists()` 与移动弹窗目录浏览用 `$.ajax` 发 `list` | 走的是被包装的 `$.ajax`，每次都会触发 `resetSelectionOnReload()`：在移动弹窗里点一下目标目录，刚勾好的多选项就被清空 | 新增原生 XHR 的 `listDir()`，插件内部 list 一律走它 |
| `doMove()` 用 `withSlash($('#move-input').val())` | `withSlash('')` 返回 `/`，用户清空输入框再点移动，文件会被**静默搬到根目录**（原版是空输入直接不提交） | 提交前校验：空值拒绝、必须以 `/` 开头，不通过就只在弹窗里提示，不发请求 |
| `onRowAction()` 只排除按钮元素 | 行末那几个 40px 按钮**格子的空白处**仍会冒泡上来，点删除按钮旁边一点就跳页 / 开预览 | 追加排除 `td.column-move / td.column-delete / td.oka-download-cell / td.oka-rename-cell` |
| `renderPreviewBody()` 的 `el` 为 null 时无兜底 | 理论上会留下一个空白弹窗 | 补 `else` 分支，清空 body 并显示「不支持预览」提示 |

#### v1.1.6：行末按钮重排 / 左侧选中热区 / 每行下载（按真机反馈改）

| 反馈 | 根因 | 改法 |
|---|---|---|
| 删除按钮要在最右，重命名放在它左边 | v1.1.5 用 `tr.appendChild()` 把重命名 td 追加到行尾，结果它排到了 `.column-delete` **右边** | `decorateRows()` 改为 `tr.insertBefore(rtd, delCell)`；下载按钮用 `tr.insertBefore(dtd, moveCell)` 插在移动按钮左边。最终列顺序：**复选框 · 图标 · 名称 · 大小 · 下载 · 移动 · 重命名 · 删除** |
| 选中太麻烦，必须点开头的小方块；希望文件名左边整片都能点中 | 复选框 td 只有 34px；图标列里的原版按钮还绑着「点一下就下载 / 进入目录」的 handler，点了反而触发下载 | 给 `.oka-check` 与 `.column-icon` 两个 td 加 `oka-select-zone`，在 `document` **捕获阶段**用 `onSelectZoneCapture` 统一 `toggleRowSelection()`；`e.stopPropagation()` 顺带拦掉原版 `$(".button-download").click(...)` / `$(".button-open").click(...)`。CSS 再给图标按钮 `pointer-events:none`，点击必然落到 td。复选框本身放行给原生 `change`，避免双重 toggle 抵消 |
| 取消点行下载，改成每行一个下载按钮，文件夹也要能下载 | v1.1.5 的行级 click handler 里 `openRow(d)` 会触发 `download?path=` | 删掉整个行级 click handler 与 `openRow()`，点名称区域不再有动作；新增 `.oka-download` 按钮 → `downloadItem(d)`：文件走原版 `download?path=`，目录走前端 ZIP 打包 |

**文件夹为什么只能前端打包**：`GCDWebUploader.m` 的 `downloadFile:` 里

```objc
if (isDirectory) {
  return [GCDWebServerErrorResponse responseWithClientError:
          kGCDWebServerHTTPStatusCode_BadRequest message:@"\"%@\" is a directory", relativePath];
}
```

一个 `download?path=<目录>` 请求必然 400，服务端也没有打包接口。所以目录下载在浏览器端做：
原生 `XMLHttpRequest` 递归 `GET list` 展开目录树（带深度上限与防环 `seen`）→ 逐文件 `GET download`
取 `ArrayBuffer` → 零依赖的 store 模式 ZIP（CRC32 + local headers + central directory + EOCD，
文件名置 UTF-8 标志 `0x0800`）→ `Blob` + `<a download>` 触发 `<目录名>.zip`。空子目录也会写成
以 `/` 结尾的 0 字节条目。打包进度与成功/失败汇总复用原页面 `#alerts`；某个文件失败不会
丢弃其它已取到的文件，结果条会明确列出失败项。

**全程不经过 `$.ajax`**：本插件为了排序与选择重置包装过 `$.ajax`，若目录递归借它读 `list`，
每读一层就会触发一次 `resetSelectionOnReload()`，把用户当前勾选项悄悄清空，还会干扰上传队列。
ZIP 下载使用独立的 `zipping` 标志，不占用 `tasks` / `running`，`Upload Files` 与拖放上传完全不受影响。

**附带结论：「点行打开文件 → 编辑 → 保存回手机」当前做不到**，仅靠现有 6 个接口不行：

- `GET download` 是 `isAttachment:YES`，只能下载，没有 inline 读取接口；
- `POST upload` 同名文件走 `_uniquePathForPath` 自动改名，保存回去只会得到 `xxx (1).txt` 副本，不会覆盖原文件；
- `POST move` 也不能覆盖（同样自动改名），且没有「写入内容」的接口。

要做成真正的在线编辑，必须**在插件侧新增原生接口**：hook `GCDWebServer` 注册
`GET /open?path=`（读文件内容按文本返回）与 `POST /save?path=`（原子覆盖写回），并处理
路径规范化、扩展名白名单、文本/二进制判定、备份与并发锁。这属于改服务端能力，
风险与工作量都明显大于本版前端改动，本版未实施；当前可用的替代流程是
「下载 → 电脑编辑 → 重新上传」，但会得到副本而非原文件。

> **v1.1.7 补充：**「点行**只读预览**」已经实现（上面的 Blob URL 方案），
> txt / pdf / mp4 / 图片 / 音频都能在网页里直接看 / 听 / 播；但「编辑完保存回手机」
> 仍然需要上面的 `/save` 覆盖写回接口，本版依旧不支持。

#### v1.1.5：文件夹配色与行点击语义（按真机反馈改）

| 反馈 | 根因 | 改法 |
|---|---|---|
| 文件夹和文件的颜色太相似，看着分不清 | 原版 `template-listing` 里两者都只是 `btn-default` + 灰色 glyphicon，名称文字同色同字号 | 列表重建时给目录行加 `oka-dir-row`、文件行加 `oka-file-row`；CSS 把目录的图标按钮底色、文件夹 glyph、名称文字、行首色条统一成 Windows 式琥珀黄，文件保持中性蓝灰。选中态带 `!important`，优先级更高，不会被配色盖住 |
| 点文件 / 文件夹所在行是重命名，想改成点行就下载 / 进入 | `jquery.jeditable.js` 第 125 行把 `click` **直接 bind** 在 `.column-name` 里的 `<p class="edit">` 上，handler 还 `e.stopPropagation()`；名称列又是最宽的一列，于是「点行」几乎必然落在它上面，连行级事件都收不到 | 不改 `index.js`、不改 bundle 模板（会破坏 App 签名）：在 `MutationObserver` 回调（microtask，晚于原版 `_reload` 里同步执行的 `$(".edit").editable(...)`）把 `<p class="edit">` **整个替换**成 `<p class="oka-name">`，绑在旧元素上的 jeditable handler 随元素一起失效。当时把行点击改成了文件下载 / 文件夹进入（**v1.1.6 已按反馈整体删除**，改为点行无动作 + 每行独立下载按钮） |
| 把重命名改成每行一个按钮 | — | 每行末尾追加一个 `.oka-rename` 按钮（pencil glyph），点它才 `startRename()`：名称换成内联 `<input>`，Enter 提交 / Esc 取消 / 失焦提交，用 `settled` 标志防重复 POST。提交语义与原版 jeditable 完全一致：`POST move {oldPath: 当前 path, newPath: _path + 新名}`，成功后 `_reload(_path)` 并在原 `#alerts` 反馈成功或失败 |

**为什么用「替换元素」而不是在捕获阶段拦 click**：jeditable 的 handler 绑在元素本身，
且自己 `stopPropagation()`；在 `document` 捕获阶段拦会连行级点击一起吞掉。替换 DOM 元素是
唯一能同时做到「点名称不再重命名」和「行级手势留给上层决定」的做法，而且不碰 App 包内任何文件。
（v1.1.7 起点名称是进入目录 / 预览文件，这个替换依然是必需的前提。）

多选入口没丢：行首复选框、工具栏「全选 / 取消选择」以及原版行末「移动」按钮照常可用；
**v1.1.6 起文件名左边整片区域（复选框列 + 图标列）点击都切换选中**，点名称区域不切换选中；v1.1.7 起点名称区域有进入 / 预览动作，但依然不切换选中。

#### v1.1.4：修复「操作后勾选残留」（按真机反馈）

| 现象 | 根因 | 修法 |
|---|---|---|
| 勾选 A 删除或移动后，下次勾选 B 点移动，「已选择项目」里仍有 A | `selected` 存的是完整 path，而列表重建后没清空：原版「删除」按钮走原版 `_reload`，我们的包装只排序、不清选择；移动后旧 path 匹配不到新行 —— DOM 上看不到勾选，集合里却还留着（幽灵项） | ① 每次 `GET list` 响应到达（删除/移动/上传/新建/切目录/刷新都走）就重置选择；② 打开移动弹窗时只认当前列表里真实存在的行，幽灵项顺手剔除；③ 关闭移动弹窗也视为操作结束，一并清空 |

三条路径都写了浏览器回归用例（删除后 / 关弹窗后 / 提交移动后）。把 v1.1.3 的 JS 换回来跑，
8 条断言失败：删除后计数仍是 1、批次里出现 `2.log`；关弹窗后甚至累积成
`2.log + 10.log + a.txt`。换回 v1.1.4 后 106/106 全过。

#### v1.1.3：移除诊断日志（按使用者要求）

| 原来 | 现在 |
|---|---|
| 构造函数、hook、地址修正等共 55 处 `LOG()`，向 syslog 打 `[OkaWiFi] ...` | 全部删除；`LOG` 宏、`NSLog` 调用、`[OkaWiFi]` 字面量一并从源码和产物里消失 |

功能语句逐句保留（返回的 URL、flags 改写、hook 注册、弹窗、前端注入都没动）。
代价是**不能再靠日志确认插件是否加载**——改为看功能：长按图标有没有出现「开启 WiFi 传输」、
蜂窝下按钮与地址是否正常、电脑网页有没有注入标记。构建期 `devkit/build.sh` 的
「诊断日志负向闸门」会从 dylib 原始字节反查 `NSLog` 与 `[OkaWiFi]`，一旦回流直接构建失败。

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
sh devkit/build.sh arm64e iphoneos-arm64e 1.1.7   # RootHide / roothide
sh devkit/build.sh arm64  iphoneos-arm64  1.1.7   # rootless（Dopamine / palera1n）
sh devkit/build.sh arm64  iphoneos-arm    1.1.7   # 传统 rootful
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

v1.1.3 起插件**不输出任何系统日志**，所以判据一律看功能表现，不要再抓 syslog。

装好后按顺序看：

1. **插件是否加载** —— 回桌面长按 App 图标，快捷菜单里应出现「开启 WiFi 传输」。
   菜单出现即说明 dylib 已注入、构造函数已跑完。没有菜单 → 检查 `OkaWiFi.plist`
   的 Bundles 过滤与 `dpkg -L` 里 dylib 路径是否匹配越狱类型（rootless 要在 `/var/jb` 下），
   装完重启 App。
2. **功能 1** —— 点「开启 WiFi 传输」，稍等 1~2 秒应弹出结果窗，地址已复制到剪贴板；
   电脑浏览器打开该地址能连上。
3. **功能 2** —— 关 WiFi、开个人热点，进 App 的 WiFi 传输页点「开启 WiFi 传输」：
   按钮可见、状态 Connected、二维码有内容，服务器地址是 `http://172.20.10.1:8080/`
   （不再是 `unknown`）；电脑浏览器能打开。
4. **功能 3** —— 电脑浏览器打开 `http://<手机IP>:8080`：
   - 默认外观与原版几乎一致：没有独立拖放框、没有多出来的面板；顶部设备名是一行小字，
     文件列表明显比原版靠上；原按钮那行多了「全选 / 取消选择」，列表每行前面多了复选框；
     `Upload Files` 按钮照常可用。
   - 从文件管理器拖 1 个文件 / 多个文件 / 整个文件夹到页面**任意位置**：拖入时浮出全屏
     「松开即可上传」提示 → 任务面板只出现**一条折叠小条**（汇总 + 展开 + 清除/重试/停止），
     点「展开」才看逐项进度 → 原 `#alerts` 位置显示成功/失败数 → 列表自动刷新。
     文件夹在手机上保留完整目录结构。
   - 勾选多项 → 点**任意一行的原版「移动」按钮** → 仍是原版弹窗，但多了「本次移动 N 项」
     和目录浏览 → 选目录或填 `#move-input` → 点「移动」：弹窗内显示成功/失败汇总，
     列表刷新，文件出现在目标目录。
   - 排序：文件夹全部排在文件上方，组内按名称自然序（`2.log` 在 `10.log` 前）；
     新建文件/文件夹后点 Refresh 仍保持有序。
   - 选择重置：勾选一个文件 → 点删除（或打开移动弹窗后直接关掉）→ 再勾另一个点移动，
     「本次移动」里应当**只有新勾选的这一个**，不带上次的文件。点删除后列表里的勾选也应全部消失。
   - 文件夹配色：目录行的图标按钮是**琥珀黄底 + 黄色文件夹图标**，名称是琥珀色加粗，
     行首有一条黄色竖线；文件行仍是灰色图标 + 普通深色名称，一眼可分。
   - 行末按钮顺序（左 → 右）：复选框 · 图标 · 名称 · 大小 · **下载** · 移动 · **重命名** · **删除**；
     删除在最右、重命名紧挨其左，下载在移动按钮左边。
   - 选中热区：点文件名**左边任意位置**（复选框列空白、图标列的图标本身）都会勾选/取消该行；
     点图标**不再下载也不再进入目录**；点行首小方块仍只切换一次（已选计数 ±1，不是 0 或 ±2）。
   - 点行手势：点文件名所在行**不应该有任何反应**（不下载、不进入目录、不重命名）；
     点名称**不再进入重命名编辑框**。
   - 下载按钮：点文件行 → 浏览器下载该文件；点文件夹行 → `#alerts` 显示打包进度，随后得到
     `<文件夹名>.zip`，解压后目录结构与手机上一致（含空子目录、中文名不乱码）。
   - 行内重命名：点某行**删除按钮左边的铅笔按钮** → 名称变成输入框且自动全选 → 改名字按 Enter
     → 页面 `#alerts` 显示「重命名成功」，列表刷新后显示新名字；按 Esc 或名字不变则什么都不做。
   - 回归：拖放上传、批量移动仍与前面几条一致；目录下载**不会**清掉已勾选的项目。
   - 浏览器「查看网页源代码」：`<body>` 里有且仅有一处 `<!-- OKA-WIFI-INJECTED -->`，
     源码里不应出现独立拖放框或第二个移动弹窗。

排错对照（v1.1.3 起判据全部看功能表现 / 网页源码）：

| 现象 | 原因 | 处理 |
|---|---|---|
| 长按图标没有快捷菜单 | 插件没被注入 | 检查 `OkaWiFi.plist` 的 Bundles 过滤、确认 `dpkg -L` 里 dylib 路径与越狱类型匹配（rootless 要在 `/var/jb` 下）；装完重启 App |
| 菜单有，但蜂窝下按钮仍不出现 / 地址仍 `unknown` | 类名/符号没找到（App 版本不是 1.7.5） | 本工程的类名与偏移都按 1.7.5 反汇编得出；换版本需重新定位 `PAGE_CLASS_MANGLED` 等常量 |
| 长按没菜单项 | `didFinishLaunching` 没被 hook 到 | 确认不是从后台恢复（此时不会重走 didFinishLaunching，重启 App 即可）；确认 AppDelegate 类名 |
| 点了崩溃（`brk`） | Swift 强解包 trap（`brk #1`） | 代码已按「只有 `httpServer` 必须为真」判断，其余 6 个 outlet 是 Swift `weak`，页面没上屏时本来就该是 nil。若仍崩，抓 crash log 里 `0x100013e2x` 附近的 pc 定位到具体强解包点 |
| 能开但地址显示 `unknown` | `serverURL` hook 没生效 / 类没找到 | 直接看功能：地址文字、二维码、复制按钮应一起正确。v1.0.2 的主力是 hook `-[GCDWebServer serverURL]`；应急可直接在电脑浏览器开 `http://172.20.10.1:8080` |
| 开了但电脑连不上 / 切后台就断 | 进程被挂起 | App 包内自带 `Happy Dreams.mp3` 用于静音后台保活；确认该素材存在、且 prefs 没关掉保活 |
| 蜂窝下按钮还是不出现 | flags 没被改写 | 确认 prefs 的 `EnableCellular` 未被关掉；确认 App 是 1.7.5；重启 App 再试（层 2 兜底是 `viewDidLoad` / reachability 后强制显示按钮） |
| 快捷菜单点了没反应 | `gServerPort` 未清零，被误判成「已在运行」 | 已 hook `-[GCDWebServer stop]` 清零；若 App 走别的停服路径会误判，重启 App 再试 |
| 操作过一次后再点移动，仍带着上次勾选的文件 | 选中集合没随列表重建清空（v1.1.4 已修） | 确认装的是 1.1.4；控制台执行 `document.querySelectorAll('#listing .oka-checkbox:checked').length` 应为 0 |
| 点名称还是进入重命名编辑框 | `decorateRows()` 没把 `.edit` 换掉（v1.1.5 已修） | 控制台执行 `document.querySelectorAll('#listing p.edit').length`，应为 **0**；为 1 说明装的是 v1.1.4 或更早，确认包版本后强刷（Ctrl+F5） |
| 点文件夹行进不去 / 点文件行不预览 | 装的是 v1.1.6（该版把行级 handler 整个删了） | 确认包版本是 1.1.7 后强刷（Ctrl+F5）；控制台查 `document.querySelector('#oka-preview-modal')` 应存在，点 `.oka-name` 应打开预览或进入目录 |
| 点文件图标还是会下载 / 进入目录 | 捕获阶段拦截或 `pointer-events:none` 没生效 | 控制台确认 `.column-icon` 带 `oka-select-zone`、`getComputedStyle(图标按钮).pointerEvents === 'none'`；两者都对仍下载就强刷排除缓存 |
| 点文件名左边空白不选中 | `onSelectZoneCapture` 没注册 | 网页源码搜 `onSelectZoneCapture`，控制台确认 `window.__okaWiFiLoaded` 为真 |
| 找不到重命名按钮 / 它不在删除左边 | `.oka-rename` 没注入，或装的是 v1.1.5（旧版 append 到了删除右边） | 控制台查 `document.querySelectorAll('#listing .oka-rename').length` 应等于行数；每行 `lastElementChild.className` 应为 `column-delete` |
| 文件夹下载得到的 zip 少文件 / 打不开 | 某个 `GET download` 失败，或浏览器不支持 `Blob` / `TextEncoder` | 先看 `#alerts` 里的「目录下载部分失败」清单；空子目录以 `/` 结尾的 0 字节条目保留。中文名乱码时确认解压软件支持 ZIP 的 UTF-8 标志位 |
| 文件夹 ZIP 解压后是一堆散装文件，没有外层文件夹 | 装的是 v1.1.6（ZIP 里没有顶层目录条目，部分解压工具不建隐式目录） | 确认包版本是 1.1.7；用解压软件查看 ZIP 内第一条应是 `文件夹名/`。v1.1.6 的包只能自己建一个同名目录再解压进去 |
| 点文件行弹出预览但视频 / 音频播不了 | 该编码浏览器不支持（如 `mov` / `flac`），或文件过大被拒 | 预览弹窗右下角「下载原文件」到电脑播放；超过 200 MB 会直接提示用下载按钮，不会读进内存 |
| 点文件行提示「此文件类型暂不支持在线预览」 | 扩展名不在白名单（如 `.zip` / `.bin`） | 用弹窗里的「下载原文件」；可预览类型：txt/log/json/xml/csv/md 等文本、pdf、png/jpg/gif/bmp/webp/svg、mp4/m4v/webm/mov、mp3/m4a/wav/aac/ogg/flac |
| 文件夹还是和文件一个颜色 | `oka-dir-row` 或注入的 CSS 没生效 | 控制台看目录行是否有 `oka-dir-row` class；为真但颜色没变就强刷（Ctrl+F5）排除缓存 |
| 系统日志里还能看到 `[OkaWiFi]` | 装的不是 v1.1.3（或更早） | v1.1.3 起源码和产物里都不会有 `NSLog` / `[OkaWiFi]`；确认装的是 1.1.3 的包并重启 App |
| CI 报 `CPU: unbound variable`，但编译和签名都成功了 | 某行 `echo` 里写了 `$CPU，`（后跟全角标点） | macOS 的 bash 3.2 把 `0xEF` 吞进变量名。改 `${CPU}`，并跑 `python3 devkit/check-shell.py .` 扫全仓库 |
| CI 的 Verify deb 步骤报 `IndentationError` 或 YAML `ScannerError` | 在 `run: \|` 里内嵌了 Python heredoc | 这两个错误互斥、无解：YAML 要缩进、Python 要顶格。校验逻辑必须外置成 `.py` 文件 |
| 蜂窝/热点下能开启，但服务器地址显示 `unknown` | `Helper.uploaderUrl` 在 `serverURL == nil` 时返回 Swift 常量 `"unknown"`（framework `0x713C` 的 `mov/movk` 立即数已确认）。前两版改 UI 控件没打中源头 | **v1.0.2 直接 hook `-[GCDWebServer serverURL]`** 返回正确 NSURL。应急仍可用电脑浏览器直接开 `http://172.20.10.1:8080` |
| 服务没在跑时地址保持空白 / `unknown` | 故意不写假地址 | 这是正确状态；v1.0.2 起端口由 `[self port]` 直取，探测不到也会采信 |
| 网页里没有复选框 / 拖了没反应 | 模板注入没生效（v1.1.1 默认外观本就与原版几乎一致，**不要拿「有没有新增面板」当判据**） | 浏览器「查看网页源代码」搜 `OKA-WIFI-INJECTED`，控制台确认 `window.__okaWiFiLoaded` 为真；再看 `#listing tr.row-file` 行首有没有 `.oka-checkbox`。源码有但页面没变 → 强刷（Ctrl+F5）排除缓存；源码没有 → 说明 `GCDWebServerDataResponse` 类没找到，确认 App 内 framework 已加载 |
| 文件夹和文件还是穿插着排 | 列表排序没生效 | 先在控制台确认 `window.__okaWiFiLoaded` 为真，再在网页源码里搜 `sortListingInPlace`；都没有说明注入层根本没加载。**首屏就乱**通常是包装 `$.ajax` 的时机被改到了 `$(document).ready()` 里（必须留在 IIFE 顶层，才能赶在原版 ready 里的 `_reload("/")` 之前）。若只有某个目录乱，把该目录 `GET list` 的原始 JSON 发来——排序只认「每项都有 `path`/`name` 字符串」的数组，结构不符会原样放过 |
| 顶部还是很空 / 上传任务框还是占地方 | `body.oka-compact` 没加上，或注入的 CSS 被缓存了 | 控制台看 `document.body.classList.contains('oka-compact')`；为真但样式没变就强刷（Ctrl+F5）。任务面板的折叠由 `setTasksExpanded(false)` 控制，若上传后明细直接展开，检查 `startUpload()` 里那行是否被删；也可直接点头部「展开 / 收起」验证切换是否可用 |
| 页面源码里出现两次 `OKA-WIFI-INJECTED` | 幂等标记失效（类方法 + 实例方法双 hook 都注入了） | 正常情况下 `OkaAugmentVariables` 会在第二次命中时直接返回原 variables。若重复，检查 `rangeOfString:@"OKA-WIFI-INJECTED"` 是否被改掉 |
| 拖入文件夹后只剩顶层文件、子目录丢了 | `create` 用 `withIntermediateDirectories:NO`，父目录不存在时整条链会失败 | 任务面板会显示每个目录的失败原因。代码已按深度升序逐级创建并对「目录已存在」做 `list` 反查；若仍失败，把任务面板的失败明细发来 |
| 拖放后出现两份上传 / 进度条乱跳 | 捕获阶段的 `stopPropagation` 没挡住 jQuery File Upload 在 `$(document)` 上的 drop handler | 检查是否有其它插件/浏览器扩展改写了 drag/drop；本插件的 `onDrop` 必须同时 `preventDefault()` 与 `stopPropagation()` |
| 点行末「移动」按钮还是原版那个只有路径输入框的弹窗 | 捕获阶段接管没生效（v1.1.1 仍复用原版 `#move-modal`，**弹窗长得很像原版是正常的**，判据是里面有没有「本次移动 N 项」和目录浏览） | 先在浏览器控制台确认 `window.__okaWiFiLoaded` 为真；再看源码里有没有 `onMoveButtonCapture`。若原版 handler 仍执行，会出现「弹窗立刻自动关闭 + 只发 1 个 POST move」——这是 `.off('click')` 没摘掉 `#move-confirm` 旧绑定的特征 |
| 移动完成后按钮点不动 | 故意设计：结果留在弹窗里给用户看，确认按钮保持禁用以防重复提交 | 关掉弹窗，重新点任意一行的「移动」按钮即可恢复 |
| 移动/上传后出现 `name (1).ext` 之类的副本 | 服务端 `move` 与 `upload` 都走 `_uniquePathForPath`，目标已存在时**自动改名，不覆盖也不报错** | 这是 `GCDWebUploader.m` 的真实行为，不是插件 bug；移动前确认目标目录没有同名项 |
| 批量移动全部报失败 | 目标目录不存在，或路径手填错了 | 弹窗里逐项列出失败原因。目录浏览只列 `GET list` 里真实存在的目录；手动输入路径时先确认它存在 |


---

## 五、已知限制（如实说明）

1. **编译环境是 macOS，本机（Windows）无法验证真实编译。** 代码做了静态审查（括号平衡、函数定义顺序、函数指针签名与赋值、ARC 桥接配对、格式串类型）。本机已用「桩替换 `xcrun`」把 `build.sh` 三个变体从编译一路跑到打包全部 `exit=0`，并在本地用 `verify-deb.py` 把三个 deb 的 22 项检查全部跑通；`shell` 语法、打包链路、校验判据的正反方向都已实测。但**真正的 `clang` 编译与 `codesign` 只能在 macOS 上发生**——桩产出的 dylib 不是可运行代码，本地 deb 也**不能装到手机上**。唯一可交付产物必须来自 CI。
2. **后台保活依赖音频后台模式。** App 的 `UIBackgroundModes` 是 `audio`/`fetch`，用音频钉住进程是可行的，但 iOS 会占一个「正在播放」状态（音量 0，听不到）。若你介意，可在 prefs 里关掉——但那会导致切后台后端口失效。
3. **`_GCDWebServerGetPrimaryIPAddress` 靠符号名 hook。** 若 App 换 GCDWebServer 版本或静态链接，符号可能找不到。这层失效**不影响 v1.0.2 的主力修复**（`serverURL` 是 ObjC 方法 hook）；只有 `GCDWebServer` 类本身找不到时才会退化到 UI 层写回。功能 2a 与功能 1 始终不受影响。
4. **地址优先级是启发式。** `bridge* > en* > pdp_ip*` 覆盖了绝大多数场景；若你同时开了 VPN 且 VPN 也建了 `bridge` 接口，可能选错；此时界面上显示的地址会不是热点网段，只能从地址本身判断（v1.1.3 起不再输出接口打分日志）。
5. **端口是 App 自己定的，插件不改。** v1.0.2 直接从 `[self port]` 读真实端口（`GCDWebServer` 公开属性）、`isRunning` 读运行状态，`stop` 时清零；不再靠猜 8080，也不再依赖 `startWithPort:` 是否被命中。端口 LISTEN 探测只保留为 UI 兜底层的补充手段。
6. **网页增强依赖电脑浏览器的 `DataTransferItem.webkitGetAsEntry()`。** Chrome / Edge / Safari 支持整个文件夹递归；不提供该 API 的浏览器会自动降级为「单文件 / 多文件」拖放上传，文件夹结构无法保留（点 `Upload Files` 选文件始终可用）。另外服务端 `create` 不递归、也不允许目标已存在，所以文件夹上传是**串行**的：先逐级建目录，再逐个传文件，大文件夹会比一次多文件选择慢。
7. **注入层依赖 `responseWithHTMLTemplate:variables:` 这条渲染路径。** 已在 1.7.5 的 `GCDWebServer` 二进制里反汇编确认（6 个 variables key 的字典 + `0x1D210` 的类方法调用）。若 App 升级换了 GCDWebServer 版本、改走别的 HTML 渲染方法，注入会静默不生效——但功能 1 / 2 完全不受影响，网页也会保持原版可用状态。
8. **「点行打开文件 → 编辑 → 保存回手机」仍不支持编辑保存；v1.1.7 起支持只读预览。** 服务端只有 6 个接口：`download` 是附件下载（指向目录直接 400）、`upload` 同名自动改名不覆盖，没有 inline 读取与覆盖写回。要真正实现在线编辑，必须在插件侧注册新的 `GCDWebServer` handler（例如 `/open` + `/save`），属于改服务端能力，未实施。目录下载因此走浏览器端 ZIP 打包（v1.1.7 起显式带顶层文件夹），超大目录会受浏览器内存限制；单个文件预览上限 200 MB，超过只给下载按钮。

---

## 六、文件清单

```
wifitweak/
├── Tweak.xm                    # 全部实现（约 1230 行，含逐条逆向依据注释）
├── OkaWiFi.plist               # CydiaSubstrate 过滤：只注入 com.blinker.documents
├── web/                        # 网页端增强前端资源（v1.1.7 含点目录进入 / 文件预览 / 顶层文件夹 ZIP）
│   ├── oka-wifi.js             # 列表排序 / 全页拖放 / 文件夹递归 / 折叠任务面板 / 复用原版移动弹窗 / 行交互 / ZIP 打包
│   └── oka-wifi.css            # oka- 前缀样式 + 顶部紧凑布局 + 文件夹琥珀黄 + 选中热区 + 行内按钮
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