# Android ARM64 Vulkan GPU 回归探针

该目录同时包含 ARM64 客体候选驱动的构建/验证工具；本文仅说明 `probe.c`、三个 `.comp`、`build.py` 和 `run_probe.py`。探针使用 Android 客体的真实 `libvulkan.so`，实际提交 compute dispatch，并读回 GPU 输出。

## 构建

在仓库根目录执行：

```sh
python3 incoming/macos-guest-vulkan/build.py
```

默认使用 Google NDK r27d Darwin ZIP，SHA1 固定为 `2970926d705988f79baa9b04c51b4f7914dd8c56`。脚本只提取编译器与 sysroot；三个 shader 由已获取的 AOSP `glslangValidator` 编译为 Vulkan 1.1 SPIR-V。C 编译使用 `aarch64-linux-android34-clang -std=c11 -O2 -Wall -Wextra -Werror -fPIE -pie`。

输出位于 `.macos-work/qa/guest-vulkan-probe/`，包含 ELF、SPIR-V 和记录源码及二进制 SHA256 的 `build-receipt.json`。无需把 NDK 或着色器编译器带入发布包。

## 运行

`run_probe.py` 不替换驱动、不启动或停止模拟器。调用方先准备一次性实例，明确传入其 QEMU PID、隔离 ADB server 端口、serial、安装根目录与预期驱动 SHA256。脚本核对 PID 的启动时间、QEMU 路径和 `-ports`，再核对客体 ABI、boot ID、驱动和推送后 ELF 的 SHA256。每次运行使用全新证据目录。

当前会话保留的验证游戏实例 PID `54065` 及端口 `20050` 被明确排除。脚本也拒绝默认 ADB server 端口 `5037`。

原始 API34 revision14 驱动负对照示例，PID 必须替换成运行时确认的值：

```sh
python3 incoming/macos-guest-vulkan/run_probe.py \
  --adb .macos-work/package/ModAVD-gapfix-native/sdk/platform-tools/adb \
  --adb-port 20350 --serial emulator-20030 --qemu-pid 89203 \
  --root .macos-work/package/ModAVD-gapfix-native \
  --expected-driver-sha256 d6188011ed2ed58a1d69adfb04246abaa2595c0f15565a22da83f7751a3d6828 \
  --iterations 128 --output .macos-work/qa/guest-vulkan-original
```

候选 v2 的预期 SHA256 为 `73ddfe0935826feef1f1a78d5a70af3ee7fa83584d2efaf5bad6ee91f36eb613`。QA 调用方可使用同目录的 `temporary-candidate-mount.sh` 在 adbd 的 mount namespace 中临时挂载候选，再使用全新证据目录运行同一命令；此 standalone native probe 由 adbd fresh exec，会直接加载候选，因此临时探针验证无需重启隔离实例或 zygote。完成后按脚本 rollback 并确认原库 SHA256。生产 Manager 的自动挂载仍保留 zygote 重启和运行中应用/安装门禁，以覆盖既有应用预加载的驱动。

每次产生：

- `identity.json`：运行前的 QEMU、ADB、boot ID、客体驱动与 ELF 身份。
- `stdout.json`：GPU 读回与压力结果；发生 Vulkan API 初始化失败时可能只有部分 JSON。
- `stderr.txt`：API/系统错误。
- `receipt.json`：退出码、结果 JSON 状态、运行后身份与驱动不变检查。

探针使用客体侧 240 秒超时，执行通过无 PTY 的 ADB shell v2 透传远端退出码。退出码 `0` 表示所有语义检查通过；`1` 表示检查失败；`2` 表示初始化、身份或工具错误。原始驱动负对照出现 `1` 是待修缺陷的证据，不能被记成探针通过。若客体侧超时或崩溃，保留退出码和原始输出继续排查。最早两次实机对照使用 `exec-out`，旧 receipt 中 `exit_code` 是 ADB 客户端状态；语义判定使用完整 JSON 的 `pass`，原驱动仍明确失败，候选明确通过。

## 检查内容与预期

1. 对 storage buffer、storage image、uniform texel buffer 各自先使用普通 `vkUpdateDescriptorSets`，读取三个不同输入。分别应为 `[7,11,17]`、`[19,23,29]`、`[31,37,41]`。
2. 每种类型均运行 `core/core`、`core/KHR`、`KHR/core`、`KHR/KHR` 四组 template create/update。模板包含同类型的二元素数组及后续单元素 binding，非零 offset 和两段间隙。所有组必须读回同一组预期值。每次模板更新前先把所有输入设成第一个合法资源，避免被丢弃的 KHR 更新留下无效句柄；该缺陷通常表现为三个重复值。
3. 查询含 `size_t` 字段的 descriptor-buffer 及 acceleration/ray-tracing properties 链。记录客体 `sizeof(size_t)`、pNext 链完整性；当前未宣告的扩展属性应为零。这是对补丁的特定回归约定，不用于判定任意 Vulkan 实现是否合规。
4. 四个 pthread 同时在同一 VkDevice 上创建、分配、绑定并销毁各自独立的 image/buffer。循环交替原始 bind、core BindMemory2、KHR BindMemory2；默认每线程 128 组，总计 512 组 / 1024 次 bind，其中原始与 core 各 344 次、KHR 336 次。没有线程共享同一个 image、buffer 或 VkDeviceMemory，也没有在线程间共享 queue 调用。

当前 ARM64 候选 v2 应使所有 reference、12 组模板 GPU 读回、pNext 检查及四线程压力均通过。缺少 KHR template 扩展会明确记为未覆盖/失败，不会静默跳过。
