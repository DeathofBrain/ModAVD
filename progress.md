# Progress

当前工作为完整 Apple Silicon 移植；下方早期兼容性检查是历史记录，最新进展见文末。

## 2026-10-31
- 检查工作区为空，无上级 AGENTS.md。
- 已成功执行 git clone https://github.com/DeathofBrain/ModAVD.git .。
- 记录仓库 HEAD 和 macOS 环境。
- 正在检查构建说明与平台依赖。
- 已读取 README、bridge 构建脚本、补丁清单、CI 和 CONTRIBUTING；并行委派补丁的平台兼容性只读审查。
- 已下载官方构建文档快照到 .local-check/build-docs.html。
- 官方构建文档已提取到 .local-check/build-docs.txt；元数据校验退出码 0。
- 正在运行 CI 隐私扫描、当前版本补丁语法/计数检查与 macOS 构建入口检查。
- 另委派上游固定版本的 macOS 支持核查，只下载少量文本，不同步完整依赖。
- metadata、privacy、89 个当前补丁解析/计数全部通过；日志位于 .local-check/logs/。
- 上游固定版本确有 Darwin arm64 构建路径，但本项目清单缺少必要 Darwin 工具，随包脚本和 guest 镜像方案也需要适配。
- 没有执行完整源码同步、编译或 Android 启动；没有修改已有受 Git 跟踪文件。
- 运行 xcodebuild -version，确认当前仅选用 CommandLineTools；前置检查不通过。
- 已生成 macOS-compatibility-report.md 与检查日志，完成当前版本可直接构建/运行的评估。

## 2026-10-31：完整移植实施

- 用户确定：原生 Apple Silicon / ARM64 Android 14，新建实例，本地 ad-hoc 签名；保留管理器、CLI/MCP、输入法、增强截图与更新恢复。游戏只回归明日方舟，当前不检查 MAA Verge。
- 已创建 `codex/macos-port` 分支。重型源码、缓存、产物仅在 `.macos-work/`，保留 20 GiB；未修改系统 Developer Directory。
- 已找到现有完整 Xcode 27.1 / 27A9275，使用进程级 DEVELOPER_DIR；实际 macOS SDK 27.0。早期“Xcode 缺失”判断已纠正。
- 已补齐固定 Darwin 构建清单；目前 62 个固定 SHA 项目，跳过 Windows 工具、依赖源码归档与上游 E2E 镜像。common、Qt、Clang 用 blob:none + sparse，仅保留 ARM64 Darwin 和必要公共/测试资源。
- 已定位 repo 浅取失败时自动回退完整历史；旧下载已暂停并停掉重复 common fetch，新的同步仅直接拉固定 SHA，不自动取消 depth。
- 原 65 个 QEMU、23 个 gfxstream、1 个 crashpad 补丁已回放并比对目标树；SDK 27 许可列表补丁及 5 个 Darwin 功能补丁已应用。尚未证明整个模拟器编译或启动成功。
- 5 个 Darwin 补丁覆盖：slirp 短 keepalive、APFS 稀疏 userdata、HVF/8核上限、console window show/hide、Cocoa 宿主 IME 路由。局部原生测试、语法和顺序应用已通过。
- 管理器固定基线 `21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a`；Darwin 全量 Go 测试、vet、Windows 交叉编译和 9 类真实进程 HTTP smoke 已通过；ARM64 CLI 与 WKWebView `.app` 已构建且 ad-hoc 签名通过。
- 隔离 runtime 工具与 24 字节头/BGRA POSIX SHM 探针已实现；14 个测试通过。未接受官方 SDK 条款、下载系统镜像、启动 ADB/AVD 或游戏。
- 62 个固定项目和稀疏依赖已全部检出，已记录 checkout 收据。新增 SDK 27 原生 Apple linker 补丁，保留固定 Clang、使用 Xcode ld，并将 ARM64 最低系统版本设为 11.0；真实 C/C++ 可执行文件及动态库测试通过。全部 7 个 Darwin overlay 已验证。
- 核心 configure 成功并进入真实编译；第一轮在 4,194/9,749 步因 FlatBuffers 独立 CMake 缺 Ninja PATH 停止。bootstrap 已将固定 Ninja/CMake/Python 加入子进程 PATH，FlatBuffers 子构建随后 44 步全部通过，正在复用原对象继续 install/strip；不额外构建 fishtank，不制作重复 ZIP。
- 删除此前失败下载遗留、无进程占用的 15 个 Git tmp_pack 文件，回收 10,215,325,696 B；未删除有效 pack、源码或构建断点。清理后可用空间约 109 GiB。
- 真实 WKWebView 首页与诊断弹窗已打开并确认 ARM64/Apple M4 信息、缺失 SDK 诊断和检查徽标；测试窗口已正常关闭，后端退出。尚未验证客体控制、Keychain 与在线更新。
- 内存回收仍需 HVF 实测。MADV_FREE_REUSABLE 仅降低计账，不能作为已实现真实回收的证据；未加入不安全的内存优化。


