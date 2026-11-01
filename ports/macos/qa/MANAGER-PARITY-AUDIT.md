# Manager 原版对齐审计

审计日期：2026-11-01。基准为 `ModAVD/ModAVDManager` 固定提交 `21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a`。实际源码目录为 `/Users/azuki/ModAVD/.macos-work/manager`，以下 `M/...:行号` 均以该目录为根。此次只读源码、收据和已有证据，另在自动清理的临时目录验证 POSIX 打开文件的 rename/unlink 语义；未修改源码/补丁、运行实例或 MAA，未读取/输出用户 token 或 CDK。

## 结论与版本边界

入口清单无删除：CLI **20→20**、MCP tools **11→11**、HTTP 注册 **26→26**（24 条具体 API + `/`、`/api/` 两个 fallback）。机器输出协议仍为 `1`（`M/internal/buildinfo/buildinfo.go:12`）。这证明接口表保留，不代表所有入口在 Darwin 已实机验收。

当前源码补丁与 `.macos-work/manager-dist/.modavd-manager-build.json` 的 SHA-256 为 **`bdf40ea4eb70a444c1a81776eb55f98abe70c6b7b4788e23f356fec54a49ca65`**；本次重新读取补丁的 SHA-256 一致。正在使用的 `.macos-work/package/ModAVD-validated/provenance/manager/.modavd-manager-build.json` 则为 **`acc3c046e9eecfdc85711c2bf4322358c91776d8025b91d654cebfb8a4240a09`**。当前按实例内存判断和 Darwin discovery 路径修复尚未进入该运行包，不能借用旧包运行证据宣称新修复已实机通过。

本轮 `.macos-work/qa/parity-package-verify-20261101.log` 显示 `ok:true`、489 个 payload entries、`runtimeValidation:"pending"`；`package-final-verify.json` 仅为此前快照。主线程另已复核最新 `.app` strict 签名。包完整性、签名、构建和临时替身更新测试均不代替真实功能验收。

发现一项明确的 Darwin 语义回归：**日志清理仍以 Win32 的打开文件删除限制识别活动日志，在 POSIX 上该假设不成立**。在线更新对当前本地完整 bundle 的安装/事务恢复、真实 Keychain 授权、新 GUI 交互及多实例运行仍有验收缺口，详见后文。

## 完整入口清单

通过 Python 读取 `git show <base>:<path>` 与当前文件，分别提取 `name:`、`newTool(` 和 `HandleFunc(`；三组新增/删除集合均为空，顺序也一致。本次未执行 CLI/MCP/HTTP 写操作。

CLI，`M/internal/cli/commands.go:83` 起：

```text
list
info <i>
status <i>
create [--index ...] | --resume N
set <i> ...
delete <i> --yes
start <i> ...
stop <i> ... | --all
restart <i> ...
check [--memory M]
window <i> show|hide
bridge <i> install|uninstall|verify
install <i> <apk>
shell <i> ...
adb <i> ...
servers
version
update [status|check|prepare|install|recover]
mcp
help [command]
```

MCP，`M/internal/mcp/tools.go:125` 起：

```text
list_instances
get_instance
check_host
create_instance
update_instance
delete_instance
start_instance
stop_instance
set_window_visible
shell
install_apk
```

HTTP，`M/internal/ui/http.go:26` 起：

```text
GET    /api/info
GET    /api/instances
POST   /api/instances
GET    /api/instances/{i}
PATCH  /api/instances/{i}
DELETE /api/instances/{i}
POST   /api/instances/{i}/start
POST   /api/instances/{i}/stop
POST   /api/instances/{i}/restart
POST   /api/instances/{i}/install
POST   /api/instances/{i}/resume
POST   /api/instances/{i}/window
POST   /api/instances/{i}/input
GET    /api/instances/{i}/screenshot
GET    /api/instances/{i}/log
POST   /api/open
GET    /api/check
GET    /api/jobs
GET    /api/jobs/{id}
POST   /api/jobs/{id}/cancel
GET    /api/events
GET    /api/update
POST   /api/update/check
POST   /api/update/settings
```

