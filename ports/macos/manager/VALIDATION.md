# 本机验证记录

验证日期：2026-10-31 至2026-11-01。主机：Apple Silicon macOS，Go1.26.5，Xcode27.1（27A9275）/macOS SDK27.0。Manager仍对应下列补丁SHA；最新完整组合为D9 QEMU+Darwin gfxstream0002（G2），见文末`ModAVD-gapfix-readback`。下表与历史D8/D9章节保持实际版本边界。

```text
1955b54a9c6916647daf6354fb28687e342cb10d10082f87e355827ea4afc91d
```

| 检查 | 实际结果 |
| --- | --- |
| `go test ./...` | 通过；含 Darwin 新增宿主测试和共用 CLI/MCP/HTTP 测试 |
| `go vet ./...` | 通过 |
| `GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build ./...` | 通过 |
| `GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go test -exec /usr/bin/true ./...` | 编译通过；测试未执行 |
| 固定上游提交的干净补丁重放 | 通过 |
| `python3 ports/macos/manager/build.py build` | 通过；生成 ARM64 CLI、GUI 后端、WKWebView helper 与 `.app` |
| `codesign --verify --deep --strict .macos-work/manager-dist/ModAVD.app` | 通过；本地 ad-hoc 签名 |
| `python3 ports/macos/manager/smoke.py` | 前一构建已通过 9 类实际进程/HTTP 检查；本轮内存判定修复未重复启动 HTTP server |
| 原生 WKWebView 可视检查 | 隔离根目录的实例主页、电脑检查诊断对话框已实际打开；Apple M4、ARM64 组件与内存检查结果正常显示 |
| 共享前端的 Darwin HTTP 数据回放 | 较早构建的最小 fixture 与真实 `/api/info`、`/api/check` 响应已验证主页检查刷新，控制台无异常；当前内存专用提示由下列自动回归覆盖，尚待新 GUI 可视验收 |
| Darwin 启动 GPU 环境 | 实际临时子进程确认继承的 `HIDE_REBAR=1` 被覆盖为 `0`，大小写独立变量和私有 ADB 20050 保留；不注入 NVIDIA 驱动缓存变量 |
| 核心运行记录 / 控制台目录 | 固定核心 `ConfigDirs::getDiscoveryDirectory` 在 HOME 非空时使用 `HOME/Library/Caches/TemporaryItems/avd/running`，为空时才回退 `getUserDirectory()/avd/running`；两条 discovery 分支均有回归。用户目录环境优先级测试继续覆盖 `ANDROID_EMULATOR_HOME`、`ANDROID_PREFS_ROOT`、旧 `ANDROID_SDK_HOME` 及 `.android` 规则；控制台令牌路径保持原逻辑且不受 `USERPROFILE` 干扰，协议测试仅用临时令牌 |
| 本地包与 Manager 目录契约 | 临时包按 `package.py` 的字面路径构造；识别 ARM64 镜像四文件、`mas-36` / `37.2.10`、`0.36.0` 清单、IME 与可选 launcher；缺失 `vendor.img` 会被拒绝 |
| Darwin 更新事务 / 恢复 | 原 Windows 测试迁移至本机，最终 17 个顶层用例含 6 个恢复点与 4 个坏包子例；`.app` 的入口、helper、Info.plist、嵌套资源、旧资源删除和执行位通过临时 TLS 更新源验证 |
| Darwin 更新故障恢复与数据保留 | 真实重命名冲突后再次恢复无需网络；部分应用文件已替换时可完成新版或退回旧版，重复恢复为空闲；SDK 平台工具、系统镜像、AVD 与日志哨兵保留 |
| 更新容量边界 / 竞态检查 | Darwin 下载、解包、增量与备份复制按每 MiB / 文件入口检查 20 GiB 门槛，空间低或探测失败立即停止；保留 `.part`、完整 archive 或 staged / 恢复备份；Windows 原预算与 I/O 路径保留；`go test -race ./internal/update -count=1` 与静态检查通过 |
| 构建产物完整性 | 10 项 SHA-256 与构建 receipt 的补丁指纹全部匹配 |
| Darwin HVF 输出识别 | 实际签名核心 `-accel-check` 返回 0 与 `Hypervisor.Framework OS X Version 26.6`；不再套 Windows 的 `usable` 文本规则。8 个场景回归覆盖真实输出、CRLF、非零退出、空输出、权限/能力失败、Windows 文本和缺版本；失败理由使用 Hypervisor.framework |
| Darwin Metal 输出识别 | 实际 `system_profiler SPDisplaysDataType -json` 返回 Apple M4 与 `spdisplays_mtlgpufamilysupport: spdisplays_metal4`；按明确字段及支持值识别，含 metal3/metal4 与 unsupported、not supported、未知值/字段、坏 JSON 回归 |
| 完整包的只读 CLI check | 新 CLI 指向实际装配根目录时 HVF 与 Metal 均为 true；4 GiB 配置仍因当时可用内存 3.7 GiB、要求 5.5 GiB 而 ready=false，没有绕过内存停线 |
| 按实例内存预算判定 | `/api/check` 保留默认 6 GiB 的 `ready` 与检查结果，内存项附带可选的实例容量、可用内存和后端开销；Go 回归覆盖阈值边界、零可用内存、JSON 往返及探测失败 |
| 前端内存策略回归 | `node --test internal/ui/checks_frontend_test.mjs` 通过 10 项；默认 6 GiB 不足时允许预算满足的 3 GiB 实例，阻止超预算配置；组件/HVF/磁盘等阻断、异常与旧响应的保守回退保留 |
| Restart、设置与运行中窗口操作 | 自动回归覆盖当前 store 配置及实际卡片菜单策略：Restart 在停止后交由后端重新检查内存；运行中窗口显隐和关机不受内存不足禁用。默认配置仅内存不足时显示“内存需调整”。尚未在本轮新 GUI 中实际点击验收 |
| 既有完整包的实际实例流程 | 当前修复前的 Manager 包已完成 3 GiB / 4 CPU / 32 GiB 实例初始化、正常关机和 CLI 带窗口启动；明日方舟已安装并首启至协议页。代理未处理协议或登录，用户暂缓登录后实战；不推断用户后续动作。该结果不替代当前 GUI 内存修复的可视验收 |