## 2026-11-01：真实运行进展及原版对齐审计

- 核心已完成全量/增量构建和签名，两条ARM64引擎实际装载，官方API34 ARM64镜像分别headless/Qt开机；原先“仍在编译/未装SDK”的记录为历史。
- 已接受官方两份许可并保存收据；adb37.0.1/系统镜像r14逐文件校验通过。Manager单实例3GiB初始化/正常停机/窗口启动、IME/桌面安装默认设置及B服APK安装首启已有真实证据。
- Qt主窗显隐保持boot ID；动态SHM10样本frame509→528，Manager既有流成功只读导出游戏PNG；console IME五项真实往返、33,792B网络哈希和HVF请求16核裁剪8核均通过窄范围验收。
- port QA最终记录73项通过；选择性核心89项通过/2项上游主动skip；最新Manager的Go/vet、Node10项和本地更新17个顶层事务测试通过。不是全功能验收。
- 最新manager-dist补丁bdf40ea4，当前运行包仍acc3c046；内存GUI/discovery修复尚未部署。此次重新运行包verify489项通过，最新app deep/strict签名通过。
- 用户要求检查MAA当前截图：第一轮RawByNc，第二轮AVD+MaaFwAdb截图成功但实际协议未确认，彼时worker无对应SHM映射；不推定之后新连接状态，未修改MAA。游戏登录后实战按用户要求暂缓。
- 全面审计原89补丁及Manager20 CLI/11 MCP/26 HTTP注册，入口没有删除。独立核心/Manager/runtime审计与总表见macOS-compatibility-report.md。
- 新确认待修：固定AOSP继承的HVF guest Q30→Q20映射错误（SDK32项表及两个引擎编译规则复核）；Darwin日志清理错误套用Windows拒删打开文件语义（仅临时原语复现）。本次未改生产代码或运行包。
- 尚未全对齐：安全物理RAM回收后端、ARM guest Vulkan khrfix/bindlock等价、随包补全/问题包/脱敏报告、真实更新包闭环；扩盘/双实例/Cocoa IME/图形视频故障分支和本机性能对照尚需验收。
- 本轮只读采样已确认reporting协商bit5=1/order9（并非物理回收）、ext4块4096B/inode1048576、512B扇区、write-through、dirty_expire200/dirty_writeback100；watchlist为空，未判为关闭成功。runtime定向合成测试65项通过。三个审计附录与总报告已完成，89项无遗漏/重复、入口集合与顺序一致，文档本地链接/空白/潜在凭据模式检查通过。


## 2026-11-01：按用户要求继续补齐

