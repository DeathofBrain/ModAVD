# 官方 AVD 已完成基础证据独立审计

审计快照：2026-11-01 11:36（Asia/Shanghai）。仅对既有文件做本地只读重算、格式复验及证据索引核对，没有连接客体、执行 ADB、启动模拟器或新增测试轮次。主审计与截图/SHM 的独立子审计分别复核结果；只在审计结束后生成本文并校准主报告的方法说明。

**结论：所审项目的次数、数值与完成状态均与原始证据一致，审计通过。** 发现两处需要精确描述的口径：PNG 原哈希是滤波扫描行哈希；SHM 的 60 份像素读取中有 2 份拷贝前后帧号改变。两项均已写入主报告；没有因此修改截图有效数、去重数或实测帧率。审计不把“验证失败结果的证据一致”表述为扩容测试通过。

## 范围与逐项复核

| 项目 | 独立核对方法 | 审计结果 |
|---|---|---|
| sync 后关机 10 轮 | 重算本地 10 份 1 MiB payload 的 SHA-256；逐轮解析原始 verify stdout，与 expected/actual 比较；核对 push、sync、verify 的命令索引与返回码，以及 shutdown 返回码 | 10/10 大小与摘要一致，返回码均为 0；通过状态正确 |
| 不 sync 直接关闭 | 从 30 份 verify stdout 重建 960 个唯一路径结果；按每轮 key 重建 64 KiB 新旧载荷摘要，独立分类；核对写入 CLOSED 标记和验证命令成功；重算每档计时中位数 | 总计 30 轮，0/100/1000 ms **各 10 轮**，不是每档 30 轮；创建 480/480 零字节，覆盖 1/480 零字节、479/480 新内容；没有其他分类。失败状态及合成负载范围正确 |
| 截图 | 从 screenshots.json 的 samples 日志索引枚举全部 400 份原始 stdout，独立格式复验和摘要检查；从耗时列表重算中位数及线性插值 P95 | 400/400 有效；四组各 100；P50/P95 与报告一致。raw 去重 5/98；PNG 扫描行及独立解滤后 RGBA 去重均为 1/96 |
| 共享内存视频 | 重算全部 5415 行轮询 CSV 的 delta、帧号起止、逆跳和像素采样；核对 JSON 实际观察秒数及首帧载荷摘要 | 122 → 3717，推进 3595，非零推进记录 3592，0 逆跳；60.007504750043154 s，59.90917327715438 fps；60 个像素摘要均不同。58 份 stable_header=true，2 份 false |
| 1 GiB 分配释放 3 轮 | 由 rounds.json 单调时钟确认完整观察；读取各轮 touched/released 事件、返回码；从原始 CSV 重算持有末 3 点和释放末 12 点中位数及差值 | 3/3 完整观察 600 s，403 点，零采样错误；全部中位数/差值与报告一致。宿主相应 GiB 量级净回收未获证实，未错误认定为泄漏 |
| 自动扩容首次 + 3 复测 | 比较 marker host 摘要与 before/after；检查实际 df、hardware 和两层 qcow2；核对源备份指纹、停机和端口状态 | 4 次均未增长客体容量；三次配置复测两层仍为 8 GiB，df 前后均 8,302,063,616 B；marker 完好。失败状态正确 |
| 手动扩容首次 + 3 复测 | 逐轮核对 qemu-img、块设备、resize2fs、df、清理 marker、冷启动复查及内核记录；复核源指纹 | 两层镜像及块设备均 32 GiB，但 4 次 resize2fs 均返回 1，df 均仅为 10,547,974,144 B；4 次冷启动维持部分容量且 marker 一致。失败状态正确，不把冷启动 marker 通过误作扩容通过 |

另核对扩容 JSON 中 125 处 stdout / 命令返回码引用，均与原始日志及 `logs/commands.jsonl` 一致；其中可能多次引用同一日志，不表示新增 125 次试验。只读检查当前已停机克隆的两层 qcow2 头，与各自最终容量一致；源备份当前配置 SHA-256、记录文件大小与 mtime 均与结束指纹一致。源保护证据是所述元数据及头指纹，不是全部数据盘逐字节哈希。

正常关机表中的开机时间是 10 次关机后重启的 `boot_after_s`：中位 12.36637193756178 s，最小 12.240311541012488 s，最大 17.612429874949157 s；不是全部测试开机的总体统计。未 sync 计时仍按宿主接收 CLOSED 标记至 kill 调用，三档中位为 4.587458504829556、108.69793751044199、1008.5261255735531 ms，不能据此获得客体实际 close 的时间。

## 截图哈希与格式边界

输入按 `results/screenshots.json` 的 `scenes.{static,dynamic}.formats.{raw,png}.samples[*].log + ".stdout"` 枚举，每组 100 份；没有仅检查抽存的第 1/50/100 张图片。

200 份 raw 均为小端 1280×720、pixel_format=1、16 B 头和 3,686,400 B RGBA8888 载荷，总长 3,686,416 B。载荷摘要与原记录一致。该复验支持布局、长度和证据一致，不证明每个像素语义正确、与当时屏幕完全一致或无撕裂。