冒烟输出：

```json
{"ok":true,"checks":["cli_version","gui_version","loopback_startup","login","list","host_rejection","origin_rejection","single_instance","shutdown"]}
```

新增宿主测试实际创建并清理了 TCP listener、文件锁和子进程，验证当前进程的 argv/启动时间、陈旧 PID 拒绝、监听者归属、锁释放、孤儿进程的 launchd 父进程和独立进程组。未启动 ADB、模拟器或游戏。

启动参数静态核对已确认：Manager 使用 ARM64 AVD、`darwin-aarch64` 双 QEMU、`-datadir`、HVF 可用的 AArch64 `virt` 机器与 PCI balloon；控制台 `window show/hide`、返回 `windows=N` 的协议与核心补丁一致。runtime QA 使用独立 AVD 名称和目录；本地包仅复制 SDK，不导入该开发 AVD。Manager 实例 6 与 runtime QA 均使用 console 20060，应避免同时启动。

ARM64 AVD 与更新测试使用临时目录。早先隔离根目录的原生 GUI 检查未创建实例、安装 SDK 或写入 Keychain。Windows AUTO-MAS byte oracle 和写入用户 Keychain 的 UI CDK 测试在 Darwin 跳过。当前新 GUI 的内存提示及按钮状态、其余 WKWebView 交互、CDK 交互授权、明日方舟登录后游戏回归或发行公证仍待验收。本轮 MAA/MAA Verge 集成暂缓。

HVF/Metal 修复只读运行签名核心 `-accel-check`、系统 GPU 信息与 CLI `check`，没有启动真实 ADB、模拟器或游戏。最终只读报告为 `.macos-work/qa/manager-package-check-fixed.json`；原错误报告为 `.macos-work/qa/manager-package-check.json`。产物位于 `.macos-work/manager-dist`，已装配的 `.macos-work/package/ModAVD` 未在此轮替换。私有 ADB 启动用 `adb -P <port> start-server`，没有 runtime 先前遇到的 `-L tcp:hostname:port` 解析问题。

当前内存与运行记录目录修复只重建 `.macos-work/manager-dist`，没有替换正在运行的包。前端使用后端返回的预算数据，不解析提示文本或另设开销常量；创建、启动与 Resume 的后端内存检查保持原行为。运行记录目录修复只影响 `pid_<pid>.ini` 的查找，用户设置与控制台令牌路径沿用原规则。新 GUI 的完整包验收由后续装配单独记录。

以下为此次补齐前的历史结论：本地完整 bundle 不是原在线更新器的输入格式：在线更新器只交换 `sdk/emulator` 等原有单元，拒绝包内其他 SDK 子目录和符号链接，并要求带文件表的更新清单；本地 `PACKAGE-MANIFEST` 格式不同。当前也没有成功安装后的用户回滚入口。上述更新与恢复路径需要独立的本地 bundle 安装设计和真实包验收，本轮未宣称完成。

持续空间停线测试覆盖一次大 Write、1 KiB 小 Write、3 MiB 下载与压缩条目、文件间停线、增量基包复制、安装备份中断和清理空间后再次安装；未将低空间或探测失败误判为坏包，也未在核心 rename 前破坏原安装。测试使用注入磁盘探针，没有真实填满系统磁盘。临时 `.app` 测试使用可执行脚本替身，证明目录、执行位和事务恢复逻辑，不代表真实签名应用的更新验收。

2026-11-01 原版对齐审计的修复前记录：CLI20、MCP11及HTTP26个注册入口均保留，详见 [Manager逐项审计](../qa/MANAGER-PARITY-AUDIT.md)和[总报告](../../../macOS-compatibility-report.md)。新确认日志清理仍依赖Windows对打开文件的rename/unlink限制，Darwin可能删掉活动日志目录项；`logs_test.go`为Windows-only，因此上表Go测试不覆盖该组行为。临时文件原语证据已保存，生产问题尚未修复。本轮运行包完整性复验通过489个payload entries，日志为`.macos-work/qa/parity-package-verify-20261101.log`；最新dist未替换正在运行的包。MAA后来按用户要求做过两轮截图通道检查，第二轮只确认当次截图成功、实际增强方式未确认，不推断用户之后的新连接状态。


2026-11-01 签名更新与活动日志修复验收：

