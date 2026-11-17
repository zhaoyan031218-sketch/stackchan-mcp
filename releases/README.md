# CoreS3 recovery-20261118.3

[下载 20261118.3 升级包](https://github.com/zhaoyan031218-sketch/stackchan-mcp/raw/refs/heads/codex/cores3-rebuild-20261118/releases/cores3-recovery-20261118.3.zip)

959,631 字节。修复已由实机日志定位的 PCM 接收过早超时：允许音频接收最多
8 秒无进展，同时严格保留整条命令的截止时间，不重放失败命令。保留前版修复。
软件回归已通过，实际无线链路及发声仍需刷入后确认。Windows + Python 3.11 以上。

已经配网的设备：完整解压新包，在该目录终端运行：

```powershell
.\START-SETUP.cmd --flash-only --port COM7
.\.venv\Scripts\python.exe scripts\setup_prebuilt.py --monitor --port COM7
```

第一条只升级固件，保留现有 Wi-Fi 和设备 token；第二条只读诊断 90 秒。
`COM7` 为用户已确认的当前端口。刷完等待设备连网后，开启监视并在 Kelivo
发起一次短句播放；将结果及 OUTBOUND/PCM/重启日志用于实机确认。

SHA-256：`67511302d48d6688c6ade24d186ace40fa3b2acffc688283fb4c4bcc3cf5b52d`

[完整步骤](../START-HERE.zh-CN.md) · [验证结果与实机限制](../VALIDATION.zh-CN.md)

旧版 `cores3-recovery-20261118.1.zip` 和 `cores3-recovery-20261118.2.zip`
保留作为回退材料，内容及校验值未改变。