200 份 PNG 均通过签名、每个 chunk CRC32、IHDR、全部 IDAT 解压、完整 IEND 及尾部无多余字节检查；IHDR 均为 1280×720、8-bit RGBA、compression=0、filter-method=0、interlace=0。解压扫描行长度均为 720×5121=3,687,120 B。IHDR 的 filter-method=0 不表示每行均未滤波；实际行滤波类型包含 1–4。

原 `pixel_sha256` 哈希覆盖解压后的滤波扫描行，包括每行 filter 字节，未先恢复像素。独立检查使用本地 Node.js 的 fs/zlib/crypto，行跨度 5120 B、像素跨度 4 B，按 PNG 0 None、1 Sub、2 Up、3 Average、4 Paeth 还原 RGBA，缺失邻居视为 0，复原模 256；Paeth 平局按左、上、左上顺序处理。按已复验的扫描行摘要缓存相同输入，实际解滤 97 份不同输入；真实 RGBA 去重仍为静态 1、动态 96。原始输入和结果未改写。

四组 P50/P95（ms）独立重算仍为静态 raw 172.68/258.19、静态 PNG 300.96/321.16、动态 raw 179.31/205.91、动态 PNG 79.21/82.76。PNG 压缩复杂度和不同采样时刻的画面不受控制，不能从此比较中推断普遍速度优势或跨方法像素一致性。

## SHM 并发读取的两份样本

像素采样按约 1 Hz 进行，共 60 份；以下行号包含 CSV 表头。`stable_header=false` 表示像素拷贝后读取的帧号与拷贝前不同。

| shared-video-frames.csv 行 | elapsed_s | 拷贝前 frame_number | timestamp_us | delta | stable_header |
|---:|---:|---:|---:|---:|---|
| 3520 | 39.00072391703725 | 2457 | 1793500673746530 | 0 | false |
| 4241 | 47.000480417045765 | 2937 | 1793500681749364 | 0 | false |

这里 delta 比较本次初始 header 与上次轮询；其值为 0 不能否定拷贝期间的推进。脚本未保存拷贝后的具体帧号，不能量化跨越几帧，也不能单凭该字段认定出现可见撕裂。帧率取全部帧号推进量除以真实观察时长，不用 60 fps 目标值或 3592 次非零观察次数替代。

## 内存探针版本与测量复算

本轮内存使用的是已归档旧探针 `probes/history/pre-line-framing/build/avd_probe`，9985 B；实际文件 SHA-256 为 `23b6c5ca97cb1d865b3bb0c06c9f54b786e9d5ccc622788dfd34c99605e728ee`，与 `results/probe_prepare/probe_prepare.json` 部署回执、归档 `build_manifest.json` 的二进制摘要一致。归档源代码确实对每 4096 B 页进行 volatile 非零触碰并成功 munmap；没有用后续 line64 网络探针的摘要替代旧版本。该核验不是重新编译或执行探针。

三轮 touched→released 分别为 60.058900820、60.050383945、60.045328112 s；观察实际时长分别为 600.005295250、600.005538667、600.001242417 s。释放阶段每轮 120 点，其首末约 595 s 与完整观察 600 s 并不冲突。实际采样间隔范围 4.992–5.008 s。

持有末 3 点与释放末 12 点中位数的 Δ，guest MemAvailable 为 +1257.908203125、+1023.677734375、+1037.8125 MiB，emulator phys_footprint 为 +37.999977112、+5.5234375、−0.15625 MiB，RSS 为 −559.6953125、−975.1171875、−1172.984375 MiB。报告保留两位小数正确。全宿主压缩、swap 的首末与极值也与 CSV 一致；不把这些全局统计全部归因于模拟器，不把 RSS 下降替代 phys_footprint 回收。

## 关键输入快照

以下为本次读取文件的完整 SHA-256，标识审计使用的证据版本；其他逐轮命令日志由对应 JSON 索引。相对路径均基于 `${WORKSPACE}`。

