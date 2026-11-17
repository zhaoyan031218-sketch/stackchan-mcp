# CoreS3 从干净目录重建：recovery-20261117.1

适用：M5Stack CoreS3 Stack-chan，Railway + Kelivo，Windows 电脑负责 USB 烧录。

本包是可以重新生成专属烧录文件的完整源码包。Wi-Fi、设备 token 和 TLS
证书需要在你的电脑上配置，所以包内没有一个可以直接刷入、就能连上你的云端的
通用 `.bin`。配置后会自动编译出你的私密 `firmware.bin`。

## 先处理当前的离线

当前反馈是：开机后一直离线，Kelivo 返回：

```text
Device request failed: Relay HTTP 503: Device outbound channel is offline
```

这表示处理这次请求的云端中继没有在线设备连接。MCP 能连接、能列出工具，
并不表示机器人已连接；此时拍照和说话都会受阻。暂时没有证据判定摄像头损坏，
也没有证据证明只要重刷就能解决全部问题。

垃圾桶先保留，不需要彻底删除。删除电脑文件不会清除机器人 Flash/NVS，也不会
删除 Railway 部署。新建目录即可摆脱旧编译产物。旧 `firmware/src/config.h`、
自定义表情和旧固件仍可用作备份。此流程不整片擦除 Flash；NVS 中旧 Wi-Fi
仍优先尝试，串口会显示实际连上的网络。

## 1. 解压到新目录

例如 `D:\Stackchan\recovery-20261117`，不要覆盖旧工程。在解压后同时包含
`firmware` 和 `scripts` 的目录打开 PowerShell。

需要 Python 3.11 或更新版本，先运行 `py --version` 确认。首次编译需要联网下载
PlatformIO、编译器和固定版本依赖；下载失败要处理报错，不能继续烧录旧文件。
若编译器下载持续为 0 字节或报 `10053`，参照
[Windows 编译器下载恢复](docs/windows-toolchain-download-troubleshooting-2026-11-18.md)，
可用已校验离线包本地安装，或使用带读取超时的 Core 安装助手，继续使用现有配置。

## 2. 配置并编译

```powershell
py scripts\rebuild_robot.py --host YOUR_DOMAIN.up.railway.app
```

将 `YOUR_DOMAIN.up.railway.app` 替换为自己的 Railway 域名，不带 `https://`
或 `/mcp`。助手会验证服务器证书，然后依次让你输入：

1. 2.4 GHz Wi-Fi 名称。
2. Wi-Fi 密码（输入时隐藏）。
3. Railway Variables 中的 `STACKCHAN_RELAY_DEVICE_TOKEN`（输入时隐藏）。

三个 token 的用途不要混淆：

| 名称 | 用途 |
| --- | --- |
| `STACKCHAN_MCP_AUTH_TOKEN` | Kelivo 连接 `/mcp` |
| `STACKCHAN_RELAY_DEVICE_TOKEN` | **本次机器人配置要输入的值** |
| `STACKCHAN_RELAY_TOKEN` | Railway 内部控制，不填进机器人 |

设备 token 必须与当前 Railway 服务中的值完全一致。不要发到聊天或公开仓库。
配置保存于 `firmware/src/config.h`。已有配置时助手不会覆盖；如果之前配置成功、
只是下载/编译失败，重试时去掉 `--host`：

```powershell
py scripts\rebuild_robot.py
```

编译目标固定为 `m5stack-cores3-railway`。没有私密配置、未启用出站连接、缺少
主机/设备 token/证书或端口不是 443 时会拒绝编译。此检查不能证明 token 被
Railway 接受，最终仍以在线状态为准。

成功后生成：

```text
firmware/.pio/build/m5stack-cores3-railway/firmware.bin
```

该文件含设备凭据，只在自己的电脑上保存。它是应用镜像，不能把它当作整片
合并镜像随意写到地址 0；使用下一步助手，由 PlatformIO 处理镜像和地址。

## 3. 确认串口并烧录