| 检查 | 本次结果 |
| --- | --- |
| 全部 Go / vet | `go test ./...`、`go vet ./...` 通过；新增 native 真实签名用例通过 |
| Windows 兼容 | 生产源码 build 与测试源码交叉编译通过，未在 Windows 执行测试 |
| Race | `go test -race ./internal/update ./internal/emu ./internal/host -count=1` 通过 |
| 活动日志 | 数量与总量均保护打开的旧日志；其他实例进程与普通 orphan sleep writer 受保护，writer 退出后可清理；实际 libproc FD/PID 身份和跨进程目录锁回归通过 |
| 真实 ARM64 签名 fixture | clang 生成原生 ARM64 程序，构成签名 `.app` 与 emulator 平面包；prepare、离线 install、断点 recover 补完、损坏 stage 还原均通过；无 shell 伪装原生验收 |
| 坏载荷拒绝 | 已签名原生字节篡改、重算文件清单后的坏签名、shell 入口伪装、已签名 x86-only 文件、暂存文件同尺寸改写均拒绝；拒绝前不开始交换 |
| 链接 graph | 内部相对链接与安全 `..` 通过；绝对路径、越过单元、悬空、循环和链接路径祖先被拒绝 |
| 本地凭据隔离 | 已 prepare 的 local install 使用会直接使测试失败的 settingsPath hook，仍成功；install/recover localOperation 不读取保存的 CDK |
| 签名扩展属性 | 真实 core 的 3 种 `com.apple.cs.*` generic-code xattr 经清单 base64 传输、恢复后通过严格签名检查；禁止其余属性，允许 CodeEntitlements 键并限制大小 |
| 真实完整核心更新 ZIP | `.macos-work/releases/ModAVD-gapfix-candidate.zip`，391253373 bytes，SHA256 `985494f61fcfe01e770de154baa19d12642bb7f9ac691c7688c7c7647cdac7fd`，transport 为 `modavd-update-manifest.json` |
| 独立完整根安装 | `.macos-work/qa/update-native-real/installed` 从隔离 mas35 更新到真实 mas36，prepare/install 均成功；SDK platform-tools、system/vendor 镜像与 5 类用户状态共 8 个 SHA256 不变，实例与自定义 metadata 保留；package.py verify 通过 |
| 真实中断恢复 | `recovered` 隔离根在 emulator 已换好、`.app` 新目录 rename 后尚未写 `new_moved` 时模拟退出；`update recover` 离线核对并补完，package.py verify 通过，8个 SDK/用户状态 SHA256 与实例 metadata 保留 |

本次早期真实 ZIP 用于验证完整核心、Qt、签名和更新格式，包含较早 Manager；不将该早包误记为最终组合发行包。上述实际 install 使用最新 Manager-dist 操作，未启动 ADB、模拟器或游戏，未替换 `.macos-work/package/ModAVD-validated`。证据放在 `.macos-work/qa/update-native-real/`。成功安装后清理事务旧单元；长期用户回滚不属于原版 parity 要求。

## ARM64/HVF idlepoll 回归

Manager 已解除原生 ARM64 的 `setupIdlepoll` 跳过条件，复用原有磁盘请求率守护逻辑：达到 400 requests/s 才启动各 vCPU 的 SCHED_IDLE loop，低于 200 requests/s 持续 1 秒后停止 loop。Windows 与 Darwin 共用的状态/错误报告测试现均在两平台编译，Darwin `go test ./internal/emu ./internal/guest` 与对应 `go vet` 通过。

一次性 ARM64/HVF 实例完成 off/on 各 3 轮 32 MiB、4096 × 8 KiB + fsync 测量，所有逐字节读回正确。off 中位数为 23.0706 MiB/s，on 为 32.1132 MiB/s；对应 QEMU CPU 中位数为 1.63 → 2.10 秒/32 MiB。生命周期检查实际观察到 2 个 SCHED_IDLE loop，磁盘空闲 1.6 秒后 loop 清空而 watcher 保留，执行 off 后二者全部退出。证据为 `.macos-work/qa/gapfix-smallio-ab-20261101.json`、`.macos-work/qa/gapfix-idlepoll-lifecycle-20261101.json`；该结果只说明此机器的这类本地负载受益，不推论游戏 CDN 下载速度。

独立增量补丁 `.macos-work/qa/manager-arm64-idlepoll-only.patch` 保留了这一改动与此前 Manager 补丁的边界，便于单独审查。

## ARM64 客体 Vulkan profile

API34 arm64-v8a revision14 原库 SHA256 为 `d6188011ed2ed58a1d69adfb04246abaa2595c0f15565a22da83f7751a3d6828`，候选 v2 为 `73ddfe0935826feef1f1a78d5a70af3ee7fa83584d2efaf5bad6ee91f36eb613`。独立 Go profile 从完整 4409384 字节原库生成与该候选逐字节相同的结果，校验输入不变、独立输出、确定性与损坏/截断/已修复库拒绝；unwind 元数据使用独立解码器和真实 native libunwind 验证。

真正 Android ARM64 GPU 负对照 `.macos-work/qa/guest-vulkan-original/stdout.json` 中，storage buffer、storage image、texel buffer 三类的普通 writes reference 及 core 更新均正确，6 组 KHR 更新重复首值。临时安装 v2 后 `.macos-work/qa/guest-vulkan-candidate-v2/stdout.json` 的 12 组 core/KHR 创建和更新组合均读回预期的三个不同值；含 ARM64 `size_t` 的 pNext 检查通过，4 线程 × 1024 组 image/buffer 创建/分配/绑定/销毁共 4096 组、8192 次 bind 完成，Vulkan 错误为零。QEMU、boot ID 与驱动 SHA 在运行前后保持一致，QA 临时挂载随后恢复为原库。最早两次对照的 `exec-out` receipt 退出码表示 ADB 客户端状态；语义判定由完整 GPU JSON 提供，runner 已改用 ADB shell v2 透传远端退出码。

