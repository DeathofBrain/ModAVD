# G2 成功读回才发布：2026-11-01 检查点

G2 已进入独立本地包 `.macos-work/package/ModAVD-gapfix-readback`，完成构建、签名、原生GPU矩阵与Qt/headless实机验收。它不包含私有HVF物理回收实验。原游戏包和MAA未修改，游戏登录后回归仍按用户决定暂缓。

## 修复及来源

[补丁](../../../../incoming/macos-gfxstream/0002-no-gl-readback-publish-only-success.patch) SHA-256为 `34a24390e2a7ddb0845864b0863d315b4d60d30fdc657918421add65d9699982`。Google基线已有未初始化的注册cache及丢弃reader bool的void wrapper；原ModAVD G0006新增的noGL发布路径依赖这些行为，可能在首成功帧前读到未初始化cache、或在reader失败后发布旧/部分内容。Darwin native-bind overlay没有引入这些问题。

修复保留既有 `IColorBuffer` void API及vtable，增加nonvirtual `ColorBuffer::readToBytesChecked`，传播既有GL/Vulkan reader的bool。注册cache一次置零；仅noGL每display预分配一个scratch并在既有cache锁下复用。失败时不改旧cache、不发callback，成功后才转换并复制进cache。没有逐帧分配或清零；代价为额外一帧缓冲和每成功noGL帧一次copy，尚无性能对照。cache置零也影响GL注册，但GL worker/readback行为未改。这里只传播已有bool，不承诺检测所有GPU错误。

## 产物身份

[安装收据](../../../../.macos-work/qa/g0002-no-gl-build-20261101/installed.json)保存overlay、补丁及日志哈希，`build-result.json`记录exit 0，目录deep/strict签名通过。QEMU为3个构建+9个功能overlay；gfxstream为2个overlay；AEMU为1个声明overlay。

| 对象 | 当前身份 |
| --- | --- |
| QEMU tree | `3058e5f90f327708e08e4df6eff63b28362479b3` |
| gfxstream tree | `d14277bc86e27f74baa1a1716b1ee29f71587895` |
| AEMU tree | `04a678d0a729c6f9870e5f2c8149256294489298` |
| launcher SHA-256 | `4640bd3c0417773ff9d08205c9f95a112332817c9860251cf3cba5538a5d152e` |
| Qt QEMU SHA-256 | `a02ecb6a22b06190b1d727db9115187dec3011c642627cc8f237b5ca2698979a` |
| headless QEMU SHA-256 | `017fb0cf6c33fd7a8ecf56c233ae72f553bdbf6e8b8aaee4f6d95492602b2a30` |
| gfxstream backend SHA-256 | `35fbd4366185ea5985e41e495c73c910030e7522075ef23de7c6317ea6f60920` |

两个QEMU与封存D9逐字相同，backend已变。D8/D9硬件视频证据仍只属于其旧backend `67812b82…`，没有在G2上重跑。

## CPU与真实GPU证据

[CPU矩阵收据](../../../../.macos-work/qa/gfxstream-d9-readback-candidate-20261101/future-contract-runs/receipt.json)记录baseline/candidate × GLES1/0 × 普通/ASan共8组，全部编译成功。baseline GLES1每组6个、GLES0每组3个命名断言失败；candidate四组全部通过，无sanitizer诊断。逐字提取的真实函数配显式reader/renderer/lock doubles，覆盖失败前false、部分写后false、旧cache保持及成功恢复；不证明真实GPU故障或完整线程行为。

[native v4收据](../../../../.macos-work/qa/gfxstream-d9-readback-candidate-20261101/future-native-runs-v4/receipt.json)记录sealed D9/G2 × 普通/driver-ASan，每wrapper的GL/noGL两模式，总8模式、192帧。每模式覆盖RGBA/RGBX/BGRA源→RGBA/BGRA目标6种矩阵，逐字节核对真实GPU源、通道和GetPixels；noGL另验R8、宽错配、高错配的拒绝/旧cache保持/恢复，以及复制边界。G2首成功帧前cache全零且callback为0。所有模式确认实际dyld backend、Vulkan loader和MoltenVK路径，前后源/构建/运行库hash稳定，无sanitizer诊断；ASan仅新driver，非全backend/Apple框架。

历史失败没有覆盖：初版driver const-pointer编译错误未运行GPU；v3统一假定GL RGBX第四字节为255，实际为上传/raw X=31而失败。源码确认GL保持raw X、noGL才显式255；v4按各模式契约精确比较所有字节，RGB独立pattern、GL X等于raw GPU源、noGL alpha=255，不忽略字节、不加容差、未据此改生产代码。运行方法、契约与全部边界见[README](README.md)。

独立review记录37项源码、75项CPU及89项native检查，均通过；见[审核目录](../../../../.macos-work/qa/g2-reused-scratch-independent-review-20261101/assessment.json)。

## 新包双引擎实机

新包装配418个APFS clone，verify校验508项负载。复制的是正常停止的D9完整AVD，保留兼容标记；`state-clone.json`历史脚本报告23clone及9项hash/marker检查通过，只保存布尔结果，没有逐项源/目标hash对。后续实机独立确认原sentinel、32GiB、µLauncher及驱动保持。

证据位于[构建与实机目录](../../../../.macos-work/qa/g0002-no-gl-build-20261101/installed.json)，实例3/console20030，私有ADB20680；3GiB、2CPU、1280×720、32GiB。

| 检查 | Qt | headless |
| --- | --- | --- |
| 启动 | PID34148，31.794854458s，0warnings | PID42667，13.251739375s，0warnings |
| 数据/配置/实际backend装载 | `qt-retention-v2.json`，7项通过 | `headless-retention.json`，7项通过 |
| SHM生命周期 | `qt-shm-lifecycle/report.json`，7项及HOME/stream恢复通过 | `headless-shm-lifecycle/report.json`，7项及HOME/stream恢复通过 |
| 停机 | `qt-stop.json`，graceful/synced，3.64848575s | `headless-stop.json`，graceful/synced，3.383434625s |

两引擎各80次重复start、80次重复stop、40轮启停；停止时客体Settings画面确实改变而SHM header/pixels冻结，restart收到新帧，最终恢复HOME与流且boot ID保持。retention验证sentinel `87b20f4f…`、32GiB、驱动 `73ddfe09…`、µLauncher、生产order9、实际加载本包backend及同boot。原始stdout/stderr与命令收据保留。headless即时stop后ps短时仍有进程行，后续[退出观察](../../../../.macos-work/qa/g0002-no-gl-build-20261101/post-stop-processes.json)确认两PID均消失且console无listener；不把即时字段改写成已退出。Qt首次retention非root读sysfs而失败的工具异常转录另存，后续显式su0重跑通过。

这些实机使用Android host GL路径，验证SHM公共启停/冻结/恢复；不是GuestVulkanOnly→SHM整链、像素旋转等价、无撕裂、旧reader跨stop持同一mapping或MAA实际消费增强的证明。内部refcount由D9 host专项验证。真实GPU失败、任意并发teardown、其他尺寸/resize、codec及游戏长稳仍未覆盖。

[新包独立集成审核](../../../../.macos-work/qa/g2-package-independent-review-20261101/assessment.json)的706项检查全部通过：508项负载类型/权限/内容、核心及包内签名、补丁指纹、源码/构建/安装身份、两引擎raw retention/lifecycle/stop，以及退出观察。两张恢复PNG经CRC/解码、RGBA→BGRA后的SHA均与各自SHM报告精确一致；这证明保留文件内部一致，未扩大为ADB像素相等或无撕裂。
