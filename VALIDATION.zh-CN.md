# recovery-20261117.1 验证记录

基线：`1093695f8fa094c5e4fb9c1b35182b598111fb16`。
在独立云端工作副本完成检查，原工作区源码保留。

| 检查 | 结果 |
| --- | --- |
| `uv run --locked ruff check .` | 通过 |
| `uv run --locked pyright` | 0 错误、0 警告 |
| `uv run --locked pytest --cov=mcp_server --cov=scripts --cov-report=term-missing` | 642 项通过；11 条依赖弃用警告 |
| `uv run --locked pytest tests/test_mcp_server.py -q` | 123 项通过 |
| `uv run --locked python scripts/ci_security_audit.py` | 通过 |
| `pio test -d firmware -e native` | 99 项原生固件测试通过 |
| `pio run -d firmware -e m5stack-cores3-public -j 4` | 编译成功 |
| `pio run -d firmware -e m5stack-cores3-railway -j 4` | 使用测试配置编译成功 |
| `pio check -d firmware -e m5stack-cores3-public --severity=high --fail-on-defect=high` | 通过，No defects found |
| `git diff --check` | 通过 |

Railway 目标的 RAM 静态占用为 103,028 / 327,680 字节（31.4%），
程序 Flash 占用为 1,480,773 / 6,553,600 字节（22.6%）。这不是运行时剩余内存测量。

新增验证覆盖缺少私密配置、未启用出站、缺少主机/证书/token、token 过短、
错误端口、误用 public 目标，以及构建失败时不得上传磁盘上残留的旧固件。
继承的集成测试通过真实本地 MCP/WebSocket 服务验证图片字节回传，并用真实
ffmpeg 验证 MP3 到设备 PCM 的转换；输入设备和付费 TTS 提供商均为测试替身。

首次全套 Python 检查中，一项本地服务测试遇到临时端口被占用；单项复查及
完整重跑均通过。没有以跳过测试或改动云端业务逻辑掩盖该失败。

## 构建依赖

使用 PlatformIO 6.1.18、espressif32 7.0.0、Arduino ESP32 2.0.17、
M5Unified 0.2.15、M5GFX 固定提交 `5268353`、StackChan-BSP 固定提交
`0addef4`、ArduinoJson 7.4.3、AnimatedGIF 2.2.3、WebSockets 2.7.2。
`pio pkg list` 还列出安装器拉取的 M5GFX 0.3.0；编译依赖图和编译日志实际
选用的是直接固定的 M5GFX 0.2.21 源码提交，没有将项目升级到 0.3.0。
其他解析出的库包括 IRremoteESP8266 2.9.0、M5Unit-NFC 0.1.2、
M5UnitUnified 0.6.5、M5HAL 0.1.4、M5Utility 0.3.1。

云端原有的 esptool 缓存安装不完整，已从缓存归档恢复，并与 PlatformIO
注册表的 SHA-256 核对一致。此操作没有更改项目依赖版本，也没有关闭 TLS 校验。
Railway 编译验证复用相同依赖集，配置仅含示例主机和测试凭据，未烧录到设备。

## 尚需实机确认

- Windows 上的证书获取、依赖下载、USB 端口和烧录流程需要在用户电脑执行。
- 机器人使用真实 Wi-Fi/设备 token/CA 后能否持续连接 Railway。
- 实际摄像头画面、扬声器声音，以及 Kelivo 是否把图片传给支持图像的模型。
- Railway 当前部署版本、副本数量和真实 TTS 配置。

此次没有访问真实机器人或付费 TTS，没有更新 Railway。云端联网白名单阻止了
对用户 Railway 主机的 HTTP 检查，不能由该网络拒绝推断 Railway 服务故障。
测试配置、测试固件、缓存、日志和私密文件不包含在发布的源码中。