- D0006修复固定Google/AOSP基线的Q30→Q20；真实HVF32寄存器put/get、guest STR/LDR及负对照通过。不是本次port新增，未核查Google最新HEAD。native EGL overlay补NSOpenGL/CGL真实current验证，27原生用例通过。QEMU树d052e22a、gfxstream树ea665af1，两个引擎重建签名完成。
- Darwin活动日志保护、同UID非子/孤立writer、目录锁和race完成。随包维护8静态文件+4入口完成，34隔离测试、实际组件status/脱敏报告/问题包通过；12,470 B真实日志ZIP的home路径、邮箱与常见认证值模式检查通过。
- 真实core+Qt内部symlink+.app更新ZIP的prepare/install、中断recover、4种签名xattr与strict签名/清单通过，8项SDK/用户状态hash保持；package/release17项测试通过。不是远程发布服务验收。
- gapfix-native包（Manager2a16、507项payload、417APFSclone）隔离实例3完成create/初始化、Qt/headless/正常stop及16→32GiB保数据扩容。4MiB sentinel SHA87b20f4f…前后一致，ext4从4194304增至8388608个4096B块，前后qemu-img check无错误。
- ARM idle-poll六轮32MiB、4096次8KiB+fsync及全字节读回A/B：off/on中位23.0706→32.1132MiB/s，QEMU CPU秒1.63→2.10；busy启两个SCHED_IDLE loop、idle1.6秒退出、off清watcher。仅适用于该负载，未外推游戏CDN。
- ARM r14 Vulkan原驱动SHA d6188011…的6组KHR真实GPU读回错误已复现。v2 SHA73ddfe09…修复后12组core/KHR × buffer/image/view、pNext、四线程4096对/8192bind通过；原生ABI/锁/FDE回溯专项通过。临时QA候选mount已回退，系统vendor.img未写。
- Manager2182eac1接入ARM Vulkan+idle-poll，Go/vet/race/Windowscompile及strict签名通过；新gapfix-integrated包417clone/507payload通过。首次正式start自动原库拉取→生成→推送→挂载→zygote重启，GPU复测全部通过；重复start的zygote/SystemUI PID和boot ID不变。adb reboot后同QEMU15268，bootwatch直接cache挂载无NEED_UPLOAD，hostcache hash/mtime不变、界面恢复0warnings。证据gapfix-integrated-{start,adopt-verified,boot-cache}-20261101.json及guest-vulkan-auto-manager/。
- 复制隔离QA数据后删除兼容标记，副本启动触发上游userdata重建；源QA与原游戏目录未覆盖。正在核查触发条件和修正克隆流程，不把该副本算作保数据迁移通过。原16→32扩容证据不变。
- 内存audit v2真实boot13.026s，sized HVF6次/240B，128SG累计512MiB候选（非严格资格/非实际回收），reclaim_enabled=0。证据冻结于experimental/evidence/20261101-v2-sized-guest/；guest6808已正常停止。团队继续checked-sized DMA、pause/transaction/rollback实现。
- Cocoa实际按键helper可编译，但AXIsProcessTrusted=false，未进行原生注入/未改输入源；console五项往返不冒充Cocoa验证。原游戏实例后续只读核对已离线，本轮未向它执行stop/kill；MAA/5037配置未改，游戏登录后实战仍按用户安排暂缓。

- 后续1955诊断修正版已fresh assemble `.macos-work/package/ModAVD-gapfix-1955`（Manager1955b54a，417clone/507payload，strict签名），完整stopped AVD复制保留version_num.cache后旧sentinel/32GiB/µLauncher均保持。误删该cache会由drive-share.cpp重建qcow2，根因已单独记录。
- 隔离QA单次guest sync后SIGKILL：userdata/encryption/cache三种qcow2均无错，重启后旧sentinel/32GiB/home/缓存驱动保持。已正常stop。真实restart失败注入未到达（预先umount已加载驱动失败），仅主机精确脚本负例算通过。
- 最新ZIP为ModAVD-gapfix-1955.zip，391402596B/SHA c1f617d92488c2e4965de7fc9f5b927cb387769b40e2157e55504400e3ad1562。对旧2182 QA相同0.36.0 prepare正确拒绝，未install/未覆盖；既有真实更新证据不变。
- 新有界Android内存probe已NDK编译：16MiB活数据并发全字节校验、guard页、128–512MiB高熵数据释放/读零/重用，JSONL分阶段采证。当前non-reclaim production guest的128MiB/1round/2s smoke通过，20次live全校验无错；这不是物理回收证据。binary SHA9ea596d0de18e350c5398719e96d870ebbd4ab23a8888766a96bfcbb8b880e04。
- 独立native production Mach/HVF transaction在2/4/8vCPU ×整64MiB/局部2MiB、三类注入故障、commit/读零/guest重写/邻页通过；真实未知unmap失败已改fail-stop，需新header重验。RGBA8 renderer精确DMA传输的ASan/UBSan及真实NSOpenGL poison state/PBO/canary27项通过；候选核心已构建，Media/外部pointer边界继续独立审计，尚未宣称已开启实际回收。

