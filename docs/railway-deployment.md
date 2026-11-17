# Railway 云端部署

此方案把 MCP 服务和单设备中继运行在一个 Railway 容器中。机器人主动建立
WSS 连接，不需要云端访问家中局域网，也不需要电脑持续在线。先部署云端，
再配置机器人；机器人尚未连接时，云端健康检查仍可通过。

## 新建 Railway 项目

1. 将包含本次部署文件的代码提交并推送到 GitHub。
2. Railway 中新建项目，从 GitHub 导入这个仓库，选择包含这些文件的分支。
   项目根目录使用仓库根目录，构建使用根目录 `Dockerfile`。
3. 在服务 Variables 中设置下表的三个必填令牌，再部署。
   不要将令牌放进仓库、Docker build arguments 或聊天记录。
4. 在服务 Networking 中生成公网域名，目标端口使用 `PORT`（未设置时为
   `8080`）。程序监听 `0.0.0.0:$PORT`，不需要覆盖 Start Command。
5. 检查 Railway 提供的 `RAILWAY_PUBLIC_DOMAIN` 已传给服务；如果使用自定义
   域名，设置 `STACKCHAN_PUBLIC_HOST` 为该域名。域名配置生效后重新部署。
6. 保持 **1 个副本、1 个 worker、单区域**；关闭服务休眠/Serverless。
   中继和 MCP 会话存在进程内存中，不支持跨副本分发连接。

根目录 `railway.json` 已指定 Dockerfile、单副本、`/healthz` 健康检查和失败
重启策略。新服务缺少必填令牌时会直接启动失败，这是预期行为。

| 变量 | 用途 |
| --- | --- |
| `STACKCHAN_MCP_AUTH_TOKEN` | 必填；AI 客户端访问 `/mcp` 的 Bearer token |
| `STACKCHAN_RELAY_DEVICE_TOKEN` | 必填；机器人连接 `/stackchan/ws` 的专用 token |
| `STACKCHAN_RELAY_TOKEN` | 必填；同容器内部 MCP → 中继控制 token，不能与前两个相同 |
| `STACKCHAN_PUBLIC_HOST` | 自定义公网域名，例如 `robot.example.com`；不含协议、端口或路径 |
| `FISH_AUDIO_KEY` | 语音合成/识别所需；仅部署并验证云服务时可暂不配置 |
| `FISH_AUDIO_MODEL_ZH` / `FISH_AUDIO_MODEL_EN` | 使用 Fish 时配置需要的语音模型 ID |

三个 token 必须各不相同，至少 24 个非空白 ASCII 字符。建议用密码管理器
生成三个独立的 64 字符随机十六进制值，并直接保存到 Railway Variables。
已有 `_FILE` 令牌配置也受支持，但不要把密钥文件打包进镜像。

Dockerfile 默认设置 `STACKCHAN_TRANSPORT=relay`、`STACKCHAN_AUDIO_MODE=pcm`。
此入口会自行分配 loopback 中继端口，不使用自定义 `STACKCHAN_RELAY_URL`。
不要把本地 `.env.example` 整份复制到 Railway；其中默认的 `direct` 与此入口
不兼容。镜像的构建上下文只包含 Python 服务和锁文件等必要文件，排除 `.env`、
固件私密配置及录音文件。原来的本地 `mcp_server.server` 入口不受影响。

如需 ElevenLabs，按主 README 设置 `TTS_ENGINE=elevenlabs` 和相关变量。
如需 MiniMax 自定义音色，参照 [MiniMax 语音配置](minimax-voice.md) 设置
`TTS_ENGINE=minimax`、`MINIMAX_API_KEY`、`MINIMAX_VOICE_ID`；国内平台地址默认已配置。
镜像包含 ffmpeg，支持 WAV/MP3 到 PCM 转换（MiniMax 语音也使用它）；
未安装可选的 `edge-tts` 命令和 OpenCV。
无人值守唤醒/语音桥也不是这个容器的默认服务，需要另行配置 AI 前端投递。

## 对外接口与验证

