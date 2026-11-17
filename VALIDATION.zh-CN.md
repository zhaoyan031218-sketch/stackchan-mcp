# recovery-20261118.1 验证记录

基线：`49664df`（`codex/cores3-recovery-20261117`），继承 MiniMax 修复 `9f4cad9`、
Railway 适配、单次拍照和 WebSocket 照片/录音分片回传修复。
新增 runtime USB 配置、摄像头引脚修正、异步麦克风与播放队列修复。

## 已通过

- Python Ruff 静态检查：通过。
- Pyright 使用项目 Python 环境：0 errors、0 warnings。
- 完整 Python 测试：675 passed；11 项依赖弃用警告。
- PlatformIO native 固件测试：119/119，通过。
- `pio run -e m5stack-cores3-runtime`：编译成功，ESP32-S3 app 镜像 1,480,496 字节。
- `pio check -e m5stack-cores3-runtime --severity=high --fail-on-defect=high`：No defects found。
- 仓库安全审计与 `git diff --check`：通过。
- 四个镜像的目标、分区地址、尺寸与 SHA-256 校验；ZIP CRC；解压后配置助手
  `verify_package()` 再次验证：通过。
- 独立代码复查覆盖 USB 小缓冲区、分片接收、有限等待、旧配置保护与串口秘密泄露。

## 下载包

文件：`releases/cores3-recovery-20261118.1.zip`，**955,453 字节**。

SHA-256：

```text
ac9b64eef74a538049719444dfc3f5952faf5fe1966edc85a0692da5f52552d1
```

包内有四个已编译镜像及 Windows USB 配置助手，不含 Wi-Fi 密码、设备 token 或
API Key。目标为 `m5stack-cores3-runtime`，并非禁止云端功能的 public 检查镜像。
无需下载 ESP32 编译器；首次运行仍需 Python 和小型 USB 烧录依赖。

## 构建依赖

PlatformIO 6.1.18；espressif32 7.0.0；Arduino ESP32 2.0.17；esptool 4.11.0；
M5Unified 0.2.15；M5GFX 固定提交 `5268353`；StackChan-BSP 固定提交 `0addef4`；
ArduinoJson 7.4.3；AnimatedGIF 2.2.3；WebSockets 2.7.2。
PlatformIO 会同时缓存 M5Unified 约束解析出的 M5GFX 0.3.0，实际生成的 C++ 依赖
文件确认使用显式固定提交的 M5GFX 目录，而非该缓存副本。

## 尚需实机验收

没有连接用户的 USB 设备；未烧录、拍照、录音或让扬声器实际发声。
Windows 串口重枚举、真实 NVS 写入及断电恢复、Wi-Fi/TLS、摄像头清晰度和音频
时序仍需在设备上验收。自动测试不证明这些硬件行为。

按用户要求以 GitHub 项目为准，未访问 Railway 做生产检查。
Kelivo 的模型需要支持图片并收到 MCP ImageContent，才能理解机器人拍到的画面。
验收顺序与恢复步骤见 `START-HERE.zh-CN.md`。当前提交保存于独立恢复分支，
不通过本次操作修改原有部署分支。