- 补查Google emu-main-dev于本次查询的tip cf0f40a0aad1aa3fca68f2f89372525c8d28ca68（2026-10-27）仍将case30返回Q20，证据google-qemu-current-branches-hvf-map-20261101.json；固定基线和当前该开发分支均可确认继承问题。默认HEAD指向旧2015 master、emu-main-release无该ref，二者未作为最新版本证据。
- 新增network_probe.c/network_matrix.py和NETWORK-CHECKS.md：原生与NDK严格编译通过，宿主v2八项（160并发TCP、32MiB单路、8x4MiB、半关闭、refusal/RST、160 UDP、故障后新TCP）通过；Android/slirp尚待客体窗口，不宣称CDN或速度收益。首次bound non-listening TCP在Darwin超时的失败证据保留，v2前后证实无listener端口。
- host_rusage.py修复Darwin CPU原始Mach ticks误标ns，按mach_timebase_info转换并保留raw值。本机125/3，0.45秒CPU loop与process_time_ns比1.000318；字节resident/footprint始终独立，不把进程计账当物理回收证明。
- 网络实机PID58200/私有20150/20160八项全部通过，宿主highwater160 TCP/160 UDP、最后active0，refusal111/RST104，boot ID保持、UUID目录清理完成；单路32MiB2098ms、8x4MiB4135ms含校验且并行编译，不声称提速。network-matrix-guest与provenance收据固定pre-ASG media候选；PAGE_POISON门槛导致本次虽请求PAUSE_AUDIT但未实际暂停，此证据不用于回收/暂停通过。
- 根据已部署内核6.1.23/g7e35917775b8的Google官方virtio_balloon.c核实：没有init_on_free/page poisoning时驱动主动清PAGE_POISON bit4而保留REPORTING bit5。原候选将bit4当作暂停必要条件过严；团队改为reporting+poison0，保留全部feature快照/epochs/status/realization/topology门禁。精确源码SHA f32a87c2…及1095–1098摘录在page-reporting-kernel-source-20261101.json，尚待新候选真实暂停复验。
- v4已真实进入pause处理（batches/popped日志），暂停与reclaim两个guest各256MiB×3×8s/148livechecks零错误。RECLAIM1初次开机131次实际事务累计549453824B；主probe窗口6次/3768320B，均非候选计数，但不同boot-age/footprint波动不能声称物理或性能收益，见experimental/evidence/20261101-v4-memory-comparison.json。
- RECLAIM1网络八项再次全通过，network-matrix-guest-reclaim1-20261101.json及产物侧车。另128MiB短内存probe也通过，但时间戳显示与网络未重叠（overlap0），不称组合并发；后续若测并发由同一工具调用协调。
- 真实VideoToolbox helper H264/HEVC初轮448frames与320frame逐字节参考通过；ASan拦截随后复现new[]/scalarunique_ptr释放不匹配，修复private三个同类声明并准备v5重测。Google真正基线f0c183的三个源码与回放HEAD逐字节同，allocator-origin-google-baseline.json确认上游遗留；旧错误把回放HEAD称上游的归因证据保留但已被新报告取代。