| 输入 | SHA-256 |
|---|---|
| `results/sync_shutdown.json` | `d33c5215f44fd8f7a049a6774901d7b17a0f0400c9ccdf09c036d24b55ad8826` |
| `results/no_sync_shutdown.json` | `c30a7e6b3aa53166f68fcc81037d36a9b7b86efd8f810f0d7da3a1efef68cd71` |
| `results/screenshots.json` | `27069ae9a5420fedb456794fe4a13e20784de22224bdf0ba3fa49d2790834546` |
| `results/screenshots-static-raw.csv` | `c39641e0970ed8a840bbe05ddb451532c81b8ca309a68698b69d3b6d497c5e72` |
| `results/screenshots-static-png.csv` | `0272a6520348681eb91d5662cff7f1e87975f71222d6da8407a7f6b7dce2dc51` |
| `results/screenshots-dynamic-raw.csv` | `27a3e505f244cb232182fc6dec9080b9426859f357e7f5062d51b2559069c940` |
| `results/screenshots-dynamic-png.csv` | `b870e0cca658d3b1388d7a54ebe9f8f2461c5679707b2a3cf725b05cf32345b9` |
| `results/shared_video.json` | `a5b3f23644fd4149b1889b418febae5a3426f0da4e9431684aad69ee452a9202` |
| `results/shared-video-frames.csv` | `4e7c762a7871d04e17a3362b86c7cb4b928a18938fae437bc29068418b21468d` |
| `results/memory/memory.csv` | `21189c036d098ee91da39b49ed89eea40e7b81000bae5fe649e44863c4a2047e` |
| `results/memory/rounds.json` | `a0cb48a7a82678944b18ed2c11a90dd7cb226aff55244dc3fe11255d28627819` |
| `results/memory/metadata.json` | `f1f6ff1d6e8bca0cd9eb8d3d1d6294d2a97fe18da52605f0bc1397b541b3fac4` |
| `results/memory/memory_summary.json` | `9793acf592147b8b5d64f0a596fbe55c45751c20213e4d598ada72ec5d25b925` |
| `results/probe_prepare/probe_prepare.json` | `7f7a908e5750681f5cb5591e753673307f1c8c7583503b3a1ae2952abc7f512d` |
| `probes/history/pre-line-framing/build/build_manifest.json` | `0ccd4bc87b888a0bd8cd67a3be355b442d67eb0bf77d96911225e54c860928eb` |
| `probes/history/pre-line-framing/avd_probe.c` | `b5f929977a07b290399734dd58fdc664d8bb5f20f6d384e26e33af71152176ab` |
| `results/partition_expansion.json` | `446c3de5332ed2fb68f8bc929ecdf4c6ee5114b2de868307711f44d9864aff25` |
| `results/expansion_retests.json` | `920e220e690edefb1587cffcea0d78db8737c5079eaeecdffdf7bbca9495950a` |
| `results/manual_expansion_retests.json` | `838724c81076436be227ca7bac7b64e9041e658fe849b35af04e41587d8eb68c` |
| `results/expansion-marker.bin` | `861a2e19cddb688fb25f2e4e4643f6a625903da083bf99d0030ad550da83257a` |

7200 秒空载重跑编排尚未形成最终结果，未纳入上述“完成状态通过”的审计判断；第三次启动及成对预检不能替代两小时观察。游戏安装、登录、热更新、MAA 与游戏挂机亦未纳入已完成结论。本文没有将部分网络运行或预检问题归因于 AVD。

## 追加审计：已完成的 7200 秒空载内存与双组网络对照

追加审计快照：2026-11-01 13:40（Asia/Shanghai）。本节新增审计范围是正式 `results/soak/` 与 `results/soak/wan/` 完整运行，实际执行时间为 11:32:32–13:32:33（Asia/Shanghai）；此前 11:36 快照中的“长测未完成”保留为当时状态。本节仍仅本地读取文件并独立重算，没有连接客体、重跑探针、修改脚本或使用正在进行的游戏测试结果，也没有修改主报告。

**追加审计通过：完整时长、四条客户端的校验心跳覆盖、零错误/零重连、1441 个有效内存样本及代码回执均与最终汇总一致。** 两个网络组均为 passed，orchestration 为 passed；这验证本次空载控制条件下的观察，不等同于游戏长测、游戏热更新或不限条件的网络稳定性结论。

### 四条原始事件流与派生 CSV

逐文件解析 JSONL 全部行，独立核对以下条件，未发现无效行、未知事件、逆序时间戳或证据缺失：

- 各流恰好一个首部 `network_config`、一个 `connected`、一个末尾 `network_complete`。配置与本组 metadata 的端点、端口、7200 s、1000 ms、负载大小及 framing 一致。
- `bulk_ack` 与 `heartbeat_ack` 合并后的 sequence 从 0 连续递增；host 结束事件的 sequence、guest 结束事件的 value 均等于实际包数。每个 ACK 均记录完整 sent/received 字节、errno=0、非负请求耗时；两个 guest stderr 均为空，退出码均为 0。
- 本地组各有 512 个 64 KiB bulk ACK，即 32 MiB；其后为 64 KiB 心跳。远端组无 bulk，全部为 64 B line64 心跳。未将 bulk 包计入 heartbeat ACK 数。
- 四份 CSV 与 JSONL 按 CSV 的固定列投影逐行一致。保留 JSONL 中的完整配置和 guest 结束事件 value；CSV 未包含的字段不被误判为原始事件缺失。

下表数值均由原始单调时钟重新计算；host、guest 各自计时，不跨时钟相减。事件窗是首个配置事件至最终完成事件，ACK 跨度是首末 heartbeat ACK 之间的时间。