Manager 现在贯穿挂载参数、主机/客体缓存、状态/延期报告和补丁函数选择 ARM64 profile。ARM64 缓存名为 `libvulkan_enc.arm64-khrfix-bindlock2.so`；只支持列入白名单的客体缓存名。新增 Darwin 回归验证首次上传再挂载、已启动实例的只读状态、应用/安装门禁、上传期间新应用出现时延期，以及所有报告使用 ARM64 哈希。

2182eac1 集成 QA 包已经完成首次 NEED_UPLOAD→从 d6188011 拉取→生成 73ddfe09→push→MOUNTED，返回 zygote_restarted=true；随后实际 GPU 12 组、pNext、4 线程 8192 bind 全通过。重复 start 时 QEMU、boot ID、zygote4276 和 SystemUI4540 不变。客体 adb reboot 后保持 QEMU15268，bootwatch 直接使用客体 cache 挂载，没有再次 NEED_UPLOAD，主机 cache hash/mtime 不变，系统界面恢复且 warnings 为空。证据为 `.macos-work/qa/gapfix-integrated-{start,adopt-verified,boot-cache}-20261101.json` 和 `.macos-work/qa/guest-vulkan-auto-manager/`。该包的 userdata 复制另发生错误删除版本缓存导致复位，不将这次启动记作迁移保数据成功。

## 当前诊断修正版 1955b54a

已运行实例的只读检查改为报告 `mounted <sha256>`，仅与所选 profile 的 fixed SHA 相符才判定当前补丁已挂载；外来或读不出的挂载给出 warning。`MOUNTED` 回报也先核对 fixed SHA，防止错误或缺失哈希被报告成功。客体 `setprop ctl.restart zygote` 失败返回 `FAILED restart`，不再误报 MOUNTED。

脚本仅在目标只有一条 mountinfo 记录，且该记录、源 inode 与目标 inode 均保持本次挂载身份时回滚；外来替换或叠挂会跳过卸载。结果明确记录 rollback-ok、rollback-skipped-mount-changed、rollback-failed-umount 或 rollback-failed-hash，不把回滚失败误写成“没有挂载”。实际执行嵌入 shell、替换全部 guest 命令的宿主测试覆盖成功、重启失败回滚、umount 失败、外来替换、叠挂、mount syscall 失败；另验证错误/缺失 MOUNTED SHA 不进入成功等待。

此修正版的 `go test ./...`（含真实 ARM64 原库/候选 fixture）、`go vet ./...`、`go test -race ./internal/emu ./internal/guest ./internal/vkfix -count=1`、Windows 测试编译链接、shell 语法及 diff 检查全部通过。构建器精确核对 source=固定 base+patch 后重建 CLI/GUI/Swift helper，app 严格 deep codesign 通过。CLI SHA256 为 `9df5b0c575153f995e4edcce79e6989b6f5e3532a65c30a6b8f59b12c144129d`；完整收据在 `.macos-work/qa/manager-arm64-final-validation-20261101.json`，前版收据另存 `manager-arm64-2182-validation-20261101.json`。1955b54a 已 fresh assemble 至 `.macos-work/package/ModAVD-gapfix-1955` 并实机启动；其新证据独立于 2182eac1，详见下文。

## AVD 完整复制

16→32 GiB 的受控扩容及 4 MiB 随机哨兵保留已独立通过。另一次克隆误删 `version_num.cache` 导致所有 qcow2 overlay 被重建，目标旧哨兵丢失，不能记作迁移保数据。源码 `android-qemu2-glue/drive-share.cpp:106–126` 在该文件缺失/不匹配时置 reset 标记，`:183–198` 执行重建。必须正常停止后复制完整 descriptor 与整个 `.avd`，保留 base/overlay/encryption/snapshot 和全部兼容标记，只重写 descriptor 的新根路径，再验证 backing/hash、旧哨兵和实际容量。详见 [源码定位](../../../.macos-work/qa/avd-clone-reset-source-audit-20261101.md)。


## 1955b54a 新根、保留与受控强杀

完整复制正常停止的 QA descriptor 和 `.avd`，保留全部兼容标记后，新根启动保留旧哨兵 `87b20f4f88d708b0fa57a64ce807dfe87fe608428bbab4eb8832eaec43852d64`、实际块设备 34359738368 字节与 µLauncher HOME；Manager 自动再次生成/挂载 `73ddfe09…`，idlepoll watcher 存在且 warnings 为空。证据为 `.macos-work/qa/gapfix-1955-retention-20261101.json`。这次完整复制通过，不改写此前误删版本标记导致复位的失败记录。

另在明确 guest sync 后，对该隔离 QEMU 执行单次 SIGKILL；停止后对 userdata、encryptionkey、cache 三种 qcow2 离线 check 全部无错。重新启动为新的 QEMU/boot，哨兵 SHA、32 GiB、µLauncher HOME 均保持，驱动从客体 cache 自动重挂且无 warning。证据为 `.macos-work/qa/gapfix-1955-{controlled-kill,after-kill-verified}-20261101.json`。此结果仅覆盖这次已同步的受控断电，不推定未同步应用写入或任意崩溃均安全。

