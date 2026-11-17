# MiniMax 自定义音色

此适配让 `stackchan_say` 使用 MiniMax 同步语音合成，再沿现有 PCM 通道播放。
机器人固件不需要更新。自定义音色必须能通过 MiniMax 开放平台 API 使用；
`voice_id` 是 API 音色标识，不是页面上的音色显示名称。密钥与音色需要属于
有使用权限的同一区域账户，且该账户有可用的语音 API 额度。

## Railway 设置

保留已经生效的 MCP/设备令牌和 relay 设置，添加或修改：

| 变量 | 值 |
| --- | --- |
| `TTS_ENGINE` | `minimax` |
| `MINIMAX_API_KEY` | 在 Railway Variables 中安全填写开放平台 API Key |
| `MINIMAX_VOICE_ID` | 在 Railway Variables 中填写自己的音色 ID |
| `MINIMAX_API_HOST` | 国内：`https://api.minimaxi.com`（默认）；国际：`https://api.minimax.io` |
| `MINIMAX_MODEL_ID` | `speech-2.8-hd`（默认，可指定账户支持的其他语音模型） |
| `STACKCHAN_MINIMAX_TTS_TIMEOUT` | `30.0`（默认，秒） |
| `STACKCHAN_ASR_ENGINE` | `auto`（默认）；MiniMax 发声时也使用 MiniMax 识别，可明确设置 `minimax` 或 `fish-audio` |
| `STACKCHAN_MINIMAX_ASR_TIMEOUT` | `30.0`（默认，秒） |

MiniMax 模式不要求配置 Fish 或 ElevenLabs 密钥。不要将真实 API Key 或音色 ID
提交进 Git。配置摘要仅返回密钥和音色是否已配置，不返回实际值。

代码更新部署成功、变量保存并生效后，在 Kelivo 请求：

> 调用 stackchan_say，说“你好，我已经连接上啦”，lang 设置为 zh。

先用一句短话确认音色和音量。若 MCP 显示成功，代表设备接受了播放请求；
实际有没有发声仍需现场确认。MiniMax API 错误会显示 HTTP 状态或数字错误码，
不回显供应商响应正文、语音文本或密钥。

## 用 MiniMax 听懂录音

如果已经用 `TTS_ENGINE=minimax` 正常说话，更新云端代码后，默认会复用现有的
`MINIMAX_API_KEY` 和 `MINIMAX_API_HOST` 进行识别，不需要 Fish Audio 账户或密钥。
已经显式设置 `STACKCHAN_ASR_ENGINE=fish-audio` 的环境则需改为 `minimax` 或 `auto`。
无需重新烧录机器人，也不改变音色、语音合成模型或 PCM 播放设置。

对机器人说一句短话，停顿让录音结束，再在 Kelivo 调用 `stackchan_listen`。
该工具先检查是否有待取录音，再验证选定识别服务的配置；缺少密钥等本地配置
错误会在取走录音之前返回。录音取回后上传到所选服务进行转写；账户权限、余额、
网络或供应商错误可能在此阶段发生，因此收到配置正确提示不等于真实识别已经成功。

识别使用固定的 `asr-1.0` 模型，与发声的 `speech-2.8-hd` 分开；上传带文件头的
WAV，不把裸 PCM 当成 WAV。请求语言是提示信息，接口没有返回的检测语言不会伪造。
这是一次录音完成后的工具调用，不会自动开启持续收听或给 Kelivo 主动发送消息。

接口依据：[MiniMax 官方语音识别文档](https://platform.minimax.cn/docs/api-reference/speech-to-text)。

## 语音合成的行为与边界

- 请求官方 `/v1/t2a_v2`，指定 `stream=false`、自定义 `voice_id`、
  24 kHz 单声道 MP3（128 kbps）。先解开 JSON 中的十六进制编码，再由
  ffmpeg 明确解码为 24 kHz、单声道、signed 16-bit little-endian PCM。
  不直接将供应商的压缩音频或未知位深的原始字节当作扬声器采样播放。
- 运行环境需要 ffmpeg（Railway 镜像已包含）；缺失时在调用付费 API 前报错。
  完整合成、解码、检查音频长度后才开始传给机器人；解码失败不会开始播放。
- 复用现有音频大小上限（默认 2 MiB，约 43 秒 PCM）。响应 JSON 同样有上限；
  太长的语音会报错，建议拆成短句。该限制不意味着供应商不会对失败或超限的
  合成请求计费。
- 合成失败不自动重试，不自动切换其他声音。设备播放失败也不会重新调用
  MiniMax。TCP 转 staged 的传输回退复用同一份已合成音频。
- 仅允许国内/国际两个官方 HTTPS API host；不跟随重定向，保留 TLS 校验。
- 解码进程受剩余总时限与输出时长限制，超过 PCM 大小上限会报错而非截断播放。
  网络阶段有连接/读取超时及响应块之间的总耗时检查，并非严格的线程
  墙钟终止定时器。
- 后台唤醒/前端投递仍需单独设置；仅配置识别服务不会自动开启免手动语音对话。

## 开发验证

```sh
uv run --locked pytest tests/test_minimax_tts.py tests/test_minimax_audio_decode.py tests/test_elevenlabs_tts.py tests/test_stackchan_config.py
uv run --locked ruff check .
uv run --locked pyright
```

测试模拟供应商响应，不访问付费 API，也不操作实机。真实编解码回归测试需要
ffmpeg：将已知 440 Hz 测试音编码成 MP3，再经过实际播放准备路径检查 PCM
频率、幅度与时长，包括 44.1 kHz 立体声输入的重采样和混音。还覆盖请求参数、PCM/WAV
及 relay 播放路径、超限/格式错误、密钥保护、超时、错误响应、无重复合成和
现有 ElevenLabs 行为回归。真实音色权限和实际播放需要部署后现场验证。

接口与区域依据：MiniMax 官方
[MCP 文档](https://github.com/MiniMax-AI/MiniMax-MCP/blob/2904f3333474cc020676b6c91676656bc4d03b3b/README.md)、
[TTS 请求实现](https://github.com/MiniMax-AI/MiniMax-MCP/blob/2904f3333474cc020676b6c91676656bc4d03b3b/minimax_mcp/server.py)
及该版本默认模型配置。未复制其应用代码，也未新增 Python 依赖包。