| 客户端 | JSONL 事件数 | heartbeat ACK | bulk ACK | 事件窗 s | heartbeat ACK 跨度 s | 最大相邻 ACK 间隔 s | 错误 / 重连 |
|---|---:|---:|---:|---:|---:|---:|---|
| 本地 host | 7669 | 7154 | 512 | 7200.574223250 | 7199.522286041 | 1.012836959 | 0 / 0 |
| 本地 guest | 7694 | 7179 | 512 | 7200.989935309 | 7195.904435348 | 1.061759876 | 0 / 0 |
| 远端 host | 7155 | 7152 | 0 | 7200.166284375 | 7198.757738125 | 1.129270375 | 0 / 0 |
| 远端 guest | 7186 | 7183 | 0 | 7200.981759184 | 7199.091133099 | 1.439956459 | 0 / 0 |

不依赖汇总中的 passed 字段，重新检查其完成判据：事件窗至少 7198 s；最低 heartbeat 跨度为 `7200 − 本流实际 bulk 耗时 − 3 s`；ACK 数至少为该跨度除以 1 s 后乘 0.99 并向下取整；相邻 ACK 间隔不得超过 3 s。四流最低跨度分别为 7196.958647708、7192.908531873、7197、7197 s，最低 ACK 数分别为 7124、7120、7125、7125；实测全部满足。计数的 1% 调度余量是启动前已固定的实现，不意味着每秒都有精确一次 ACK；实际最大间隔已单列，不能仅凭总包数判断覆盖。

独立重算总收发和已校验接收字节，本地 host 为 502,398,976 B、本地 guest 为 504,037,376 B、远端 host 为 457,728 B、远端 guest 为 459,712 B，三种口径在本次零错误运行中均一致。中位/P95/最大请求耗时、bulk 耗时和汇总中的派生流量指标也与原始事件一致。本次没有恢复事件或重新握手，故未测得断线恢复耗时，不能写成“恢复用时 0”。

### 内容校验与正式版本绑定

本轮代码回执为 `metadata/idle-soak-code-receipt.json`，其列出的 8 份脚本、源代码、二进制与 manifest 逐个重新计算 SHA-256，全部吻合。正式部署回执 `results/idle_probe_prepare/probe_prepare.json`、`probes/build/build_manifest.json` 内的二进制摘要及实际 12372 B 的 `probes/build/avd_probe` 均为 `8d8b76ea4b9b1ce5181ee2de338ac7fd0524b7c104bfe0b534b19a9534c95a74`；chmod、16 MiB smoke 均返回 0。此版本用于本节网络控制，区别于此前 1 GiB 三轮测试的旧 23b6… 探针，不替换旧内存证据。

绑定版本的 host 实现在完整收回期望长度后逐字节比较 payload，内容不同即产生 error；guest C 实现同样在完整收回后比较 tx/rx，只有 errno=0 才产生 ACK。远端 line64 的前 16 字节编码 sequence，后续为可打印字节且只在末尾含 LF；原始配置事件确认实际选择了该模式。本审计核对了这些分支及其版本摘要、每个 ACK 的结构、长度和错误码；原始网络日志不保存 tx/rx 字节，因此未声称做了独立抓包或事后逐字节网络载荷重放校验。

正式 orchestration 所引用的预检输入 SHA-256 与 `results/remote-preflight-retests/round-2/guest_network.jsonl` 实际文件一致，记录端点 198.18.0.4:4242、line64、64 B、无 bulk、10 个 ACK，未误用早期失败/部分运行的证据。远端目标为 tcpbin.com:4242，198.18.0.4 属当前宿主路由下的服务路径，可能经过 FakeIP/TUN/代理；本审计不将其描述为直连公网。本地组只覆盖 host loopback 对照和 guest NAT。

### 单一内存监控器与原始采样

两个网络组 metadata 指向同一份 `results/soak/memory.csv`，orchestration 声明一个监控器。记录的模拟器 PID 为 15290，与 `logs/launches.jsonl` 中唯一同 PID 的 `official_clean` 无窗口 host GPU 启动吻合；未查询当前客体或把后续游戏 PID 混入本轮。

CSV 共 1441 点，全为 `idle_non_game_control`，elapsed 从 0.000 至 7200.010 s，单调间隔为 4.990–5.010 s；每次采样耗时为 21.554–80.143 ms。关键宿主与客体字段无缺失，host_memory_error、guest_memory_error 均为空，summary 的两个错误计数均为 0。duration 和间隔采用单调时钟；UTC timestamp 的相邻跨度为 4.780132–5.101800 s，不以墙钟差替代单调观察窗。

独立从原始 CSV 重算五项指标的首、末、最小、最大、变化及全程最小二乘斜率，与 memory_summary.json 一致。下表列出其中变化和斜率，单位为 MiB；斜率只是本轮样本的线性描述。

| 指标 | 末−首 MiB | 全程线性斜率 MiB/h |
|---|---:|---:|
| emulator RSS | −2325.609375 | −46.009740 |
| emulator phys_footprint | +180.593796 | +55.439636 |
| 全宿主 compressor physical | +493.156250 | +77.490604 |
| 全宿主 swap used | −144.000000 | −75.541759 |
| guest MemAvailable | +318.191406 | +31.540296 |