先插好支持数据的 USB 线，关闭其他串口监视器，再列出当前串口：

```powershell
py scripts\rebuild_robot.py --list-ports
```

确认 CoreS3 的端口。下面的 `COM7` 只是示例，必须换成当前编号：

```powershell
py scripts\rebuild_robot.py --upload --port COM7
```

只有编译成功才会上传；编译失败不会上传残留的旧 `.bin`。烧录会重启设备，
放稳机器人并给舵机留出空间。此流程没有 `erase_flash` 或 `uploadfs`。

## 4. 看启动日志，先验收连接

```powershell
py scripts\rebuild_robot.py --monitor --port COM7
```

USB 重新枚举时串口可能变化，请再次列出端口。如果打开监视器后没有启动信息，
按一次设备复位，再看从开机开始的日志。

| 日志 | 含义与下一步 |
| --- | --- |
| `[BOOT] Stack-chan recovery-20261117.1` | 确认实际运行本次版本 |
| `[BOOT] Railway configuration required by this build` | 使用了专用 Railway 目标 |
| `[WIFI] All networks failed` | 检查 2.4 GHz Wi-Fi、密码和配网 |
| `[OUTBOUND] Disabled ...` | 设备配置/证书无效，或没有刷入正确固件 |
| `[OUTBOUND] Waiting for NTP time; TLS has not started` | 时间未同步，TLS 尚未开始；检查网络能否访问 NTP，可用手机热点对照 |
| `[OUTBOUND] WSS not connected; check host, network, CA and DEVICE token` | 检查域名、443 访问、证书、设备 token，以及 Railway `/stackchan/ws` 路由 |
| `[OUTBOUND] Channel connected` | 已建立 WebSocket；仍需用云端状态确认连接持续可用 |

连接后在 Kelivo 按顺序调用：

1. `stackchan_status`，应返回 `online=True`。
2. `stackchan_face`，`expression=happy`，确认屏幕有反应。
3. `stackchan_see`，先只调用一次，确认收到图像。
4. `stackchan_say`，`text=你好，我回来了。`，确认听到清晰声音。

如果日志显示不断连接/断开，或显示连接但 Kelivo 仍离线，请检查 Railway 是
**单副本、单区域、单 worker**，以及 Kelivo 与固件指向同一个域名和服务。
服务端连接状态保存在进程内，多副本可能让请求落到没有设备连接的实例。

## 图片与声音的修正

本包基于已有修正版本 `1093695f8fa094c5e4fb9c1b35182b598111fb16`，
保留图片/录音回传的 WebSocket 分片修正。新增启动版本标识与连接等待日志，
帮助定位本次“开机一直离线”。没有改动摄像头引脚或音频硬件参数。

MiniMax 的 MP3 → 24 kHz、单声道、16 位 PCM 转换在 **Railway 服务端**。
源代码已包含该修正，但烧录机器人不会更新 Railway。设备在线后若仍无声，
需要查看那一次 `stackchan_say` 的完整工具结果，并核对当前 Railway 的部署
版本、TTS 引擎、音色 ID 和 API 配置。本文不假设当前线上已部署哪个版本。

如果 Kelivo 的模型不支持图像，或客户端没有把 MCP 图片内容送给模型，设备
拍照成功后仍可能出现“无法看图”；这要在设备已在线、图片已回传后再检查。

## 验证范围

本包的自动化验证结果见随包 `VALIDATION.zh-CN.md`。离线测试和编译不能证明
真实摄像头、ESP32 TLS/Wi-Fi、扬声器或 Kelivo 的端到端效果。本次未连接你的
USB 设备，未刷机，未更改 Railway 部署或密钥。云环境访问该 Railway 域名
被其联网白名单拦截，因此没有以本机 HTTP 检查证明线上服务是否正常。

仍离线时，保留从 `[BOOT]` 到最后一条 `[OUTBOUND]` 的新日志；发来前去掉
Wi-Fi 名称等不想分享的信息，不需要提供密码、token 或整个私密 `config.h`。
