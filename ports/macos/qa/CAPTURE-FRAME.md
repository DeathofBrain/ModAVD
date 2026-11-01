# SHM 帧导出开发工具

`capture_frame.py` 仅用于本项目隔离 QA 实例，不连接 MAA，也不安装或启动客体应用。工具检查 runtime 标记、私有 ADB 与模拟器 PID/启动时间、ADB 协议就绪状态，并复用 `Runtime.owned_device()` 核对客体 AVD 名称。它不会启动 ADB 服务或模拟器。

已经运行的 Manager 包实例使用单独的 [capture_manager.py](CAPTURE-MANAGER.md)，其归属检查与目录结构不同。

先启动并验收本任务的 QA 实例，再运行：

```sh
python3 ports/macos/qa/capture_frame.py \
  --root /Users/azuki/ModAVD/.macos-work/runtime \
  --output /Users/azuki/ModAVD/.macos-work/qa/frame-001
```

`--output` 必须是不存在的新目录，父目录必须已存在；已有文件、目录和符号链接均拒绝。输出不得位于运行目录的 SDK 内。固定文件名通过目录文件描述符和 exclusive/no-follow 打开，不覆盖现有文件。失败时可能保留空目录或不完整证据目录，应检查错误后改用新名字。

工具在 `try/finally` 中启动和停止 `screenrecord webrtc`，停止前再次核对归属；如果进程身份改变，则拒绝对新进程发送清理命令并返回失败。只读取精确名称 `videmulator20060`，不添加 Windows `SHM_` 前缀，也不移除共享内存对象。

输出 `frame.png` 和 `evidence.json`。证据包含 24 字节小端头部、帧号、时间戳、尺寸、BGRA 像素 SHA-256、PNG SHA-256 和进程身份。PNG 使用标准库 zlib 编码为 RGBA8，保留行顺序和 alpha；无需 Pillow。映射限制为 24 B 至 512 MiB，单帧最多 64 MiB，复制时检查尺寸、fps、发布时间戳和负载边界。静态画面不要求帧号增加。本工具暂不采集 ADB screencap。

复制前后头部相同仅表示头部未改变。上游接口没有锁或序列锁，不能保证像素来自同一个无撕裂帧，也不能将该结果解释为与另一时刻截图逐像素同步。

合成验证不会访问现有 SHM、ADB 或客体：

```sh
python3 -m unittest ports.macos.qa.test_capture_frame -v
```
