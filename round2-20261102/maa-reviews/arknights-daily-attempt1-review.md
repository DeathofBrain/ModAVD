# 第二轮明日方舟日常：attempt 1 失败复核

本次结果为 **StartUp 识别失败，日常任务未执行**。游戏在运行前后均停留于主界面；现有证据指向主页识别按钮与模板的尺寸不一致。不能把 `AllTasksCompleted` 或主界面动画变化计为日常成功。

本结论只针对 MaaCore **v6.20.1** 与当时固定资源。该轮设备连接、RawWithGzip 截图已经成功，失败发生在之后的游戏主页识别；它不证明 v6.21.x 存在同一问题。后续依用户要求改用新核心，本报告的旧版重试路径仅保留为当时的诊断建议，不再执行旧核心重试。

本复核只读取已有截图、日志及资源，并新增此报告；未使用 ADB、未操作游戏、未启动服务，未修改程序、任务资源或归档图片。截图由原生图像工具直接检查，未使用远程视觉模型。

## 运行与证据

- Session：`20261102-163833-run-daily`；run：`run-13e23e5534f8`。
- 核心：`v6.20.1`；资源：`mirrorchyan:2026-10-31 16:10:56.000`；Bilibili 客户端。
- 任务开始：2026-11-02 16:38:42.312；失败：16:40:20.701（上海时间）。
- 固定归档清单（本地来源未公开）
- 运行状态（本地来源未公开）、RPC 记录（本地来源未公开）、本次核心日志增量（本地来源未公开）
- 运行前截图（本地来源未公开）、运行后截图（本地来源未公开）；均为 1280×720。

归档清单为 `archived_for_review`，其 `actual_game_state_verified=false`，不会自动证明任务效果。本复核可确认两张截图的主界面状态，但无法据此证明收取了任何基建产物或任务奖励。原始截图可能包含账号信息，不应直接作为公开 issue 附件。

## 失败链与实际任务范围

核心日志第 441 行将 `StartAtHome`、`StartWithSanity`、`SwitchTheme@ToggleSettingsMenu`、`StartUpBegin` 列为首批识别候选；前几个图像候选未通过，执行了 `JustReturn` 的 `StartUpBegin`。随后持续在启动候选中重试。

| 上海时间 | 证据 | 含义 |
| --- | --- | --- |
| 16:40:17.864 | `cur_retry=50`，`retry_times=50` | 启动识别耗尽重试 |
| 16:40:19.754 | `SubTaskError`，ProcessTask | 子任务失败 |
| 16:40:19.774 | `TaskChainError`，StartUp | 启动任务链失败 |
| 16:40:19.774 | `AllTasksCompleted`，`finished_tasks=[1]` | 队列结束，不能覆盖前一条失败 |
| 16:40:20.701 | `run.failed` | runtime 正确阻止后续任务 |

运行详情中 StartUp 为 `failed`，Infrast 与 Award 均为 `waiting`。实际只追加并启动了 StartUp，没有执行基建收取、换班或每日／每周奖励收取。

`initial-snapshot.json` 是保存新计划前的旧状态，仍显示 Infrast `mode=0`；本次实际请求以 RPC 请求 ID 12 的 `plan.save` 为准：Infrast `mode=10000`，使用空制造／交易房间的 `collect-only-infrast.json`，仅 Mfg / Trade，禁止补货、训练和线索操作。Award 只启用 `award=true`。付费石头、理智药、加急许可预算均为 0。旧快照不能用来推断本次执行了默认换班。

预先清理每日补给等弹窗时已领取的 10 份基础作战记录属于人工准备，不归功于本次 MAA。

## 识别资源核对

当前 resource generation 与 core 附带资源的以下文件 SHA256 相同，排除了这几项资源在两个目录间不一致：

| 文件 | SHA256 |
| --- | --- |
| `tasks/tasks.json` | `97575f4f49613ad61ab1fe7dac4b72575aa64aab0998d0f2b1568c897d9714c2` |
| `tasks/UiTheme/SwitchTheme.json` | `5f8b5567a4bd70f909fee338351371951a3e454cdf04f4e2ce5f48a9b853f04c` |
| `template/UiTheme/SwitchTheme/SwitchTheme@ToggleSettingsMenu.png` | `ea2816b96ff11be8c0b08bb266a94402ac58bc18559a8a7258303605de2521cf` |
| `template/WakeUp/StartWithSanity.png` | `db5c7a603e6c8d2adcc1ce9bc92f68223e18d24c5ee1a4c64f04b43a1dac501d` |

