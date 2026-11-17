# recovery-20261118.3 验证记录

基于 `e2ef3df`（上一版 recovery-20261118.2）；保留传感器 ACK 修复、MiniMax、
照片分片回传和预编译 USB 配置功能。本次修复日志已确认的 PCM 过早接收超时。

## 已确认的 PCM 断线触发点

实机日志：元数据在 0 ms 被接受，49,188 字节的帧在 4,713 ms 开始；7,015 ms 时
仅收到 13,900 字节，库报 `rx_timeout` 并主动关闭 1002。命令仍有效，尚余约
12.8 秒，未进入音频处理。其直接触发点是原先 2 秒的接收空闲限制。

本版对等待音频的命令采用共享的 8 秒空闲计时：从元数据接受时开始，只由实际
收到的字节刷新，帧头／分片／新一次 readCb 调用不会另起计时。同步接收期间也
检查服务器截止时间和固件 20 秒总期限，连接、握手、写入和独立文本接收不变。
失败命令不会自动重放。本次不用修改服务器或重新填写设备凭据。

实际 WebSockets 2.7.2 接收函数的主机回归测试，先在旧函数复现收到 13,900 字节
后失败，再验证新函数能在模拟的 3 秒停顿后收齐 49,188 字节。另覆盖 8 秒无进展、
持续少量流量到 20 秒时停止、最终一次读取跨过截止时间、断线、零字节读取、
新帧读取不重置共享空闲计时及原有文本超时。测试过的补丁函数与构建依赖源码逐字一致。

## 已通过的检查

- Ruff：通过；Pyright：0 errors、0 warnings；项目安全审计通过。
- 完整 Python 测试：744 passed，14 项依赖弃用警告。
- PlatformIO native 固件测试：134/134 通过。
- CoreS3 runtime 编译成功，应用镜像 1,483,856 字节。
- 高严重级别 cppcheck：No defects found。
- 分区地址、镜像目标/尺寸、SHA-256、ZIP CRC 和解压后助手验证通过。
- 包含 22 项 WebSockets 补丁／真实接收函数测试及 5 项固件接收窗口测试。
- 中间件校验版本及全部锚点后才写依赖文件，支持 .2 补丁升级和重复应用。
  保持关闭原库调试输出；日志不包含 token、请求正文或音频。
- --flash-only 保留 runtime NVS；--monitor 有界只读，不发复位/配置命令。
- 监视器跨串口读取边界和超长行测试通过；没有证据表明它造成日志中间字符缺失。
- 依赖清单已检查；未升级平台或库版本。

前版传感器 ACK 误检测的七项测试仍通过。PCM 首段请求经生产 C++ 解析器的
真实 loopback HTTP/WebSocket 测试仍覆盖 2、49152、131072 字节。前版实际 staging
代码的 48 KiB 首段及 1.5 MiB 连续拼接已通过主机 ASan/UBSan 检查；本次未修改它。

## 下载包

`releases/cores3-recovery-20261118.3.zip`，**959,631 字节**。

SHA-256：

```text
67511302d48d6688c6ade24d186ace40fa3b2acffc688283fb4c4bcc3cf5b52d
```

旧版 20261118.1 和 .2 的包及校验值保持不变。包不含用户凭据，不需要下载 ESP32 编译器。

## 实机边界

用户随后确认云端 `9f4cad9` 昨天已经能正常说话，换固件后才失败；此前视觉和
录音没有正常。应保持云端不变，优先查固件回归。云端小分段候选已停止，没有
代码修改、提交或部署。旧源码里已有 2 秒接收限制，因此本次确认的是当前关闭
机制，尚未确定相对于昨日实际烧录镜像的完整回归原因。

已确认固件因接收空闲超时提前断开，但不能由该日志定位链路停顿来自代理、无线、
TLS 记录缓冲还是其他原因。八秒是有界容忍窗口；软件测试不能保证现场链路一定
在原有总期限内完成。仍需刷入后运行一次短句，确认完整接收及实际发声。

本次未连接用户 USB 硬件，未做真实 Wi-Fi/TLS、I2C、扬声器或 Windows 串口验收。
仅按 GitHub 源码工作，没有访问 Railway。服务器端 sense 的错误显示改进保存在
恢复分支，需部署才会生效；固件 ACK 检测修复和本次 PCM 接收修复可独立生效。
未假定用户有或没有外接 ENV 模块，也未生成虚构的传感器读数。

## 复核命令与本次日志

```sh
python -m ruff check .
python -m pyright
python -m pytest --cov=mcp_server --cov=scripts --cov-report=term-missing
python scripts/ci_security_audit.py
pio test -d firmware -e native
pio run -d firmware -e m5stack-cores3-runtime
pio check -d firmware -e m5stack-cores3-runtime --severity=high --fail-on-defect=high
pio pkg list -d firmware -e m5stack-cores3-runtime
```

使用配置好依赖的 Python／PlatformIO 环境；本次日志保存在工作区的
`work/python-tests-20261118.3.log`、`work/native-tests-20261118.3.log`、
`work/runtime-build-20261118.3.log`、`work/firmware-check-20261118.3.log`。
