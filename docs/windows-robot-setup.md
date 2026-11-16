# Windows：配置 CoreS3 连接云端

适用：已确认的 M5Stack CoreS3，云端已部署本仓库的 Railway 服务，原固件已备份。
USB 用于烧录；机器人日常通过 **2.4 GHz Wi-Fi** 连接云端。

## 1. 准备源码和 Python

下载包含 `scripts/configure_robot.py` 的仓库版本并解压。在包含 `firmware`、
`scripts` 的文件夹中打开 PowerShell。安装 Python 3.11 或更新版本后确认：

```powershell
py --version
```

如果已有源码和私密 `firmware/src/config.h`，先保留它。配置助手不会覆盖已有文件。
需要新配置时，将旧文件保存到安全位置，或在另外解压的干净目录中操作。

## 2. 在本机生成私密配置

将以下域名替换为自己的 Railway 域名，不带协议或路径：

```powershell
py scripts\configure_robot.py --host YOUR_DOMAIN.up.railway.app
```

助手通过 Windows TLS 证书链验证取得该站点实际使用的根 CA。证书过期、域名
不匹配或证书链不受信任时会停止，不会关闭验证或生成半成品配置。它读取本地
PowerShell 辅助代码执行，不修改系统执行策略。无需手动寻找或粘贴证书。

验证成功后按提示输入：

1. 2.4 GHz Wi-Fi 名称（保留大小写和空格）。
2. Wi-Fi 密码，输入时不显示；只有开放网络才能留空。
3. Railway Variables 中的 `STACKCHAN_RELAY_DEVICE_TOKEN`，输入时不显示。
   **不要使用 MCP token 或内部 relay control token。**

结果写入被 Git 忽略的 `firmware/src/config.h`，并启用出站连接、443 端口和
TLS 证书验证。Wi-Fi 密码和设备 token 不进入 shell 历史，也不会打印到终端。
该文件及编译后的固件包含设备凭据，只保存在自己的电脑上，不提交或公开分享。

Windows 信任库中的企业 HTTPS 检查 CA 只适用于相同受管网络。若电脑有 HTTPS
拦截而机器人没有，需在正常网络中重新验证并生成配置，不要关闭机器人证书校验。

## 3. 安装独立固件工具并烧录

固件工具与 Python MCP 依赖保持分离：

```powershell
py -m venv firmware\.venv
.\firmware\.venv\Scripts\python.exe -m pip install platformio==6.1.18
```

关闭占用串口的串口监视器或烧录工具。以下以 `COM7` 为例；端口变化时以
Windows 设备管理器的当前编号为准。放稳机器人，为舵机留出活动空间。

```powershell
.\firmware\.venv\Scripts\pio.exe run -d firmware -e m5stack-cores3 -t upload --upload-port COM7
```

首次运行会下载编译器和依赖，耗时较长。这个命令会编译并写入固件，没有执行
整片 Flash 擦除，也不需要 `uploadfs`。必须使用 `m5stack-cores3`，不能使用
忽略私密配置的 `m5stack-cores3-public`。

若无法自动进入下载模式，先检查端口和 USB 数据线，并按 CoreS3 官方说明
进入下载模式。Windows 可能分配新的 COM 编号；确认后重试，不要盲目擦除 Flash。

## 4. 验证机器人连接

```powershell
.\firmware\.venv\Scripts\pio.exe device monitor --port COM7 --baud 115200
```

固件会优先尝试 NVS 里保存的 Wi-Fi，然后尝试新配置中的备用网络。
联网后需要 NTP 时间同步才能验证服务器证书并建立 WSS。连接路径固定为
`wss://YOUR_DOMAIN.up.railway.app/stackchan/ws`。串口若显示
`[OUTBOUND] Disabled`，检查配置是否生成以及是否误用了 public 构建目标。

在 Kelivo 中调用 `stackchan_face`，参数 `expression=happy`，检查屏幕表情。
此操作不依赖 TTS 密钥。确认后再逐项验证摄像头、动作和语音；不要用读取
`/audio` 的方式做健康检查，因为它会消耗录音缓冲。

云端健康接口成功和 MCP 能列出工具，仅代表服务器在线，不代表机器人已连接。
若工具报设备离线，检查设备 Wi-Fi/NTP、域名、CA 和设备 token，保留错误日志
排查，不要将 Wi-Fi 密码、token 或私密配置截图发到公开渠道。