全程最小磁盘可用空间为 132,200,267,776 B（123.12109375 GiB），与汇总一致，未低于 20 GiB 预留。orchestration 单调总耗时为 7201.386299042031 s，包含启动与收尾；它不代替各流真实事件窗或 7200.010 s 的内存样本窗。RSS、phys_footprint 的进程口径与全宿主压缩/swap 口径保持区分；本审计不从这些变化单独推断内存泄漏或回收机制。

### 追加输入摘要

以下为本节读取版本的完整 SHA-256；路径同样相对 `${WORKSPACE}`。此前失败/部分运行不在这些正式文件的时间窗内，未将它们合并计算满两小时。

| 输入 | SHA-256 |
|---|---|
| `metadata/idle-soak-code-receipt.json` | `71343e48532b5f4a1e121e66fc0536214bfe1679d3419c01bd855bf8dad749ce` |
| `results/idle_probe_prepare/probe_prepare.json` | `0f6863586144fa844f1b9e9a4f623913e36aa844e7da7e7c382de152304034b3` |
| `probes/build/avd_probe` | `8d8b76ea4b9b1ce5181ee2de338ac7fd0524b7c104bfe0b534b19a9534c95a74` |
| `results/remote-preflight-retests/round-2/guest_network.jsonl` | `e86ff489cb7d565a54b045dce3e3b968316acb8d61e0acbe0cd3d849b2bc593a` |
| `results/soak/orchestration.json` | `cb422c2e0b02f56a48059c0176ce0c1eb336486023ab4be24e567dfc4cec58a3` |
| `results/soak/metadata.json` | `2c1feac7a7a9159930b094dc8fa68f8c61b6506d9298e0a2e3cc386ba5653a40` |
| `results/soak/network_summary.json` | `bd7c5175b89fc69086454a9020d2596394e2650a320f15731195bac49fb7aeab` |
| `results/soak/host_network.jsonl` | `2adadb3c523b5a203ee648aee5531d710d7974e9903250257ee5a27e7e8a1bf9` |
| `results/soak/host_network.csv` | `a53ee390052a964a11dd203a8bc44603d30fb50ee07d6d05b91580c75f052138` |
| `results/soak/guest_network.jsonl` | `d10ab88e97a44770d1e958a852f3b42728e2ff26e23235e68a3493e45e17a997` |
| `results/soak/guest_network.csv` | `1f7bb91db198caa545fd8e9b07391f2d32e3539e07b600df5a77593ef9584ba7` |
| `results/soak/memory.csv` | `487797b2af68cebce69b0f0a8d880e4cd967f1b51d983d0e4eea1c38b0a78cc1` |
| `results/soak/memory_summary.json` | `4810649e6757f19e012f6dac7836c7c5067abdb56ab753267eb4c13a00a0460c` |
| `results/soak/wan/metadata.json` | `26d2c4a731cf24a4cd5d923e116090bb4f3cc5f72dc17aec32364c81f2a52010` |
| `results/soak/wan/network_summary.json` | `89a950ad8f6f628304eb38b7190cc2d9df283244a98f154027aa852d922876f0` |
| `results/soak/wan/host_network.jsonl` | `5296f78beb404e6843d45c65b2c1bb00c06158c548baeefa3cf23d5f7e0c9ae8` |
| `results/soak/wan/host_network.csv` | `00b07d31440526124a1d0e9969b58035df0dbcc7a32b09e65afba5bdd6b632b9` |
| `results/soak/wan/guest_network.jsonl` | `d1b53343d3101329d67ee4523230ad6d836eed5dc910a7c074f337429a26bd63` |
| `results/soak/wan/guest_network.csv` | `77a66231814a59d401c3d974550c8e24fc83ad2a5480c5f87597a7c1f0ca6acd` |

游戏仍完全在本节审计范围之外：本次正式运行是空载控制，不能据此宣称游戏安装、进服、热更新、MAA 或真实游戏挂机已通过。


## 追加审计：游戏登录前安装、三次冷启动、MAA 实际取帧与缓存范围

追加审计快照：2026-11-01 14:02:45（Asia/Shanghai）。本节仅本地读取已有文件、重算哈希/PNG/RPC 数据及只读查询隔离测试 SQLite；没有操作客体、调用 ADB、同意协议、输入账号、执行游戏控制或修改主报告。此前快照的“游戏不在当节范围”保留为当时审计状态，本节新增登录前覆盖。

**报告与证据一致：安装、协议页显示及一次 MAA 连接取帧有有效证据；首次退回桌面需保留，非空真实游戏缓存、进服、识别控制、日常与游戏挂机仍阻塞。** 已核对两份主报告的当前游戏段，未把 raw cache 状态或接口能力标志当作游戏通过；下表记录的是本次读取版本，后续报告修订应另作快照。

### 安装与首次失败、三次冷启动复测