live restart 失败回滚测试未执行到注入：前置正常 umount 在正在使用的自动预载驱动上返回 1，随后确认驱动仍为 fixed SHA 并清理临时 helper。`.macos-work/qa/gapfix-1955-vulkan-restart-rollback-preflight-20261101.json` 明确为 `not_executed`；成功、回滚和外来挂载等宿主替身负例不能替代这项实机测试。

1955 阶段本地 release ZIP 为 391402596 字节，SHA256 `c1f617d92488c2e4965de7fc9f5b927cb387769b40e2157e55504400e3ad1562`，收据为 `.macos-work/qa/gapfix-1955-release-20261101.json`。将它对旧 2182eac1 QA 包执行 prepare 时，因同为 0.36.0 被正确拒绝 `update_rejected`，没有执行 install 或覆盖用户状态；该结果只验证同版本拒绝，不能记作最新包升级安装通过。此前较早完整包的真实 prepare/install/recover 证据仍独立保留。

## 历史 D8 核心组合 ModAVD-gapfix-media

Manager patch 保持 `1955b54a…`。该历史 D8 组合的 QEMU tree 为 `f0ad4acfabc8ed00711847b393cea5515fe10ca0`，AEMU overlay tree 为 `04a678d0a729c6f9870e5f2c8149256294489298`；D0007 数组所有权、D0008 dispatcher/Context 生命周期和 HEVC snapshot 计数已进入该核心。双引擎构建与严格签名收据为 `.macos-work/qa/core-video-lifecycle-update-20261101.json`，backend SHA-256 为 `67812b82a0bbfcad9b6a65184b45d18b848ef66867b05bacb90ba26a29ec6d99`。private RAM 实验没有进入此生产组合。

| 检查 | 本次实际结果与边界 |
| --- | --- |
| fresh 组合与校验 | `.macos-work/package/ModAVD-gapfix-media` 使用 418 APFS clones；`gapfix-media-assemble-20261101.log` 与 `gapfix-media-verify-20261101.log` 记录 fresh assemble/verify 成功。 |
| Qt 开机 | `gapfix-media-start-20261101.json` 为 ok=true，mas_3、3072 MiB、2 CPU、32 GiB，launched/booted=true，32.869643959 秒，warnings 为空；Apple M4 硬件 GLES，software_renderer=false。 |
| 数据保留与正常关闭 | `manager-shm-media-20261101/report.json` 保留旧哨兵 `87b20f4f…` 与 µLauncher HOME；`gapfix-media-retention-stop-20261101.json` 核对块设备 34359738368 字节、驱动 `73ddfe09…`，sync 后 3.74186275 秒 graceful stop、warnings 为空。此次未重做强杀恢复，旧 1955 结果不改写成 D8。 |
| 真实 FrameBuffer GPU 读回 | `framebuffer-d8-normal-final-20261101/report.json` 与 `framebuffer-d8-asan-sdk-20261101/report.json` 各通过 GL/noGL 两模式，每模式 16 个变化帧，64×48 RGBA/BGRA、alpha/行序、源 GPU 与最新 `getPixels` 精确比较；noGL 另比较直接回调。实际 dyld backend、Vulkan loader、MoltenVK 与前后哈希均确认。ASan 仅仪表新驱动，未改生产库；不是 Android SHM/codec 集成验证。 |
| 真实 VideoToolbox helper | `native-vt-d8-helper-{normal,asan}-20261101/report.json` 各通过 56 个硬件会话、448 帧、5,898,240 字节及输入撤权，dyld 目标与前后哈希确认。D7 legacy 正/负控仍只属于旧二进制。 |
| dispatcher / Context 所有权 | `media-lifecycle-d0008-suite-20261101.json` 为实际 TU 的计数替身验证，120 次 dispatcher 周期、80 次组合 snapshot 往返和原始负控通过；不当作真实 Android MediaContext 或 GPU decoder 证据。 |
| 真实 SHM 首轮矩阵 | `manager-shm-media-20261101/report.json` 明确 ok=false；4 个旋转阶段均有 3 reader×12 次有效读取且相互同帧，设置已精确恢复。静态画面不推进使进度断言失败；SHM 固定 natural 1280×720，与 ADB 旋转尺寸不同，精确像素比较也失败。RGB 多见 ±1 而 alpha 相同，原因尚未闭合；不记矩阵通过，也不直接判生产缺陷。 |

原生 GPU/VT 的四份 fresh 报告及构建收据索引为 `.macos-work/qa/d8-native-gpu-video-suite-20261101.json`。该组合只覆盖上述范围，未追加 MAA 消费者、Cocoa 输入法、游戏战斗/长稳或生产物理内存回收通过结论。

后续 `.macos-work/qa/manager-shm-media-contract-20261101/report.json` 保留首轮失败，按生产契约重测为 `ok=true`：固定 natural 1280×720/BGRA 元数据、多 reader 采集、旧状态保留与设置恢复通过。源码确认仅 posted-frame callback 更新，fps 为建议值，静态画面不要求持续推进。`pixelParityEstablished=false`，没有加像素容差；ADB 与 SHM 为独立捕获，未确定对应同次 composition，尺寸兼容的旋转仅记录观察。契约通过不等于精确像素相等或无撕裂。

