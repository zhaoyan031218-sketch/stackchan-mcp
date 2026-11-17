# Windows：更新图片回传固件

这次更新改变机器人向 Railway 发送图片/录音的方式：使用小分片和每帧随机
WebSocket 掩码，并增加回传进度日志。现有 Railway 协议已兼容；真实照片能否
通过仍需烧录后现场验证。分析与测试记录见
[相机回传排查](camera-relay-troubleshooting-2026-11-17.md)。

## 保留现有工程配置

将修正版本 ZIP 解压到 D 盘的一个新文件夹。保留原有工程：

```text
D:\Stackchan\stackchan-mcp-codex-railway-deployment
```

先备份原工程中将要替换的源码，再将新版本的以下三个文件复制到原工程的
相同位置。这里只更新源码；私密配置 `firmware/src/config.h`、`.venv`、表情
资源及 PlatformIO 依赖缓存继续使用原来的文件。

```text
firmware/src/outbound_service.cpp
firmware/src/outbound_ws_writer.h       （新增）
firmware/src/http_server.cpp
```

## 编译

在原工程目录的 PowerShell 中运行。不要在未配置 Wi-Fi/令牌的新解压目录中
构建并烧录。以下检查只确认私密配置存在，不显示其内容：

```powershell
Set-Location "D:\Stackchan\stackchan-mcp-codex-railway-deployment"
if (!(Test-Path -LiteralPath ".\firmware\src\config.h")) { throw "Private config.h is missing; stop here." }
$env:PLATFORMIO_CORE_DIR = "D:\Stackchan\.platformio"
$env:TEMP = "D:\Stackchan\tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
.\firmware\.venv\Scripts\pio.exe run -d firmware -e m5stack-cores3
```

看到 `SUCCESS` 后才继续上传。云端用于编译验证的 `m5stack-cores3-public`
不包含用户的联网配置，不能作为这台机器人的上传目标。

## 上传与测试

先在串口监视器按 `Ctrl+C`，释放 COM7，然后上传：

```powershell
.\firmware\.venv\Scripts\pio.exe run -d firmware -e m5stack-cores3 -t upload --upload-port COM7
```

上传成功后重新打开监视器：

```powershell
.\firmware\.venv\Scripts\pio.exe device monitor --port COM7 --baud 115200
```

设备连回云端后，在 Kelivo 只调用一次 `stackchan_see`，保留完整工具结果与
本次新增的 `[CAM]`、`[HTTP]`、`[OUTBOUND]` 日志。新日志应包含：

```text
[OUTBOUND] Channel connected
[HTTP] GET /snapshot -> ... bytes JPEG reply=prepared
[OUTBOUND] Binary reply start bytes=... heap=... psram=...
[OUTBOUND] Binary reply sent bytes=.../... frames=... elapsed_ms=...
```

二进制长度包含 36 字节协议头；例如 JPEG 为 7,747 字节时，发送长度为 7,783。
`sent` 表示本地 TLS 写入已完成，实际成功仍以 Kelivo 收到图片为准。
若显示 `write_failed` 或 `expired`，发送会中止并关闭连接，不会自动重拍。

需要回退时，恢复备份的源码并用原私密配置重新编译上传。