独立重算选定 APK 的完整 1,886,746,222 B 文件，SHA-256 为 `9106db6d9038d11a732cdc35773e68d79895834283718895c6f929d496577df9`，与安装记录一致。首次 `official_game` 为全新 32 GiB userdata，安装返回 0、stdout 为 Success，用时 15.153678 s；四次 Package Manager 原始 stdout 均确认同一包名、versionName 2.7.81、versionCode 200、primaryCpuAbi arm64-v8a、minSdk 24、targetSdk 35。客体 df 的 32,847,728 个 1 KiB blocks 即 33,636,073,472 B；这是新盘，不能替代旧 8 GiB 数据盘扩容的失败结果。

首次 am start 返回 ok / COLD 并报告 UserProtocolNoticeActivity，但保存的 5/15/30 s 三图均为 Launcher，最终游戏主 pid 为空、前台为 NexusLauncher。因此记录首次启动失败成立；有限 logcat 不能确定退出原因。随后三轮复测的模拟器 PID 各异，启动参数均为 no-snapshot / no-window / host GPU，每轮 boot 后请求 COLD 启动，5/15/30 s 均为未同意的协议页；最终游戏主 PID 为 2758、2243、2566。**三轮使用同一已安装 AVD，属于三次冷启动，未做三次全新安装。**

独立验证全部 12 张 ADB PNG 的签名、全部 chunk CRC、IEND、1280×720 尺寸及完整解压长度，文件字节数和 SHA 全部吻合。首次三图完全相同，SHA 为 `1359e8e9680a56b0fc28049708eb4c223510e5ab7fb30e495e9dcd95cb1b1215`；复测九图完全相同，SHA 为 `20746d1650aeb7b27133bef3fcba6a0565f18b8977e78a4bf6e86d016206b95e`。各图的精确路径在对应不可变 game.json 中，下表列代表文件；代表图也经过目视核对。

三份连续全缓冲 logcat 分别为 43,132、34,822、35,508 行；检索 Fatal signal、SIGSEGV、FATAL EXCEPTION、Force finishing 均无命中。libwebviewchromium 的命中只是公共库名单；每轮 GP7Worker / GP7Service 的死亡记录均邻接 Zygote exited cleanly (0)，不是主游戏进程崩溃证据。此有限观察不等同于证明整个客户端无缺陷，也不能解释首次失败。复测每轮 sync 停机返回 0、owned=false、端口全释放；logcat 收集器随 transport 结束返回 255，不能单独计作游戏故障。

### Maa Verge 实际版本、连接与取图

实际安装 Info.plist 为 0.1.3；重新计算打包 runtime 和 worker 的 SHA-256，分别为 `d67d5b3f4697b855c9df4872597be234f83e101d65ae8c64418864bfedd1db93`、`4d5f130876d925a30ee2164ac566970e90da220f968b63804e9da0b8e0cb5a1b`，与 selected-combination 回执一致。Core / resource manifest 分别含 10,086 / 10,094 个 installedFiles，版本、installedHash 和 clone 路径吻合；本节未重复重哈希两万份 payload，只复核已留存的全量完整性回执、manifest 和实际激活组合。只读查询本任务独立 SQLite 的 active/combo，与 wrapper 中的 v6.20.1 / mirrorchyan:2026-10-31 16:10:56.000、两份 installedHash 及路径逐项相同；没有读取个人数据库。

隔离 DB 的自动连接与应用/Core/资源更新均 false、originium/medicine/expedited 预算均 0，官方 ADB 路径及 emulator-22160 / General / maatouch / Bilibili 连接配置吻合。runs 对象数为 0，三份 RPC snapshot 的 runs 也均为空。selected-combination 中早期 device_connected=false 是准备时状态，不能覆盖后来的实际成功连接。

rpc.ndjson 恰有五次请求：app.snapshot、device.connect、app.snapshot、app.snapshot、device.frame；没有点击、滑动、控制或 run.start。保存的日常计划不代表已执行。唯一 frame 请求与响应时间差独立重算为 85.519 ms，产品 latencyMs=84；runtime.event/device.capture 报告 RawWithGzip、cost=73。Core 原始 asst.log 同时记录 RawByNc 308 ms、RawWithGzip 73 ms、Encode 98 ms，并选择 RawWithGzip；其后真实 Screencap callback ret=true、cost=73。这里 73 ms 是核心上报值，85.519 ms 是日志时间差，一帧不构成延迟分布。

MAA before.png 为 217,153 B、1280×720 RGB8，独立验证全部 CRC、完整 zlib / IEND 和 2,765,520 B 滤波扫描行。进一步将 MAA RGB 与 ADB RGBA 逐行解滤，ADB 的 921,600 个 alpha 全为 255，统一 RGB 后差异像素为 0，真实 RGB SHA-256 为 `5eba68ab94c03c5254582d442d47594989e71944b4461d7461b89fa8c8ec99a6`。wrapper 中名为 pixel_sha256 的字段仍是含 filter 字节扫描行的哈希，不能与此 RGB 摘要混用。有效图目视仍为协议页；canControl=true 仅为能力字段，Core 的 EmulatorFPS=60 来自 refresh period，也不是实际游戏帧率测试。GUI、游戏 OCR、输入控制与日常没有执行。