## 历史 D9 核心组合 ModAVD-gapfix-shm

Manager patch 仍为 `1955b54a9c6916647daf6354fb28687e342cb10d10082f87e355827ea4afc91d`。该历史组合的 QEMU tree `3058e5f90f327708e08e4df6eff63b28362479b3` 为 3 构建+9 功能 overlay；D0009 patch SHA-256 为 `9c76be8281ae49f972062310eb08f46784e76fe5008c7fd3cc7ad851988d45c8`。新增 start/stop/析构幂等、单一 SHM 引用所有权、同步 callback 注销及无 frame 时有界队列回收，保留 24B ABI 和 stop 后旧映射。双引擎构建/严格签名见 `.macos-work/qa/core-shm-lifecycle-update-20261101.json`。backend 与 D8 字节相同，可按该收据身份复用上述原生 GPU/VT 证据；未声称 D9 重新跑过全部旧专项。

| 检查 | 本次实际结果与边界 |
| --- | --- |
| 主机生命周期专项 | `d0009-shm-lifecycle-v4-20261101/report.json` 的 12 普通+12 ASan/UBSan 全通过、6 原代码负控全失败（4 断言、2 有界超时）。实际 Registry/MessageChannel/POSIX SharedMemory/VideoFrameSharer TU 与精确生产 start/stop/GPU 函数；renderer、clock、readpixels 等为明确 shims，非全 GPU/整机 sanitizer 或 TSan。macOS LSan 不支持而关闭。 |
| 既有 Registry 回归 | `d0009-upstream-registry-20261101/assessment.json` 在真实 D9 `android-emu_unittests` 中运行未改的 `CallbackRegistryTest` 6 项，全执行且零 skip/failure/error。D9 build log、archive member/object hash、新 helper 符号与链接时序核对，未把旧 binary 或零选中测试计为通过。 |
| live runner 离线评审 | `d0009-live-lifecycle-final-review-20261101/assessment.json` 核对 runner SHA `154072932e0e7add6a8a3658f39420d6ccbc2cf46f28ae9562a93c104f5e537f`，8 个隔离 mock 主流程均给出预期结果（1 成功、7 拒绝）。验证逐命令归属、完整 raw screencap、boot UUID、出帧后确认及失败也 guarded HOME；仅 harness 验证，非实机。 |
| fresh 组合与完整性 | `.macos-work/package/ModAVD-gapfix-shm`，418 APFS clones、0 普通复制、508 payload verify 通过。完整 stopped AVD 与兼容标记复制，所有 base/overlay/标记 hash 检查通过；只改 descriptor 路径和隔离私有端口，见 `gapfix-shm-{assemble,verify,state-clone}-20261101.json`。 |
| 启动预检 | 首次 start 在后台构建期间被内存门禁拒绝（可用4.3GB < 要求4.5GB），未绕过。该失败记录保留，后续两引擎正常启动另记。 |
| Qt 实机 | `gapfix-shm-start-after-build-20261101.json`：PID80181，mas_3/3072MiB/2CPU/32GiB，开机34.858898875秒、硬件renderer、warnings空。`manager-shm-d9-qt-20261101/report.json` 的七项均true：80重复start、guest停止期画面变化、SHM完整header/payload冻结、80重复stop、restart新帧、40启停周期、同boot；恢复HOME及流。 |
| headless 实机 | `gapfix-shm-headless-start-20261101.json`：PID86230，开机12.290534083秒、硬件renderer、warnings空。`manager-shm-d9-headless-20261101/report.json` 同七项全部true、同boot并恢复流；是另一引擎的独立运行，不与Qt相加为同一会话。 |
| 保留与正常退出 | `gapfix-shm-retention-20261101.json` 核对旧4MiB哨兵SHA `87b20f4f…`、块设备34359738368B、µLauncher HOME和驱动`73ddfe09…`。`gapfix-shm-{qt,headless}-stop-20261101.json` 均graceful、synced=true、warnings空（3.9737/3.4846秒）。本轮未重做D9强杀恢复，1955结果保留旧版本。 |

两份 live 报告均为 `internalRefcountObserved=false`；它们验证公开 console 操作与短期出帧行为，不观察内部引用数。每次读取重开映射，不能证明某个既存 reader 或同一 POSIX 对象跨 stop/restart 保持；主机专项另证持久 mapping 契约。Manager `shm_ready` 只检查映射存在，stop 后为 true 不表示 stream active。该 ABI 无序列锁，双读头相同不能证明整帧无撕裂。精确 ADB 像素相等、Android guestVulkanOnly、MAA、长期并发和游戏/codec 集成仍未验收。原游戏 `ModAVD-validated` 未替换。

## 当前 G2 成功发布组合 ModAVD-gapfix-readback

G2指[Darwin gfxstream0002补丁](../../../incoming/macos-gfxstream/0002-no-gl-readback-publish-only-success.patch)，SHA-256 `34a24390e2a7ddb0845864b0863d315b4d60d30fdc657918421add65d9699982`，不是原G0002 compute重试补丁。gfx working-tree overlay tree为`d14277bc86e27f74baa1a1716b1ee29f71587895`，不把它写成HEAD tree；QEMU仍为D9 tree `3058e5f90f327708e08e4df6eff63b28362479b3`，AEMU仍为`04a678d0a729c6f9870e5f2c8149256294489298`，Manager仍为1955b54a。FrameBuffer与ColorBuffer一同重编译/链接、安装/strip及严格deep codesign通过；[安装收据](../../../.macos-work/qa/g0002-no-gl-build-20261101/installed.json)固定新backend SHA `35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`。两QEMU引擎与D9字节相同，launcher重新签名后SHA为`4640bd3c0417773ff9d08205c9f95a112332817c9860251cf3cba5538a5d152e`。

