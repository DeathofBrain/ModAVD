# Manager 包实例 SHM 截图工具

`capture_manager.py` 读取已经启动并完成引导的 Manager 包实例，不启动模拟器或 ADB 服务，不安装或启动客体应用。默认包为 `.macos-work/package/ModAVD-validated`、实例为 `0`、私有 ADB 端口为 `20050`。工具仅用于本项目 macOS 开发验收。

Manager 的启动流程已建立 SHM 流时，显式借用该流：

```sh
python3 ports/macos/qa/capture_manager.py \
  --root /Users/azuki/ModAVD/.macos-work/package/ModAVD-validated \
  --index 0 --reuse-stream \
  --output /Users/azuki/ModAVD/.macos-work/qa/manager-game-frame-001
```

`--reuse-stream` 要求 Manager `info --json` 明确报告 `screencap.shm_ready=true`。工具只读取流，读取前后均核对身份；无论成功还是失败，都不会向借用流发送 start 或 stop。证据中的 `streamOwnership` 为 `borrowed_existing`。

不传 `--reuse-stream` 时，工具要求 `shm_ready=false`，自行启动流并在 `finally` 中重新核对身份后停止；证据标记为 `started_for_capture`。如果包、ADB 服务或模拟器身份变化，拒绝向替代进程发送清理命令并返回失败。未知状态或现成流均不自动接管。

归属检查同时使用包所有权标记、manifest 中 CLI/ADB 的 SHA-256、私有监听进程及其实际可执行文件、Manager `info/status --json`、内核读取的模拟器 PID/启动时间/可执行文件/参数、运行时 `pid_<PID>.ini` 中的 AVD 目录、`avd.id` 与端口、以及 console 返回的 `avd id`。`avd.name` 是显示标题，不能替代实际 ID；serial 本身也不构成归属证明。仅从进程环境读取 Android 目录发现变量与 HOME，不输出其他环境变量。

运行记录位置遵循 pinned core 的 Darwin `ConfigDirs::getDiscoveryDirectory`：模拟器进程的 HOME 非空时，只读取 `$HOME/Library/Caches/TemporaryItems/avd/running/pid_<PID>.ini`；仅 HOME 为空时才按 `getUserDirectory` 规则回退到 Android 用户目录。不会遍历 TMPDIR 或搜索其他目录，也不会因主路径缺失而改读旧记录。

运行记录证据仅保存白名单字段 `avd.dir`、`avd.id`、`avd.name`、`port.serial`、`port.adb`、`emulator.version`、`emulator.build` 及原始文件 SHA-256；不保存或输出 `grpc.token` 等其他字段。

实例 0 只读取 `videmulator20000`，其他允许的 Manager 索引按 `20000+10*index` 计算；端口与 JSON、进程参数和运行记录必须一致。输出目录必须不存在，父目录必须存在，且不得位于 SDK 内。保留至少 20 GiB 空间；固定文件名使用 exclusive/no-follow 写入，不覆盖已有结果，不移除上游 SHM。失败时可能留下空目录或部分结果，应检查错误并使用新目录名重试。

输出 `frame.png` 与 `evidence.json`，复用 [capture_frame.py](capture_frame.py) 的有界 SHM 解析与标准库 PNG 编码。证据记录包、进程、Manager 观测、SHM 头部、像素与 PNG 的 SHA-256、流归属及清理结果。上游没有序列锁；两次头部相同不能证明像素无撕裂，也不表示与另一时刻截图逐像素同步。

合成测试不会连接 Manager、ADB 或客体，也不会读取已有 SHM：

```sh
python3 -m unittest ports.macos.qa.test_capture_manager ports.macos.qa.test_capture_frame -v
```