另有 `/` 静态入口、`/api/` 未知 API fallback。GUI 更新安装按钮在原版已 disabled（base `internal/ui/web/js/update.js:1` 的注释明确说明），当前保持此限制，不是本次移植删掉安装按钮；CLI 的 prepare/install/recover 入口仍在。

## 分组实现与证据

| 功能 | 原版与当前实现核对 | 已有实际证据 | 验收边界 / 尚缺内容 |
| --- | --- | --- | --- |
| GUI 页面与原生宿主 | 现有网页主页、实例卡片、表单、检查对话框、viewer、日志、更新设置保留；Mac 用 WKWebView helper 代替 Edge，失败可系统浏览器打开。`M/internal/ui/window_darwin.go:30`；`ports/macos/manager/native/main.swift:5` | `manager/VALIDATION.md` 记录隔离根目录原生主页及电脑检查对话框实开，较早 fixture/真实 HTTP 回放无控制台异常 | 当前 bdf40 内存提示、创建/设置/Resume/Restart/显隐按钮的实际点击、文件选择、取消与新旧状态切换尚未完整实机验收。原生 file panel/alert/confirm 有实现，不能因已实现就列通过 |
| 创建、恢复创建、配置、删除 | 共用 CRUD 与创建初始化状态机保留；Darwin ARM64 profile 使用官方 API34 arm64-v8a、原生 qemu，元数据/实例锁用 flock。`M/internal/emu/crud.go`、`createinit.go`、`internal/root/profile_darwin.go:5`、`internal/host/darwin.go:330` | `qa/manager-create-0-3G.json`：实例0、3072MiB/4CPU/32GiB、initialized=true、offline；同名 log 记录无头初始化→sync→正常关机。`manager-create-0.json` 保留4GiB内存不足的真实拒绝 | 是旧 acc3 包单实例；真实 Resume 成功/失败/重启恢复、活实例删除拒绝、数据保留、用户配置编辑和多个创建并发未逐项实机覆盖 |
| 启动、停止、重启 | 原 graceful→sync→console kill→受控 force fallback 及 per-instance start/stop 锁保留；Darwin native process info/启动时间、setsid detached/orphan helper 已替换 Win32。`M/internal/emu/start.go`、`stop.go:117`、`internal/host/darwin.go:138`、`:251`、`:362` | `qa/manager-start-0.json`：真实窗口启动约34.8秒、online、booted=true、无 warnings；创建日志有约3.6秒正常关机 | 未证明全部强制/失败/取消/关机与开机竞态、Manager意外退出后接管、真实 Restart均通过；合成状态机/子进程测试不得混同客体数据安全验收 |
| 多实例与跨根隔离 | 仍支持0–9除5，5的端口留给私有ADB；console=20000+10*i，独立ADB/gRPC；中央进程归属要求路径与runtime avd.dir或精确datadir。`M/internal/root/root.go:55`、`:60`、`:68`；`M/internal/adb/identity_darwin.go:100`；`M/internal/emu/launch.go:156` | 宿主测试有真实临时 TCP listeners、启动时间、锁和 orphan，CLI/GUI smoke验证单根单实例GUI锁；仅找到实例0客体成功证据 | **未取得双客体同时运行/独立画面/输入/重启/端口冲突的实机矩阵**。两个不同根同编号不可共用固定端口，应正确拒绝。QA runtime console20060与Manager实例6冲突的限制仍需保留 |
| Darwin运行记录发现 | 当前 `HOME/Library/Caches/TemporaryItems/avd/running`；HOME空才退 `emulatorUserDir()/avd/running`，与固定核心一致；用户设置/console token各自路径保持。`M/internal/adb/identity_darwin.go:14`；`internal/emu/launch.go:177` | 两个HOME分支与独立用户设置环境优先级已由Darwin合成测试覆盖，源码独立审阅通过 | **运行中的 acc3 包不含该修复**；新包重启、foreign-root检测/旧PID文件及接管仍待实机。不能用旧包能看见一个进程证明discovery正确 |
| 检查与按实例内存预算 | HVF/Metal识别改为Mac字段；后端启动/创建/Resume仍检查新鲜资源；前端使用可选MemoryCheck元数据按当前实例配置判断，Restart只对当前运行中内存快照放宽。`M/internal/emu/check.go:90`、`:308`、`:471`；`M/internal/ui/web/js/checks.js:1` | 签名核心真实accel-check、系统AppleM4 Metal字段和完整包CLI check已采集；文档记录Go边界回归与Node10项策略/卡片测试通过 | 当前源代码策略有合成证据，**运行包仍旧**；需新GUI实机确认低内存时3GiB可用、超预算禁用、非内存阻断保留、设置后立即刷新、Restart/Show/Hide行为 |
| 窗口显隐 | Darwin改走核心console Qt线程 `window show/hide` 并解析 `windows=N`，保留headless无窗口拒绝语义。`M/internal/emu/window_darwin.go:16` | `qa/window-visibility-first.json`：真实show→hide→show，CGWindow可见性变化，guest boot ID不变 | 此为runtime核心早期显隐；不证明当前GUI按钮、Mac focus/跨屏、隐藏时新副屏全部通过；当前内存策略按钮仍待新包点击 |
| 输入与宿主IME | 原GUI HTTP只支持tap和back/home/recents，现仍同范围，坐标/键白名单/在线状态检查保留。`M/internal/ui/device.go:194`；viewer `:115`；宿主IME APK安装/版本/哈希/default选择流程保留于 `M/internal/emu/ime.go:22` | `qa/ime-console-roundtrip.json`：真实Android编辑器preedit/finish/commit中文ABC/delete/key67全部pass，scope明确console pipe；旧包start记录host_ime；游戏安装后到协议页 | **不是Cocoa物理键盘/输入法候选/组合光标/焦点的端到端证据**。GUI原本没有任意文本/滑动接口，不应把此既有限制写作新增退化；真tap/back等GUI路径仍待实测 |
| APK安装、shell、ADB servers | 原入口保留；GUI上传有限额/临时目录，命令均固定根/实例；Darwin大小写敏感环境合并、私有ADB20050等。`M/internal/ui/install.go:33`、`:47`；`M/internal/adb/adb.go`；`M/internal/emu/servers.go` | 旧包真实安装B服明日方舟，guest primary ABI arm64-v8a，首启至协议页；代理未处理协议/登录，用户暂缓登录后实战 | CLI adb install成功不能当GUI文件上传、APK错误/取消/大文件以及多实例目标隔离均通过；MCP写工具未进行真实客体变更验收 |
| HTTP截图与viewer | 原HTTP继续 `adb exec-out screencap -p`，PNG签名检查、每实例single-flight与300ms复用，viewer约2fps。`M/internal/ui/device.go:20`、`:99`、`:138`；`M/internal/ui/web/js/viewer.js:1` | 有HTTP合成测试、早期真实HTTP登录/list/check；不能把独立SHM PNG直接算作该HTTP路径通过 | 新GUI画面刷新/缩放点击/离开页面取消、多个viewer共享capture未逐项真客体UI覆盖；此路径本来不直接读SHM |
| SHM报告与外部消费者 | Darwin Info采用POSIX名 `videmulator<console>`，host shm_open探测；afterboot启动stream。`M/internal/emu/manager.go:128`；`M/internal/host/darwin.go:485`；`M/internal/emu/afterboot.go` | `qa/manager-game-frame-003/evidence.json`：旧包实例0报告ready，真实videmulator20000读取并保存1.4MB PNG；头部/进程/guest身份被验证 | producer无seqlock，双读相同头不保证无撕裂。MAA旧记录截图成功实际RawByNc；recheck仅针对2026-11-01 00:28:51那次测试的worker82134（00:30:26汇总记录），只确认MaaFwAdbController，增强SHM未确认且没有对应live mapping。**该次记录不能证明增强SHM已接入，也不能代表用户后续的新连接**；本审计未操作MAA |
| 日志生成与viewer | emulator/logcat按实例文件、tail/限行/实时跟随界面保留。`M/internal/emu/logs.go:286`；`M/internal/ui/logtail.go:28`；`M/internal/ui/web/js/viewer.js:156` | 旧包创建/启动/初始化日志与logcat路径真实存在，成功流程可复查 | 活动日志保护在Darwin有确定缺口，详见下面；新GUI日志切换/跟随/下载或打开目录仍非完整验收 |
| CLI/MCP机器契约 | JSON错误码/协议1/stdio framing/取消与progress/固定root保持；MCP各schema定义仍11项。`M/internal/mcp/tools.go:125`；`protocol.go:11` | `qa/mcp-real-20261031T155941Z.json` 真进程：initialize(2025-11-25)、tools/list、list_instances、get_instance、check_host | 该证据为旧包只读三工具；余8工具、协议其他版本、真实progress/cancel/异常恢复大部分仅mock/合成，不能全称实机通过 |
| HTTP鉴权与本机隔离 | loopback-only、精确Host/端口、API cookie、非GET Origin、CSP/nosniff/no-referrer、登录redirect、HttpOnly/SameSiteStrict保留；per-root state 0600。`M/internal/ui/ui.go:195`；`http.go:55`；`static.go:116`；`cmd/modavd-gui/single.go:82` | 前一build smoke9类实际进程/HTTP：版本、loopback、登录、list、Host拒绝、Origin拒绝、单实例、shutdown；共用HTTP tests记录通过 | 不扩大成安全完备结论；本轮未启动server或读取state/token。当前新包并未重复这9类真进程检查 |
| WKWebView边界 | nonPersistent datastore、导航同源、外链交系统浏览器、启动URL只许本机http；原生菜单和picker/confirm。`ports/macos/manager/native/main.swift:31`、`:53`、`:59`、`:83`、`:122` | 原生窗口实开和界面检查已记录 | 菜单、粘贴、文件picker、外链/重定向的用户流程没有完整真机矩阵；ad-hoc签名不等于发行公证 |
| Keychain更新设置 | Win32 DPAPI在Darwin由Security generic-password保存32字节wrapping key，AES-GCM随机nonce、entropy为AAD；JSON仅base64 ciphertext，临时文件0600。`M/internal/host/darwin.go:59`、`:494`、`:511`、`:530`；`M/internal/update/settings.go:104` | **只有源码实现**；真实用户Keychain未写入，原DPAPI/CDK测试保留为windows-only（`settings_windows_test.go:1`、`:18`） | 用户允许/拒绝/锁定Keychain、跨进程读、签名变动后提示、损坏blob、清除CDK及错误显示均待实机授权验收；不应在无授权时通过审计顺手写用户Keychain |
| 在线更新、恢复、空间停线 | 原status/check/prepare/install/recover保留；平台mac-arm64/macos/arm64、`.app`路径和执行位、下载/解包/备份持续20GiB门槛、状态恢复已适配。`M/internal/update/paths_darwin.go:6`；`updater.go:407`；`pkg.go:35`、`:85`、`:140`；`swap.go:460` | 文档记录17个顶层事务用例、6恢复点/4坏包子例、临时TLS源、脚本替身.app、低空间注入/探测失败及race通过 | **本地完整bundle不是该更新输入**：只允许sdk/emulator等旧单元，拒绝其他SDK路径/符号链接，要求更新文件表，与本地PACKAGE-MANIFEST不同；成功安装后的用户回滚入口原版即不存在，这是既有限制，不作为本次对齐新增交付要求。真实签名Mach-O/Qt框架更新后签名有效、公开mac-arm64完整/增量发行源、下载→停机→换包→重启→数据保留均未实包验收 |
| 原版平台专属优化 | bridge入口仍在但ARM64返回Unsupported，bridgeWanted=false；ARM翻译cpuinfo、x86 Vulkan library patch、x86 idlepoll、Windows/X3D priority+affinity被有意跳过；Apple交OS调度。`M/internal/emu/bridge.go:70`、`:318`；`afterboot.go:119`、`:160`、`:212`；`vulkanfix.go:49` | ARM64原生游戏安装/协议页可运行，提供架构范围依据 | 这些是功能/优化范围差异，不可说“与Windows所有优化实效相同”；也不能将不适用的x86补丁称为ARM缺陷。Apple性能、CPU开销和物理内存回收仍需同机同场景单独测量 |