| 路径 | 行为 |
| --- | --- |
| `GET /healthz` | 无需认证，返回 `{"ok":true}`；检查服务就绪，不读取设备录音 |
| `/mcp` | MCP Streamable HTTP，必须带 MCP Bearer token |
| `/stackchan/ws` | 设备 WebSocket，必须带设备 Bearer token |
| `/device/*`、`/relay/status` | 公网不存在这些路由；控制 API 仅绑定容器 loopback |

生成域名后验证：

```sh
curl --fail --show-error https://YOUR_DOMAIN/healthz
curl -i -X POST https://YOUR_DOMAIN/mcp  # 应返回 401
curl -i https://YOUR_DOMAIN/relay/status  # 应返回 404
```

使用支持 Streamable HTTP 和自定义 Bearer token 的 MCP 客户端连接
`https://YOUR_DOMAIN/mcp`，配置请求头 `Authorization: Bearer <MCP token>`。
完成 initialize、ping 和 tools/list；机器人离线时也应能列出工具。
本服务没有实现 OAuth 登录流程，不能假设所有仅支持 OAuth 的客户端能直接接入。

`/healthz` 通过不表示机器人在线。机器人离线时，设备工具会返回离线错误。
公网 MCP 请求保留 SDK 的 Host/Origin 校验：若收到 421，检查域名变量；不要
用禁用校验来解决。普通服务端 MCP 客户端不需要 Origin；跨站浏览器直连需单独设计。

## 下一步：机器人配置

Windows 用户可以使用[CoreS3 配置与烧录助手](windows-robot-setup.md)，在本机
输入 Wi-Fi 和设备 token，自动验证并提取站点根 CA。

确认云端正常后，再保存现有固件和私密配置，并设置：

- `STACKCHAN_OUTBOUND_ENABLED=1`
- `STACKCHAN_OUTBOUND_HOST` 为 Railway 公网域名（不含 `https://` 或路径）
- `STACKCHAN_OUTBOUND_PORT=443`
- `STACKCHAN_OUTBOUND_DEVICE_TOKEN` 与 Railway 设备 token 相同
- `STACKCHAN_OUTBOUND_CA_PEM` 为能验证该域名证书链的正确 CA
- Wi-Fi 和 NTP 时间同步配置有效

重新编译并烧录后，设备连接 `wss://YOUR_DOMAIN/stackchan/ws`。必须验证证书，
不要启用不安全 TLS。不要把 public rehearsal 固件当成已配置设备的固件刷入。
协议、容量限制和实机验收步骤见 [outbound-channel.md](outbound-channel.md)。

## 本地验证与运行限制

```sh
uv sync --locked --dev
uv run --locked pytest tests/test_railway.py
docker build -t stackchan-railway .
# 在本机安全设置三个 token 环境变量后运行；-e 只传变量名，避免在命令行写值。
docker run --rm -p 127.0.0.1:8080:8080 \
  -e STACKCHAN_MCP_AUTH_TOKEN -e STACKCHAN_RELAY_DEVICE_TOKEN \
  -e STACKCHAN_RELAY_TOKEN stackchan-railway
```

测试启动真实 MCP 服务，并使用模拟 WebSocket 设备验证指令往返、重连、
认证隔离及 SIGTERM 退出，也验证图片能作为 MCP 图片内容完整返回。
不会访问实机或付费语音 API。

`stackchan_see` 在 relay 模式中每次只发送一条拍照请求。若失败，保留工具返回的
完整错误；Railway 日志中的 `Device channel interrupted` 会记录失败阶段和
固定错误码，不记录图片或密钥。串口显示 `Captured JPEG` 仅说明设备已生成图片，
仍需验证回传，参见 [相机 503 排查](camera-relay-troubleshooting-2026-11-17.md)。

云端入口将同步工具调用放到线程池并串行执行，保证同一事件循环中的设备心跳、
私有 HTTP 控制接口和健康检查继续响应。两个监听器共享同一个中继状态；任一
监听器意外退出都会结束进程，由 Railway 重启。

容器以非 root 用户运行；临时音频和诊断文件在 `/tmp`，部署重启后可能丢失。
此版本不提供持久化录音收件箱或多设备能力。重新部署时设备连接和 MCP 会话会
中断，客户端需重连，不会自动重放设备指令。先停止正在进行的语音或动作再部署。