本地 tasks 定义（本地来源未公开） 中，`StartAtHome` 显式继承 `Home`。`Home`、`StartAtHome`、`StartUp` 都使用 `SwitchTheme@ToggleSettingsMenu.png`；`Home` 的 ROI 为 `[227,327,159,152]`，StartAtHome 继承该 ROI。主题切换入口也使用同一模板及 ROI。因此主题切换的自动回退同样会受这个识别问题影响。

模板为 **29×24 灰度图**，内容是助理切换按钮的双箭头，而非左上角的齿轮。ROI 覆盖当前截图中该按钮；日志始终在 `[286,387,29,24]` 得到最高匹配，位置正确。全部 53 次日志分数为 **0.686563–0.693925**，均未形成成功匹配事件。

上述任务没有覆盖 `templThreshold`。对应版本上游 [AsstTypes.h 第 31 行](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.20.1/src/MaaCore/Common/AsstTypes.h#L31) 将默认阈值设为 **0.8**；[TaskData.cpp](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/v6.20.1/src/MaaCore/Config/TaskData.cpp#L592) 在未显式设置阈值时继承默认值。因此本次约 0.69 的结果不足以通过门禁。

`StartWithSanity` 是另一条专用图像入口：ROI `[700,130,140,120]`、`maskRange=[100,255]`，使用 **117×81** 的 `StartWithSanity.png` 理智图标模板，成功后转入 `CloseHomeSanity`，再回 Home。本次没有它成功识别／执行的回调。不能把理智数字的 OCR 命中等同于该模板成功，也不能仅凭任务名称断定它必须识别当前普通首页。截图中无明显遮挡弹窗。

后续基建、任务入口资源确实列有多种主题模板，但 StartUp 的上述主页检测只依赖共同的双箭头模板。当前截图为白色卡片式主页，截图不足以证明游戏设置中具体选了哪个主题。

## 尺寸差异的离线验证

为区分“目标不存在”与“目标尺寸不匹配”，使用归档 PNG 做只读离线计算：Pillow 转灰度，在原 ROI 内按均值归一化相关系数搜索；原图与模板只加载到内存，未保存或替换任何图片。该实现不是运行中的 OpenCV，但原尺寸结果与核心日志吻合。

| 归档截图 | 原模板 29×24 | 模板缩为 26×22（约 90%） | 缩小后最高点 |
| --- | --- | --- | --- |
| before.png | 0.693310 | **0.966824** | `[287,388,26,22]` |
| after.png | 0.693919 | **0.966843** | `[287,388,26,22]` |

同一搜索中 80%、110%、120%、130% 尺寸均不及 90%。两张截图背景、语音和横幅发生变化，但原尺寸与缩小尺寸的结果分别稳定，较支持“当前按钮比资源模板小约一成”。原始 ROI 已覆盖正确目标，因此仅扩大 ROI 或延长等待缺少依据。

**已证实的是尺寸不一致；尚未核对游戏内 UI 缩放、显示适配或主题设置，不能声称已确认其中哪项是根因。** 该离线高分只用于定位问题，不是 StartUp 成功，也不能计入日常任务验收。

## 最小有效重试路径

1. 保留 attempt 1 归档。检查游戏内界面／主界面缩放与显示适配；若发现缩放偏离默认值，恢复默认／100%，退出设置回到无弹窗主页。若缩放已经默认，则再检查默认浅色主题。一次只改变一个游戏设置，并记录改变前后截图与值。
2. 在新截图上先用**未缩放、未修改的原始模板**核对目标尺寸。若它仍低于原始 0.8 门槛，继续诊断或使用经过验证的新资源版本，不应将其视为可重试成功条件。
3. 设置与图像条件核实后，以独立 attempt 2 目录／检查点重跑原零预算日常计划，保留 StartUp 与原始成功证据校验。不要跳过 StartUp、降低阈值、把识别改为 JustReturn，或直接追加后续任务以制造成功。
4. StartUp 必须出现有效任务链完成证据；Infrast 与 Award 还须分别由任务回调、前后游戏画面及实际收取状态验证。若仍停在主页循环，应及时归档本次失败，而非等待整个日常计划被误判完成。

本复核未执行这条重试路径。若只是恢复游戏显示设置即可使原模板通过，这是最小且可与 attempt 1 对照的重试；若必须修改 MAA 资源或更新核心，需将其作为新的测试条件记录版本与摘要。
