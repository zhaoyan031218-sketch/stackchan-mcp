# CoreS3 预编译恢复包：recovery-20261118.3

适用：M5Stack CoreS3 Stack-chan、Windows 电脑、Railway + Kelivo。
默认服务：`gojobody.up.railway.app`。

这次交付预编译的 USB 配置固件。电脑不需要下载 PlatformIO、ESP32 编译器或重新
编译源码；Wi-Fi、设备 token 和已验证的 TLS 根证书经 USB 写入机器人。
固件包内没有你的密码或 API Key。需要 Windows 上安装 Python 3.11 或更新版本，
首次运行仍需联网安装体积较小的 esptool / pyserial 烧录依赖。

## 已经烧录上一版：保留配置升级

上一版实机日志已确认：49,188 字节的 PCM 帧仅收到 13,900 字节，便触发
2 秒接收空闲超时，固件主动断开；当时命令还剩约 12.8 秒有效时间。
本版将待收音频的空闲等待调整为 8 秒，并在接收过程中持续检查整条命令的
20 秒上限和服务器截止时间。已保留上一版的传感器 ACK 修复。
**软件回归不能代替实机验收；刷完后仍需测试一次短句。**

完整解压 `cores3-recovery-20261118.3.zip`，在新解压目录打开终端：

```powershell
.\START-SETUP.cmd --flash-only --port COM7
```

`COM7` 是目前确认的机器人端口。它只更新固件，保留上一版已经保存的 Wi-Fi 和
设备 token，不获取证书或重新要求填写凭据。完成后开诊断监视器：

```powershell
.\.venv\Scripts\python.exe scripts\setup_prebuilt.py --monitor --port COM7
```

监视器默认运行 90 秒，不发送配置或复位命令。启动后在 Kelivo 只调用一次
`stackchan_say`，说一个短句，例如“你好”。如果失败，复制监视器里的
`[OUTBOUND]`、`[PCM]` 或重启日志用于继续定位。串口重启后可能需要换成新的 COM 号。

`stackchan_sense` 读取的是可选外接温湿度／气压模块。未检测到模块时应该报告
无传感器数据；总线失败或模块读取失败应单独处理，不能由工具凭空生成温湿度。
传感器误检测已通过回归复现并修正，但是否接了模块、接线或模块型号是否匹配，
仍需要实机确认。服务器端的详细错误显示改进保留在恢复分支，需部署后才会生效；
固件 ACK 检测修复不依赖这项显示改进。

## 首次使用方法

1. 下载独立的 `cores3-recovery-20261118.3.zip`，完整解压到新文件夹。
   不要在 ZIP 里面直接双击脚本，也不要把旧工程覆盖进去。
2. 用支持数据的 USB 线连接 CoreS3，关闭已有串口监视器。
3. 双击解压目录的 `START-SETUP.cmd`。它安装固定版本的 USB 工具、校验包内文件，
   然后让你选择设备的 COM 端口。不要猜端口号。
4. 按提示输入 2.4 GHz Wi-Fi 名称、密码，以及 Railway Variables 里的
   **`STACKCHAN_RELAY_DEVICE_TOKEN`**。输入隐藏是正常的。
   这是设备 token，不是 Kelivo 的 `STACKCHAN_MCP_AUTH_TOKEN`，也不是内部的
   `STACKCHAN_RELAY_TOKEN`。不要把这些值发到聊天。
5. 助手先通过 Windows 信任链验证 Railway 证书，再烧录四个已校验镜像并经 USB
   配置。看到设备明确确认配置保存后，它会自动重启；这一步不等于云端已验收。

如果烧录已成功，只是 USB 配置失败，在解压目录的终端重试：

```powershell
.\START-SETUP.cmd --configure-only --port COM7
```

把 `COM7` 替换为实际端口。USB 重启后端口可能变化，先在设备管理器确认。
如果自动复位未完成，按提示短按设备 Reset；不要整片擦除 Flash。
重新配置会替换这一版的 Wi-Fi/云端设置，其他 NVS 内容保留。

## 验收顺序

设备屏幕应退出 USB 配置页并连接 Wi-Fi。在 Kelivo 中按顺序调用：

1. `stackchan_status`：先确认设备在线。
2. `stackchan_face`，`expression=happy`：确认设备能执行命令。
3. `stackchan_see`：只拍一次，确认工具实际返回图片，再检查模型能否理解图片。
4. `stackchan_say`，`text=你好，我回来了。`：确认清晰发声。
5. 检查麦克风录音。若处于出门模式，麦克风会按原设置保持关闭，需要先切回正常模式。

MCP 能连上不代表机器人已上线。若状态仍离线，检查同一服务的域名、设备 token、
WSS 路由 `/stackchan/ws`，并确认 Railway 为单副本、单区域、单 worker。
若能返回图片但模型说看不到，检查 Kelivo 是否将 MCP 图片内容交给支持视觉的模型。

## 这一版修复了什么

- 音频接收不再因短暂超过 2 秒的停顿提前断开；只在收到字节时刷新 8 秒空闲计时，
  同时保留整条命令的截止时间，不自动重放失败命令。
- 保留 Railway 部署适配与 MiniMax MP3 转 24 kHz 单声道 s16le PCM 修复。
- 保留照片/录音通过 WebSocket 分片回传的修复。
- 摄像头时钟配置对齐 CoreS3，避免误占 GPIO 2 外部 I2C 引脚。
- 麦克风等待异步采样完成再处理，避免读取半帧或重复提交。
- 摄像头或流式音频占用结束后继续处理排队语音，避免后续说话卡住。
- 预编译固件使用本次 USB 配置的 Wi-Fi，不再优先尝试旧版配网页保存的网络。
- USB 输入、连接等待、烧录与下载有边界和超时；文件完整生成、校验后才提供。

MiniMax 的音频转码在 Railway 服务端完成。刷机器人不会改变线上部署。
用户提供的提交标题 `fix(tts): decode MiniMax mp3 to explicit speaker pcm`
对应 `9f4cad9`，本恢复分支继承该提交及其后续修复。

## 旧文件与验证边界

垃圾桶先保留即可，不用彻底删除。删除电脑文件不会清除机器人 NVS 或 Railway
服务。这个流程不会整片擦除 Flash；可以保留旧配置备份和回退材料。

详细检查结果见 `VALIDATION.zh-CN.md`。软件测试与编译不能代替真实 USB、摄像头、
麦克风、扬声器或 Kelivo 验收。本次按要求以 GitHub 源码为准，没有访问 Railway 做生产验收，
也没有宣称已在你的设备上完成端到端验证。

旧的本地源码编译流程仍保留于 `docs/windows-robot-setup.md`，仅供开发维护。
本次预编译包请使用包内的 `START-SETUP.cmd`，不需要运行 `rebuild_robot.py`。
