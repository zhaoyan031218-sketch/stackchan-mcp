# Windows 编译器下载停在 0%

用户先后遇到 Windows `ConnectionAbortedError(10053)` 和长时间显示
`Downloading ... 0%`。两次查看当前临时文件 `tmpwnrl716b`，长度都是 0。
这两次采样未观察到数据写入，不能仅归因于文件较大；也无法仅凭这些日志确认
是镜像链路、终端代理、控制台选择暂停，还是其他本机原因。

PlatformIO 6.1.18 将未完成的下载写入 `.cache/downloads/tmp...`。数字进度会
取整，右侧时间是预计剩余时间。0 字节 `.lock` 是正常锁文件，不表示损坏。
pip 的升级通知与这个编译器下载无关。不要在下载仍运行时删除锁或缓存。

用户随后用浏览器访问注册表下载 URL，得到 HTTP 403：
`This download method is not supported. Please use PlatformIO Core to install packages.`
这个入口要求使用 PlatformIO Core；不能把这次 403 当成 Wi-Fi 不稳、固件损坏，
也不能再建议浏览器直接下载同一个注册表 URL。

## 保留已有配置，使用正式客户端或已校验离线包

1. 在旧编译窗口按 `Ctrl+C`，等待命令提示符回来。确认没有另一份编译仍运行。
2. 把新文件 `scripts/install_windows_toolchain.py` 放入原工程的 `scripts`
   文件夹。完整重建包不必重新解压，`firmware/src/config.h` 保留。
3. 已有离线补丁时，将其 `scripts` 和 `work` 两个文件夹合并到原工程根目录。
   补丁只含安装助手和官方编译器压缩包，不含 `firmware/src/config.h`。
   保留内层 `work/toolchain-esp32s3.tar.gz`，不要解压这个内层文件。执行：

```powershell
.\firmware\.venv\Scripts\python.exe scripts\install_windows_toolchain.py --archive .\work\toolchain-esp32s3.tar.gz
```

助手按 PlatformIO 实际识别的 Windows 架构选择官方包：x86 为 81,421,435
字节，amd64/arm64 为 84,769,299 字节。版本仍为 `8.4.0+2021r2-patch5`，
不是升级编译器。长度与 SHA-256 来自 PlatformIO 官方注册表，固定在助手中。
amd64 包 SHA-256：`c45a0427288d406a530a80f486fba7e77273f8152cb057cc48ef3aa64c085bf5`。
若包与当前 Python 架构不匹配，助手会拒绝安装，不能跳过校验。

4. 没有离线包时，可以直接运行助手的联网模式：

```powershell
.\firmware\.venv\Scripts\python.exe scripts\install_windows_toolchain.py
```

联网模式使用真实 PlatformIO Core 6.1.18 注册表安装逻辑与官方镜像选择，保持
TLS 校验及现有代理设置。仅在助手进程中，把 Core 默认的 `(10, None)`
连接/读取超时改为 `(10, 30)` 秒。30 秒是一次读取等待数据的上限，
不是整次下载的总时限；Core 自带重试和镜像切换，整个过程可能更久。
该设置不会修改后续常规构建的超时，也不能修复无法访问任何镜像的网络。

离线模式先复制并验证完整长度与 SHA-256，再按原来的注册表 owner/id/version
安装到 `work/platformio/packages`，防止下一次构建把本地包视作另一套依赖再次下载。
它与 PlatformIO 使用相同的系统锁，并在另一个安装任务运行时立即停止。
最后执行已校验官方包中的编译器 `--version`，确认能运行。

5. 看到 `Compiler installed and recognized` 后继续：

```powershell
py scripts\rebuild_robot.py
```

这解决的是当前编译器包的获取方式；后续 SDK 和库可能还需下载。Wi-Fi、设备
token、Railway、MiniMax 配置不需要为此重新生成。

## 验证范围

测试覆盖下载完整性、等长篡改、源文件在校验后改变、临时副本清理、并发安装锁
以及 Windows 包选择、异常后的超时设置还原。使用真实 PlatformIO 6.1.18 的
独立测试包验证安装后可按 owner/name/version 和 registry ID 查找，重复安装
复用已有包，带空格和中文的本地路径可正确解包；没有以测试包替换工程实际编译器。

已通过真实 Core 的 `RegistryFileMirrorIterator` 和 `ToolPackageManager.download`
下载 Windows amd64 官方包。Core 本次选择 GitHub 镜像，下载完毕后独立核对
84,769,299 字节及上述 SHA-256；未使用浏览器伪装或关闭 TLS 校验。
云端为 Linux，用户 Windows 上真实编译器的运行仍需现场执行。

本次检查：`ruff check .`、`pyright`、全量 Python 测试（650 项）通过。
实际使用 Core 的 HTTP 客户端连接一个返回响应头后停止传输的本地服务，确认
30.0 秒后触发读取超时，退出上下文后原始超时设置恢复。

参考：PlatformIO Core 6.1.18 的 `package/download.py`、
`package/manager/_download.py`、`package/manager/_install.py`、
`package/manager/base.py` 和 `package/lockfile.py`。
