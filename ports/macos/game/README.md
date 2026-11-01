# 明日方舟 B 服官方包来源核查

官方来源核查使用[哔哩哔哩官方游戏中心](https://www.biligame.com/detail/?id=101772)及其网页实际调用的官方 API。无账号、Cookie、浏览器资料或登录凭据参与。用户随后提供了本机 APK，静态检查结果单独保存在 `apk-inspection.json`；不改写原先匿名请求的证据快照。

```sh
python3 ports/macos/game/inspect.py
```

脚本将完整官方 JSON、CDN HEAD 状态和响应体 SHA-256 保存在 `.macos-work/game/source.json`，同时保留带时间戳的历史快照。元数据响应限制为 2 MiB，写入前检查至少保留 20 GiB。此脚本只核查来源，不启动 ADB、模拟器或安装流程。

2026-10-31 匿名请求阶段结果：

| 项目 | 官方元数据 / 实际检查 |
| --- | --- |
| 游戏条目 | 明日方舟，game_base_id `101772` |
| 渠道包名 | `com.hypergryph.arknights.bilibili` |
| 当前版本 | `2.7.81`，页面更新时间 `2026-10-09 06:05:00` |
| 包大小 | `1886746222` 字节，约 1.76 GiB |
| 官方直链 | `https://pkg.biligame.com/games/mrfz_2.7.81_20260918_112651_8bff8.apk` |
| 官方备用 CDN | `https://pkgdl.biligame.net/games/mrfz_2.7.81_20260918_112651_8bff8.apk` |
| 直链检查 | 两个 HEAD 均 HTTP 403，`X-Error-Info: auth_remote`；主 CDN 的小范围 GET 也为 403 |
| 官方加密地址 API | `/game/download/get_crypto_url?game_base_id=101772` 返回 `code=-101`，`用户尚未登录` |
| APK / ARM64 验证 | 此阶段未取到 APK；后续用户提供本机文件的验证结果见下文 |

API 的 `android_pkg_ver=200` 仅按字段原样记录；取得 APK 前不将它当作已核验的 manifest versionCode。网页 API 路由来自官方页面加载的 `https://s1.hdslb.com/bfs/prod-frontend/game.gamesdk-web.gamecenter-pc/static/index.77417068.js`。

匿名下载入口要求官方用户操作。工具不会绕过官方登录、替用户登录或改用第三方镜像；也没有将游戏服务、隐私条款记为已接受。官方隐私政策入口为 <https://user.hypergryph.com/protocol/ak/privacy>。

本机 APK 可重复执行只读静态检查，不复制或解压整包，不调用 ADB：

```sh
python3 ports/macos/game/inspect.py local \
  --apk /Users/azuki/Downloads/mrfz_2.7.81_20260918_112651_8bff8.apk
```

`local` 使用既有 owned 游戏缓存。manifest 复用 Manager 的边界受检二进制 XML 解析器，在临时副本中增加 `android:versionName` 字段，使用与 Manager 构建相同的 Go `1.26.5`。仅复制两个小 Go 源文件，退出时删除临时目录；不修改 Manager 源码、导出补丁或产物收据。ZIP 成员流式读至 EOF 校验 CRC，逐枚原生库记录 SHA-256 和 ELF 头，检查期间再次核对 APK 大小、inode 和时间戳未改变。

2026-10-31 用户提供文件的实际结果：大小 `1886746222` 字节；SHA-256 为 `9106db6d9038d11a732cdc35773e68d79895834283718895c6f929d496577df9`。真实 manifest 包名 `com.hypergryph.arknights.bilibili`，versionName `2.7.81`、versionCode `200`。4305 个 ZIP 条目 CRC 通过，没有重复名或不安全路径。`arm64-v8a` 的 31 枚库为 64 位小端 AArch64 ELF 动态库，另有 30 枚 `armeabi-v7a` 库；全部原生库均与 ABI 目录匹配。文件名、大小、包名和版本与先前官方 API 快照一致。官方快照未公布整包 SHA-256，本检查没有完成 APK 密码学签名验证，也不代表已通过实际安装或游戏运行验收。

若官方公开入口恢复下载，可参照 `ports/macos/runtime/runtime.py` 的 Range 续传与每 MiB 空间停线方式缓存，必须核对 206 Content-Range / 文件总大小，并保留中断文件；当前工具没有实现 APK 下载。