- v4的unmap/map/replace真实Android故障注入已完成：403/691/717次事务全部rollback，0commit/cleanupfailure，各128MiB读零/重用/活数据probe通过并正常stop。最终native事务header r2的2/4/8CPU六组commit/rollback与两组未知unmap fail-stop全部通过。
- v5真实RECLAIM1+RESIDENCY1 guest87274主256MiB×3×8s通过，149livechecks0errors、122hostsamples；本窗口41次事务/4,587,520B，目标before同4,587,520B到after0，queryerrors0。不把targetmincore或累计事务当物理收益。独立旧VMobject实验表明新HVA非驻留可与旧2048页仍PRESENT/DIRTY并存；MADV_FREE未实际去驻留，FREE_REUSABLE只标reusable，尚需补足真正释放机制。
- v5网络与128MiB内存probe同exec并行，两者通过；时间戳重叠4.274183875秒，memory21livechecks0errors，network TCP最高160/UDP160/最终active0。旧v4 overlap0证据保留。新证据network-memory-overlap-v5-20261101.json。
- D0007作为独立生产overlay只改三个VT数组owner，不混入实验RAM；bootstrap验证10个QEMUoverlay完整树ce93141c684b1cc23a2231fda48b4ccf3b70a795，gfxstream树不变。main两个QEMU/backend增量构建、install/strip及最终deep/strict签名全部通过，core-videotoolbox-update-20261101.json固定新backend1d1257c8…与launcher/两引擎身份；仓库化native-vt正在对其复测。

## 2026-11-01 04:25 后续补齐

- Google `emu-main-dev` tip `cf0f40a0`（2026-10-27）仍将HVF Q30映射Q20，固定Google baseline同样存在；D0006为上游继承缺陷修复，不是Apple HVF API错误。查询证据`google-qemu-current-branches-hvf-map-20261101.json`。
- 已生产固化D0008及配套AEMU声明：视频dispatcher残余plugin销毁、Context decoder/alias顺序和HEVC snapshot计数；实际dispatcher/Context ASan带原负控通过。main QEMU tree `f0ad4acfabc8ed00711847b393cea5515fe10ca0`，AEMU tree `04a678d0a729c6f9870e5f2c8149256294489298`；两引擎/backend install/strip、deep strict签名收据`core-video-lifecycle-update-20261101.json`。
- 最新独立包`ModAVD-gapfix-media`418clone/verify通过，Qtboot32.8696秒；完整旧AVD保留sentinel、32GiB、µLauncher和driver hash。正常stop sync=true、3.74秒，root释放唯一guest quota给v6专项。
- SHM初轮三consumer各12次只读mapping有效、四方向可取帧、设置精确恢复；exactADB像素/旋转matrix=false保留。natural方向固定1280x720，不能假定与旋转ADB布局相同；静态画面不保证短窗口推进。诊断PNG RGB多±1差异、alpha相等，尚未归因，不将其判作生产bug或宽松标通过。
- 私有v5完整QEMU单CPU SIMDworker：10pause/resume+3snapshot save/load/delete，2,135,867,594轮零错误，后台探针及目录清理通过；不涵盖全CPU/宿主睡眠/游戏。
- 私有v6native真实HVF独立对象释放正负对照已完成；实际allocator/transaction4558checks、故障305checks、guard86checks通过。全局counter噪声导致部分strictphysical=false，全部原样保留。actualAndroid已boot，256/512MiB各3轮live/reuse无错但有驻留回收尚未触发；不能把PRESENT0的bootcommit记成物理收益，生产默认仍不改变。

## 2026-11-01：D9与v7检查点

- D9 main两引擎重建/签名和新包418clone、508payload verify已通过。修复SHM重复recording引用、stop注销与无frame队列耗尽，12普通+12ASan/UBSan、6原代码负控、38独立review通过；找到正确的android-emu_unittests后上游6项Registry测试通过，先前android-emu-base的0tests不计入。
- 新包完整复制stopped D8 AVD并保留version_num.cache等九项SHA。首次start因后台编译时可用4.3GB<所需4.5GB被正常阻止；构建结束后预检可用4.8GB并成功Qt开机34.8589秒、PID80181，0warnings。sentinel、32GiB、µLauncher及driver SHA保持。
- 私有v7固定256KiB粒度的native和construction cost gates通过；renderer23/headless583TUs顺序编译、安装及四件套strict签名已过，不改变main/default。实际Android收益与安全仍须后续证据。