### 登录前包目录观察与实质阻塞

首个 case 0533d6c72a22 在 UID 解析前置条件阻塞：旧解析仅匹配 userId，而该系统输出 appId。raw 报告显示尚未扫描文件、请求游戏启动、执行 no-sync kill 或冷启动对比；不是游戏文件丢失或模拟器持久性失败。它的 cleanup 返回 0、errors=[]、owned=false、端口释放。

修正查询后，源实例刷新证明记录 user 0 的实际包 UID=10192，原 official_game sync 停机成功且端口全释放；新 APFS disposable clone e2a0a73b8aae 只在克隆上 root。包目录范围限 cache / files / shared_prefs、≤8 MiB、最多 128 份已有基线，排除敏感名称，未创建/覆盖/删除模拟游戏文件，也未导出内容或公开真实文件名。基线 eligible / hashed 均为 17，后续 eligible 为 18。

唯一抽中的 files 下新文件在关机前已经 0 B，hash 为标准空内容 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。公开 closure 摘要两次均为同 UID 1 进程、70 个 FD、0 个可写 mapping；另 3 次不可读/竞态候选被拒。核对代码使用 st_dev+inode、稳定 stat/hash 和同 UID FD / writable mmap 快照，拒绝不确定快照；原始公开记录不包含完整私有 FD/mmap 列表，因此本审计没有独立重放全部私有句柄判定。非原子快照无法给出 close syscall 时间、保证之后不重开，或辨别同 UID 客户端/SDK 中的具体创建者。

确实执行一次无 sync 的 emu kill，返回 0；最终宿主观测到 kill 调用开始为 3.880 ms，不能当作客体 close 延迟。冷启动复查前主游戏未运行，文件仍为 0 B、同 inode / mtime / 空哈希；随后 sync 清理成功。**这仅是空文件创建后仍存在的观察，不是非空 payload 持久性通过，也不是非空文件被截断为零。**60 s 是观察上限，提前抽中空文件后结束；实际采样窗没有仪器记录，整个 case 的 62.218620 s 含克隆、两次开机、基线与清理，不能写成完整 60 s 采样或穷尽所有非空写入。

raw `status=passed_in_observed_file`、`genuine_recent_game_write_confirmed=true` 原样保留，实质结论由 SHA 绑定的 interpretation 标为 blocked / nonempty_payload_candidates_tested=0。两份主报告均采用此边界。未来脚本已增加 scan 和 stable-hash 两处 size>0 门禁，离线验证记录与当前脚本 SHA 一致，未启动客体复跑；此修正没有追溯改变本次 raw 观察。

### 清理与结论范围

核对保存的 agent cleanup：专用 ADB PID 65866 的路径、22150 参数和启动身份匹配后停止，未碰默认 5037；22150 / 22160 / 22161 / 22162 全释放、任务进程为空。最终 root cleanup 进一步记录匹配 PID 8706 的 caffeinate 命令和起始时刻后发送 SIGTERM，后续进程不存在，最新 emulator 已 sync 停机、无残留任务进程；最终空闲 127,360,462,848 B，高于 20 GiB 保留线。本节核对这些既有清理记录，没有再次连接或启动 ADB。

两份报告准确区分安装/协议页/单次连接取帧、首次启动异常和登录后阻塞。原游戏实例已保留，登录、热更新、游戏内缓存/设置、OCR/控制、StartUp/Infrast/Award 和真实游戏 7200 s 长测仍需继续；本节未把 Launcher/Settings 的 400 次截图、SHM 帧率或空载两小时结果并入游戏通过结论。

### 登录前追加输入摘要

路径相对 `${WORKSPACE}`；以下为本次审计读取版本的完整 SHA-256，私有映射与任何账号内容不在公开输入表中。