`IColorBuffer::readToBytes`保持void虚接口与既有vtable；ColorBuffer增加非虚bool checked入口，传播现有GL/Vulkan结果。cache注册时一次零初始化，只有noGL额外分配一次帧大小scratch并在既有锁内复用；失败包括部分写后失败均不覆盖last-good cache且不回调，成功才转换/复制/回调。初始零cache不是有效帧信号。代价为noGL额外一帧内存与每成功帧一次复制，没有性能收益声明；bool仍不能普遍捕捉全部GL驱动错误，也未证明任意并发移除/finalize安全。

| 检查 | 实际结果与边界 |
| --- | --- |
| CPU失败契约 | baseline/candidate × GLES1/0 × normal/ASan共8组均编译成功。baseline GLES1/0各有6/3个命名失败（读失败不回调、部分失败不回调、保留cache）；candidate四组全过，零sanitizer诊断。生产函数体原样提取，reader/renderer/lock为明确替身，不是GPU/full-TU/ABI/线程验证。 |
| 原生v4矩阵 | sealed D9/G2 × normal/driver-ASan共4个wrapper、8个GL/noGL模式全过；每模式24个变化帧，RGBA/RGBX/BGRA源×RGBA/BGRA目标六组合，共192矩阵帧。noGL另有R8/宽不匹配/高不匹配三组拒绝→cache保持→恢复；GetPixels 0/17/oversized及halo通过，G2初始零cache/零callback通过。实际dyld backend、Vulkan loader、MoltenVK路径与前后hash核对；ASan仅仪表新驱动，生产库/Apple framework未仪表，未注入真实GPU读失败。 |
| RGBX与早期失败 | noGL helper明确将RGBX alpha归一255；GL以RGBA8保存/读回X，没有同样归一，v4逐字节核对同源GPU实际X，RGB另对已知图案精确比较。v1 const-pointer编译失败未执行GPU；v2/v3错误地统一要求GL alpha255，v3四wrapper均在首个GL RGBX→RGBA遇到31/255。失败保留，未增加容差或忽略alpha，不当作G2回归。 |
| fresh包 | `.macos-work/package/ModAVD-gapfix-readback`通过418 APFS clones/508 payload校验；正常停止的完整AVD及兼容标记复制，见同目录`package-assemble.log`、`package-verify.log`、`state-clone.json`。private RAM实验未进入此包。 |
| Qt启动/保留/SHM | PID34148，启动31.794854458秒、零warning。`qt-retention-v2.json`的旧哨兵/32GiB/73dd驱动/launcher/order9/实际新backend/同boot七项全过；`qt-shm-lifecycle/report.json`的80重复start、guest停止期画面变化、完整SHM冻结、80重复stop、restart新帧、40启停周期、同boot七项全过并恢复HOME/流。 |
| headless启动/保留/SHM | PID42667，启动13.251739375秒、零warning；`headless-retention.json`同七项与`headless-shm-lifecycle/report.json`同七项全过，恢复HOME/流。两个引擎为不同进程/boot的独立运行。 |
| 正常退出/失败记账 | `qt-stop.json`、`headless-stop.json`均graceful/synced、零warning，3.64848575/3.383434625秒；即时回执曾短时仍见pid，`post-stop-processes.json`另证两个PID及console listener均消失。Qt首轮非root读取sysfs被拒，`qt-retention-initial-failure.json`为后补异常转录而非原始subprocess receipt；显式su0读取后v2七项全过，未改生产代码。 |

CPU、native v4、早期失败与导出身份由[最终索引](../../../.macos-work/qa/gfxstream-d9-readback-candidate-20261101/verification-final.json)固定；[可复用QA](../qa/framebuffer-readback/README.md)导出的driver、runner、contract generator与已测副本逐字节一致，generator重现两份已执行contract源/manifests。原生独审89项通过；不把旧D8/D9 backend `67812b82…` 的VT helper结果冒充新G2 backend的fresh VT结果，本轮未重跑VT。

两份live均`internalRefcountObserved=false`；每次重开mapping不证明既存reader或同一POSIX对象跨stop/restart，`shm_ready`只代表mapping存在。ABI无序列锁；精确ADB/SHM像素相等、Android guestVulkanOnly、无撕裂、MAA、长稳/并发销毁与Android codec/游戏集成仍未验收。1955受控强杀仅属于旧版；G2本轮只记录正常同步停止。原游戏`ModAVD-validated`保持不变。

G2的[集中验证记录](../qa/framebuffer-readback/G2-VALIDATION.md)及[新包独立审核](../../../.macos-work/qa/g2-package-independent-review-20261101/assessment.json)固定706项通过：508项payload类型/权限/内容、签名与源码/构建/安装身份、两引擎raw retention/lifecycle/stop和退出观察。恢复PNG经CRC/解码及RGBA→BGRA哈希与SHM报告相同，仅证文件内部一致，非ADB像素等价。

