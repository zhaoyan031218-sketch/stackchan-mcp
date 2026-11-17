# recovery-20261118.2 验证记录

基于 `ced673c`（上一版 recovery-20261118.1）；保留 MiniMax、照片分片回传和
预编译 USB 配置功能。本次有一个已复现的传感器检测修复，PCM 断连目前是诊断增强。

## 已确认的传感器缺陷

原探测只检查 I2C start()，忽略 stop() 才能返回的 ACK/NACK 结果，会误判未应答的
SHT31 存在。五项失败回归在旧实现上复现，修复后相关七项测试全部通过。
这不证明用户一定没接模块；实际型号、接线和读取情况仍需确认。

## 已通过的检查

- Ruff：通过；Pyright：0 errors、0 warnings。
- 完整 Python 测试：732 passed，14 项依赖弃用警告。
- PlatformIO native 固件测试：129/129 通过。
- CoreS3 runtime 编译成功，应用镜像 1,483,504 字节。
- 高严重级别 cppcheck：No defects found。
- 分区地址、镜像目标/尺寸、SHA-256、ZIP CRC 和解压后助手验证通过。
- PCM 两个服务器版本的首段请求均通过生产 C++ 固件解析器；真实 loopback
  HTTP/WebSocket 测试覆盖 2、49152、131072 字节的 staged seq0/final0。
- 实际 staging 代码的 48 KiB 首段和 1.5 MiB 连续拼接通过主机 ASan/UBSan 检查。
- WebSockets 2.7.2 补丁有版本/源码锚点、原子校验和重复应用测试；保持关闭原库
  调试输出。新增日志仅包含固定事件名称与数字，不包含 token、请求正文或音频。
- 新增 --flash-only 保留 runtime NVS；--monitor 有界只读，不发复位/配置命令。

## 下载包

`releases/cores3-recovery-20261118.2.zip`，**959,404 字节**。

SHA-256：

```text
56fd8b1d9394449d88247a5c6562a1f0552570442cdfc98786f7377681066318
```

旧版 20261118.1 的包和校验值保持不变。包不含用户凭据，不需要下载 ESP32 编译器。

## 尚未确定／实机边界

音频 503 说明首段确认前连接断开。没有证据证明 MiniMax、扬声器或 PCM 格式是
根因，也没有证据证明本版已修复实际音频断连。新的日志用于区分接收失败、
本地主动关闭、时间校验、分配失败与设备重启。

本次未连接用户的 USB 硬件，未做真实 Wi-Fi/TLS、I2C、扬声器或 Windows 串口
验收。仅按 GitHub 源码工作，没有访问 Railway。服务器端 sense 的错误显示改进
保存在恢复分支，需部署才会生效；固件 ACK 检测修复可独立生效。