## 确定问题：活动日志可能被清理掉

`M/internal/emu/logs.go:57` 的注释与实现依赖 Windows “打开文件不能删除”的语义。启动/新建创建日志前触发 `pruneLogs`；达到全目录1GiB门槛后，`:98` 起调用 `fileInUse()`；`:207` 用 `os.Rename(path,path)` 将成功解释为closed，`:187` 的 `tryUnlink` 直接 `os.Remove`。Darwin允许对已打开文件自重命名及unlink，所以正在写入的 emulator/logcat 也可能进入可删列表。在另一实例启动触发全目录清理、旧长跑实例的日志被排序在前时，目录项会消失，writer继续写到不可见inode，GUI找不到该日志，实际空间也要等writer关闭才释放。`:122` 的freed累计和后续日志会误报立即释放空间。每实例数量限制分支 `:76` 直接 `tryUnlink` 也依赖Windows拒删语义。这与“跳过活动日志、不计总量”的原版行为不一致。

仅在本审计创建的临时目录执行下列原语检查，退出自动清理，未接触运行日志。可复核记录为 `.macos-work/qa/manager-posix-log-prune-evidence.json`（含Darwin平台、UTC时间和测试边界；不声称执行生产prune或测量磁盘块回收）：

```json
{"rename_open_file_to_self_succeeds":true,"unlink_open_file_succeeds":true,"writer_still_works_without_path":true}
```