## private RAM v7 组件与客体边界

该实验未进入 D9 或后续 G2 生产组合，也未改变 Manager 默认。固定 256 KiB 独立对象、精确 granule/serial allocation receipt、retire-before-unmap 与每个 `qemu_ram_reclaim_report` SG/range 调用最多2次 native尝试/32对象检查，已通过 `.macos-work/qa/exact-v7-native-20261101/assessment.json` 的172项外部核验：实际allocator两代正事务4,608、monolithic对照2,304、helper/故障检查。严格全局 free+speculative counter 仍 `all_windows_exact=false`（正窗口4,556/4,608精确+16页，对照2,282/2,304精确0），没有扩大容差或把它改记通过。2/32 计数每 SG 重置，不限制整个 queue element、32-element BH、连续 BH 或整次 pause；原生组件 gate 不证明完整调用者预算。`report_work_us` 仅覆盖函数内工作，未含停核等待、SG 映射/审计、push/notify 与恢复 CPU。

`.macos-work/qa/dcr-construction-cost-20261101/assessment.json` 的14项构造/释放检查通过。2.5GiB untouched 单次观察中，2MiB粒度1,280对象构造7.441ms/释放0.145ms；256KiB粒度10,240对象构造40.631ms/释放1.190ms。region在2MiB粒度为55→1337→55，在256KiB粒度为55→10297→55（target10240），释放后恢复不能抹去构造峰值。此测量不触摸目标页、不含HVF/guest/整次pause成本，不是SLA，footprint不变也不证明内核元数据零成本。native组件不执行full-QEMU报告资格/锁集成。

实际客体冻结在 [v7 capture](../../../incoming/macos-qemu/experimental/evidence/20261101-v7-reclaim-guest/capture.json)、[summary](../../../incoming/macos-qemu/experimental/evidence/20261101-v7-reclaim-guest/summary.json) 和 emulator.log，PID91872 已正常停止。256/512MiB 各3轮×8秒的 live/读零/重用分别通过148/149次 live 检查，追加512MiB×5轮×15秒通过419次，均零错且清理；网络8 case通过，与追加probe的host窗口重叠9.232410秒，不表示每case同时回收。[SIMD/快照](../../../incoming/macos-qemu/experimental/evidence/20261101-v7-simd-snapshot/capture.json) 为同进程/同boot的单CPU worker，10次stop/start、3次snapshot、26个live边界、2,164,996,438次检查零错且清理。其负例仅为probe敏感性，不是旧核心负控；snapshot 后回收永久 inhibit 符合源码安全门禁。

该早期PID91872的262次提交对应262个distinct offset，旧ref均1、旧PRESENT均0、cleanup/query error均0；这只支持该次窄范围正确性，当次没有驻留释放或同offset第二代。161个SG为131个attempt2/inspect2/remaining3.5MiB与30个attempt0/inspect16/remaining0；检查32上限从未触发，cold tail未查询，不能据此认定hot starvation。最大`report_work_us=1027`仅为单SG局部采样，full pause未测。后续另launch的遥测不并入这份冻结记录。


后续冻结[正向/故障与尾部证据](../qa/hvf-object-reclaim/v7-android-progress.md)独立于PID91872。PID2276的266次提交含262cold与4resident：每次旧PRESENT=16个16KiB页、旧ref1、paged-out/reusable/query/cleanup均0，HVA resident256KiB→0，free精确+16/spec0。4次累计1MiB，覆盖3个去重256KiB范围768KiB；同offset280756224的两代旧object ID不同。累计和去重值都不是当前净省；262cold中free/spec联合非exact10次，严格全guest仍false。map-fault PID12525有271次rollback/0commit，9次resident保持旧object和resident/free/spec，不能记成释放。两份运行身份链、raw日志/计时/物理窗口经[67项独审](../../../.macos-work/qa/v7-timing-positive-independent-review-20261101/assessment.json)通过。

只读尾部诊断另用二进制，131个4MiB SG的14对象尾部共1834个不同256KiB对象/458.5MiB，PRESENT/PAGEDOUT/REUSABLE全0，29344次真实16KiB页查询；[159项独审](../../../.macos-work/qa/v7-cold-tail-independent-review-20261101/assessment.json)通过。这仅说明覆盖尾部没有resident候选被冷首部挤掉，不证明全部SG无饥饿，458.5MiB不是释放量。正向19BH最多64次native尝试/498检查；request→resume最大14.068ms、插桩BH最大15.144ms仅为观察值，仍无共享BH/pause总预算。metadata净成本未测，生产继续默认off。

[固定kernel阶数评估](../../../incoming/macos-qemu/experimental/REPORTING-ORDER-ASSESSMENT.md)区分4KiB guest页与16KiB host页。Manager afterboot实际写读9（最小2MiB）；旧private v6 sysfs实读10，v7日志有4MiB SG且裸启动不跑Manager，但没有各launch新的sysfs读测。参数是minimum扫描/通知阶数，不拆已有高阶block；普通uint写本身不调度或flush，register会重设，guest32SG/2HZ也不限定host暂停。历史v6 buddy的较小空闲块不证明v7 host驻留、exposure或whole-object资格；冻结binary/host策略的A9/B6阶数对照收证进行：首次参数写入失败已正常停机、未进测量，retry1进行，尚无比较结论；不改生产设置或安全门禁。