| 输入 | SHA-256 |
|---|---|
| `reports/issue-28-draft.md` | `823ad8b5f190150c52b0eba3e37d0e3528e82e2747a77b2ee713f1492211aea1` |
| `reports/game-compatibility.md` | `3062b2b18f4c8399f01b8abc7bb938e6a4199447db5153315dabdbe23c107f71` |
| `metadata/game-apk.json` | `133852c2c5eca7ffa610d0b878b5ba76348318a3d247f834c22c7954077890b0` |
| `results/game/runs/1793511406217923000-825db5d4/game.json` | `d5cb05512b4e1786ac0ae39982b622aa5567f44055e7aac45bbd9820b5a37382` |
| `results/game/runs/1793511570345675000-94855282/game.json` | `4dd6e5e9baa4ee42c6346f084df40e6007a9116f10ccd96c509ce6cffee70a93` |
| `results/game/runs/1793511617907559000-e142eb1e/game.json` | `b75e62cb883533b6e2b321437f016485dd100853b83c043656d6d10b80326bab` |
| `results/game/runs/1793511664394977000-b055cb9e/game.json` | `d8c3a528aa624db5a604c4489d7b1630778de861ef10051e0c323c905e54564a` |
| `results/game/prelogin-retests.json` | `08638c9177ad1e4b3cf7e4c0f7c10e48df68b61650bed6838896d68433753b20` |
| `logs/1793511426929360000-game-install.stdout` | `781a31c82430c97897af612ece5e0ca2205e301cb5ff121ce2660541ea5182c7` |
| `logs/1793511442085564000-game-package-details.stdout` | `2c8ac98c6b26391d62467de46a23b7cc50142a0a7713acba566185aa1766fc73` |
| `logs/game-prelogin-retest-1-1793511583886516000-logcat-all.log` | `c0caaa45130627a7fddf9410dd2b76bd181cfeac7ccd3d1f11e37025cf3067ce` |
| `logs/game-prelogin-retest-2-1793511630014386000-logcat-all.log` | `5e0a1581beed561a8ef5a7e12f0d24524009bb10c3cff56bc36ad84a4fc93362` |
| `logs/game-prelogin-retest-3-1793511677888185000-logcat-all.log` | `40021adaf0ad059a5e69ae80552aae6f2810b5ea18abe27dcf8f4ab7210a5f16` |
| `results/game/runs/1793511406217923000-825db5d4/launch-5s-1793511448251263000-15612514.png` | `1359e8e9680a56b0fc28049708eb4c223510e5ab7fb30e495e9dcd95cb1b1215` |
| `results/game/runs/1793511570345675000-94855282/launch-5s-1793511590881007000-884be39b.png` | `20746d1650aeb7b27133bef3fcba6a0565f18b8977e78a4bf6e86d016206b95e` |
| `results/game/prelogin-maa-probe-1793511829426254000.json` | `40e9de927192995e92b0b6d2d3b5c25fc6168d400a5a2f40423f53a59652b7e7` |
| `maa/selected-combination.json` | `9ad6c665e4cfe5b490cbd762d992250487a5bbdd1332af15d4eb9e11b593d106` |
| `maa/sessions/20261101-134402-probe/initial-snapshot.json` | `70429d0b65f19c652d2cebf8b3e395914773dd1ad7972a47ae8a33636a25eef0` |
| `maa/sessions/20261101-134402-probe/connected-device.json` | `d42a5987fbbd3129d8e938a92633839b681a0bc2343ab44046f520b59d4b3045` |
| `maa/sessions/20261101-134402-probe/rpc.ndjson` | `8de017be85533cf9871ba9f5b915cb14478b19689e1a88f92b12874737d3b1c6` |
| `maa/sessions/20261101-134402-probe/before.png` | `0d3e7a6bac8a10e4c949f11944b8f7e782a2c579d0763c416cd7c267208d3afa` |
| `maa/sessions/20261101-134402-probe/before.png.json` | `6ec79bee3398fd558d886c49a3735748deadeb44d45167f6dc6cc007f7ed5cf9` |
| `maa/sessions/20261101-134402-probe/service.stderr.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `maa/data/native/workers/device-95e9c97e5c29/debug/asst.log` | `1d867dfdb02aec50fd46126903cdfe3a9c8e6e9e343915b5195ad35d7cdeb99c` |
| `maa/data/native/maaverge.sqlite3` | `00ad08dd91a68193db04fd8463d00367c1d30c86d7ad073c95f592cd2de94f15` |
| `maa/components/core/generations/core-50dd47da97964525a62ece40482672bf/manifest.json` | `2d4dfe1a640a1c067839030e2c39a80ad8f8c5fe325a300d6f1fa28d4eb0f2c3` |
| `maa/components/resource/generations/resource-9880ece1c9ec4fdfa9cbad5a1bda5d74/manifest.json` | `f54b72d07c79ef44088c2604f89ec82597728f8027620f8a496e53980995f1f9` |
| `results/game/prelogin-cache-source-refresh-1793512419201722000.json` | `b2c842da73fff98ac12a3519f634e72e279c5c54e8215975af1bcf352920d340` |
| `results/game-cache/0533d6c72a22.json` | `3a69eb2a64df32463b2bca37445c40fc9b815e1f80b4c8cdd16c60fffe5a47fc` |
| `results/game-cache/e2a0a73b8aae.json` | `794d91b126f915afdd97b6821e5bee445bb2d859a9d41b8d53810ca7afc3f08b` |
| `results/game-cache/e2a0a73b8aae-interpretation.json` | `6f7c45885295107cc49ed9152437123bbd214762981096070a3cdc6c9b67460a` |
| `results/game-cache/nonempty-candidate-offline-validation.json` | `4c7be548fba782b49427b5ee27078ec3014924e6766a1e763736b027776f6612` |
| `actual_game_cache_test.py` | `19b07538c9233adad0ad0dd7f0f6117e88d189444697d1eb0811aaea63bfef84` |
| `results/game/prelogin-final-cleanup-1793512775780222000.json` | `b1c696dff2190cf3f03410456c75c22d36bf387e85e1c617fffc1b04540e76b1` |
| `results/final-cleanup.json` | `737abaa21aa8af1138cc15bee10d699a1ebe6f03a0308e426559ceb579f3c6c1` |