D9封存独立包`ModAVD-gapfix-shm`现已完成真实双引擎验证：Qt开机34.8589秒、headless12.2905秒，各0warnings；各80次重复start、80次重复stop、40轮stop/start、停止期间真实Settings显示变化而SHM header/pixels不变、restart观察新帧及恢复HOME/活跃帧、同一boot ID全部通过。两次均graceful/synced=true关机（Qt3.9737秒、headless见stop收据），旧sentinel/32GiB/µLauncher/driver保持。证据为`.macos-work/qa/manager-shm-d9-{qt,headless}-20261101/report.json`、`gapfix-shm-retention-20261101.json`和`gapfix-shm-{qt,headless}-stop-20261101.json`。这些是专用QA实例公共console与SHM短观察，内部refcount由host专项证明；每次read重新open不能证明旧reader跨stop持有同一object。不声称ADB像素相等、帧无撕裂或MAA已消费增强。原游戏包和MAA未修改，私有RAM仍不在此包中。

## 2026-11-01：v7 驻留释放与 noGL 候选

- 用户追问 HVF 归因：固定 Google 基线及已查 2026-10-27 的 emu-main-dev 提交均为 Q30→Q20，属上游继承错误，D0006 已修复；未追查最初引入提交，不泛指全部发行版。
- 私有 telemetry-only 客体 PID2276 已正常停机，266 次提交中 4 次旧对象真正驻留，最终引用释放窗口均精确 free+16/spec0；累计 1 MiB、3 个地址范围 768 KiB，不是净内存节省。含同 offset 两代 backing，联合 free/spec 的冷对象非 exact 窗口为 10/262，原始 false 保留。
- 完整暂停遥测显示每 SG 2 次尝试/32 对象预算不能冒充 BH 总预算；最多 64 次 native 尝试/498 次对象检查，最大 request→resume 返回 14.068 ms、插桩 BH15.144 ms，仅为观察值。详见 [v7 增量记录](ports/macos/qa/hvf-object-reclaim/v7-android-progress.md)。同 binary map-fault 与 cold-tail 诊断继续，生产默认未变。
- 正在准备原版 G0006 noGL 读回失败发布修复：保留旧 void API并传播已有 GL/Vulkan bool，缓存初始化、每 display scratch 复用、成功才发布；独立候选尚未应用生产，物理计数窗口结束后再运行 GPU 验收。

G2新检查点：`0002-no-gl-readback-publish-only-success.patch`已完成生产构建/签名，gfxstream完整树`d14277bc86e27f74baa1a1716b1ee29f71587895`（2 overlays），backend SHA`35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920`；CPU正负控8组及native v4共8模式192帧通过。新包`ModAVD-gapfix-readback`418clone/508payload verify，Qt31.794854458s/headless13.251739375s开机，各7项数据/配置/实际backend保留和7项SHM生命周期及HOME/stream恢复通过，graceful/synced停机分别3.64848575s/3.383434625s，后续确认两PID消失。失败历史、RGBX两模式契约、额外scratch/copy成本和覆盖边界见[G2验证说明](ports/macos/qa/framebuffer-readback/G2-VALIDATION.md)。G2 backend与D9已不同，旧视频结果不能写成新G2视频重跑；生产RAM和原游戏包/MAA状态不变。

- 后续f022单组A9/B6已完成并正常停机。严格窗BH4→83/SG42→2298，精确完整resident窗6→169，精确子集地址union1→27.75MiB、累计目标及旧PRESENT1.5→42.25MiB；非当前净收益。B6另4resident+1cold全局计数非exact，pause API合计14.068→136.264ms、最大5.889→3.723ms；插桩不含完整BH，生产order仍9。raw/35check/失败历史及退出见[结果](incoming/macos-qemu/experimental/REPORTING-ORDER-RESULTS.md)。G2 15doc/169check及QA导出9check的新快照已封存于`.macos-work/qa/g2-cross-doc-final-review-20261101/`，后续RAM结果独立追加。
