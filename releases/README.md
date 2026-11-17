# CoreS3 recovery-20261118.2

[下载 20261118.2 升级包](https://github.com/zhaoyan031218-sketch/stackchan-mcp/raw/refs/heads/codex/cores3-rebuild-20261118/releases/cores3-recovery-20261118.2.zip)

959,404 字节。修复传感器 ACK 误检测并增加 PCM 断连诊断；音频断连
实机原因仍未确认，不能宣称此版本已经修好声音。Windows + Python 3.11 以上。

已经配网的设备：完整解压新包，在该目录终端运行（替换实际 COM 号）：

```powershell
.\START-SETUP.cmd --flash-only --port COM5
.\.venv\Scripts\python.exe scripts\setup_prebuilt.py --monitor --port COM5
```

第一条只升级固件，保留现有 Wi-Fi 和设备 token；第二条只读诊断 90 秒。
监视开始后在 Kelivo 发起一次短句播放，失败时复制 OUTBOUND/PCM/重启日志。

SHA-256：`56fd8b1d9394449d88247a5c6562a1f0552570442cdfc98786f7377681066318`

[完整步骤](../START-HERE.zh-CN.md) · [验证结果与实机限制](../VALIDATION.zh-CN.md)

旧版 `cores3-recovery-20261118.1.zip` 保留作为回退材料，其内容及校验值未改变。
