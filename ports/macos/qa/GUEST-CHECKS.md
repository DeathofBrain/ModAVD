# 客体功能证据 helper

`guest_checks.py` 复用 `runtime.Runtime`，只访问本任务已启动的 AVD 和 `127.0.0.1:20050` 私有 ADB。每次命令前重验 ADB/emulator 记录的 PID、启动时间、私有端口/程序路径，以及 `owned_device()` 返回的 AVD 名称。身份在采集中变化会停止后续客体访问。它不会启动 ADB server、模拟器或游戏。

准备阶段只使用：

```sh
python3 ports/macos/qa/guest_checks.py --help
python3 ports/macos/qa/guest_checks.py plan
python3 ports/macos/qa/guest_checks.py run --help
```

收到 boot-ready、任务 AVD 与其私有 ADB 均已运行后：

```sh
python3 ports/macos/qa/guest_checks.py --root .macos-work/runtime run
python3 ports/macos/qa/guest_checks.py --root .macos-work/runtime run --network
```

报告默认写到任务 runtime 的 `logs/guest-checks-<随机值>.json`。`--output` 可选择新的 JSON 文件；父目录须已存在，已有文件不会覆盖。`--timeout` 范围为 5–60 秒。`run` 前检查 20 GiB 空间余量；网络测试另预留少量临时文件空间。

采集 ABI/API/开机标志、SurfaceFlinger 的 GLES renderer/version、Android Vulkan features 与可用时的 `cmd gpu vkjson`、CPU online/cpuinfo、meminfo/vmstat、data filesystem、内核版本、virtio device/features/driver，以及可读时的内核 page-reporting config/order。只采集证据，不施加内存、磁盘或 CPU 压力，不修改系统镜像，不安装工具或应用。

状态含义：

- `pass`：明确的窄条件满足，例如 ARM64/API34/开机完成、Vulkan 枚举到设备或固定网络载荷哈希相符。
- `fail`：明确的预期不符，例如客体 ABI/API 不符或成功完成 HTTP 传输但载荷哈希错误。
- `observed`：已取得信息，尚无足以判定功能通过的检查。
- `unsupported`：已有明确的能力缺失证据。
- `unavailable`：命令、权限、服务、身份或诊断数据不可用；不能直接解释为被检查功能失效。
- `not_run`：未请求可选的网络检查。

退出码 1 表示包含明确失败，2 表示入口/身份等条件使检查无法开始。退出码 0 仍须查看各项状态；`incomplete_evidence` 表示有不可用项。复合诊断命令即使最终返回成功，也保留 stderr，避免前面的权限错误被后续成功读取掩盖。单项文本最多保留 128 KiB，并记录完整输出的字节数和 SHA-256。

`--network` 在宿主 `127.0.0.1` 的随机端口提供固定的 33,792 B 自建文本。客体用现有 toybox `nc` 通过 `10.0.2.2` 读取，再计算 SHA-256。只在 `/data/local/tmp/modavd-qa-<随机值>/` 创建 owner、请求、响应和载荷文件；退出时核对 owner，逐个删除这几个文件并尝试 `rmdir`。不递归删除，不清理其他临时目录；身份丢失等导致无法清理时，报告保留确切路径。

GLES/Vulkan 枚举、balloon 驱动或 reporting 标志都不能代替实际游戏渲染、性能或物理内存归还测试。此 helper 不作游戏性能结论。

合成验证不启动 ADB 或访问客体：

```sh
python3 -m unittest discover -s ports/macos/qa -p 'test_guest_checks.py' -v
```

## 2026-10-31 实机采集

官方 API 34 ARM64 Google APIs 客体的最终报告为本地 `../../../.macos-work/runtime/logs/guest-checks-fd67801388e6450bbe20b26f2fd1da6b.json`，采集时间 `2026-10-31T15:14:36.815090+00:00`，文件 SHA-256 为 `8469b6cbe610be43c666bb02f8183f6a8cd99770c8882abe67c4b6657b93293d`。共 3 `pass`、7 `observed`、1 `unavailable`，无明确失败；总状态为 `incomplete_evidence`。

- ARM64/API 34/boot_completed=1；4 个在线 vCPU，MemTotal 4,016,820 kB；`/data` 为 ext4，总量 6,082,144 KiB。
- SurfaceFlinger：`Google (Apple), Android Emulator OpenGL ES Translator (Apple M4), OpenGL ES 3.0 (4.1 Metal - 90.5)`。Vulkan 枚举到 1 个 Apple M4 设备，API 1.3.0。这些结果验证设备枚举，尚未覆盖真实游戏绘制。
- 33,792 B loopback HTTP 载荷 SHA-256 与固定预期一致，任务客体临时目录已清理。
- virtio driver symlink 可见 `virtio_balloon`，但 `device`、`features` 均 `Permission denied`，因此该项为 `unavailable`，不能确认 reporting feature 协商。
- `/proc/config.gz` 同时显示 `CONFIG_PAGE_REPORTING=y` 与 `# CONFIG_VIRTIO_BALLOON is not set`。此输出与可见 driver 绑定并存，保留原始事实，未据此断言 reporting 可用或物理内存已归还。没有提权、改镜像或施加回收压力。

早先报告 `guest-checks-4947f8013d0d48d498a4f20cedfa228f.json` 未合并成功复合命令的 stderr，不能用其 `virtio: observed` 状态作完整证据；以上最终报告已修正这一点。