`M/internal/emu/logs_test.go:1` 仍为 `//go:build windows`，原 `TestFileInUse` 在Darwin没有执行，因此不能由“go test ./...通过”推断该移植行为已对齐。本审计已将问题报告主线程，不在本分工修改源码。修复需显式识别/保护活动的本根日志，并验证并发、跨进程及PID复用情况下不会删活文件；修复后应更新本段状态和运行包指纹。

## 交付前应优先补齐

1. 修复Darwin活动日志保护，补相应有意义回归；当前bdf40源码/最新产物与旧运行包的版本差异在交付记录里写清楚。
2. 装配包含当前内存/discovery修复的新包，确认严格签名后，复验实际GUI创建/启动/重启/配置/显隐、生命周期、双实例隔离、日志和截图。旧acc3证据可保留，不能升级成新包通过。
3. 将本地bundle安装/事务恢复与现有网络更新输入契约明确衔接，使用真实签名包验收；在此之前维持“在线完整bundle更新未完成”的状态。
4. 在用户授权的真实环境验证Keychain CDK和Cocoa宿主输入法往返；MAA增强截图、登录后游戏、性能和物理内存回收分别列为独立验收项，不能由基础连通或协议页替代。

本审计引用 `ports/macos/manager/VALIDATION.md` 的已完成测试记录，并独立核对了源码、入口集合、收据、MCP/生命周期/SHM/IME原始证据和POSIX原语。没有重跑整个测试集，也没有把临时TLS、脚本替身.app、mock backend或静态树一致性写成真实用户流程通过。
