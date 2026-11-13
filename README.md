# VideoFusion Studio

VideoFusion Studio 是一个桌面 AI 混剪工具。导入视频和图片、填写剪辑要求后，可以批量生成带配音、配乐和字幕的 MP4，并对成片提出修改意见。

## 下载并启动

打开 [GitHub Releases](https://github.com/willzhang35929924-ui/video_studio/releases)，在版本页面的 **Assets** 中下载与你的电脑对应的发布包。文件名里的版本号以该页面为准。

| 电脑 | 发布包 | 启动方法 |
| --- | --- | --- |
| Mac（Apple 芯片，如 M1、M2、M3） | `VideoFusion-Studio-<版本>-mac-arm64.zip` | 解压 ZIP，双击 **VideoFusion Studio.app** |
| Mac（Intel 芯片） | `VideoFusion-Studio-<版本>-mac-x64.zip` | 解压 ZIP，双击 **VideoFusion Studio.app** |
| Windows（64 位 Intel 或 AMD） | `VideoFusion-Studio-<版本>-win-x64.exe` | 双击便携版 EXE，无需安装 |

Mac 不确定芯片时，点苹果菜单 →「关于本机」查看。下载页里的 **Source code (zip)** 是源码，不能代替启动包。如果对应平台的发布包尚未出现在 Assets 中，说明该平台的包还未上传。

发布包包含本地视频处理程序和 FFmpeg，无需额外安装 Node.js 或 Python。AI 混剪仍需联网以及可用的 AI 服务地址和密钥。若系统因开发者未验证而阻止打开，请先确认文件确实来自上述仓库的 Releases；不要关闭系统安全保护。

## 使用教程

完整步骤见 [使用教程](docs/使用教程.md)：包括 AI 接口配置、新建混剪任务、参考视频、批量生成、播放下载、提交修改意见、分页和永久删除。

## 从源码构建

在目标操作系统和处理器架构上安装 Node.js 24 与 Python 3.12，随后在仓库根目录执行：

```bash
python -m pip install -r requirements-build.txt
npm ci
npm test
python -m unittest discover -s tests
```

- Mac：执行 `npm run dist:mac`，产物输出至本地 `release/`。
- Windows：执行 `npm run dist:win`，产物输出至本地 `release/`。
- Mac 开发调试：可运行 `npm start`；开发环境需准备可运行的 Python、FFmpeg 和 FFprobe。

如有多个 Python 环境，可设置 `VIDEOSTUDIO_PYTHON` 指向已经安装构建依赖的解释器。Mac 打包后可运行 `python tests/verify_complete_mix.py --provider aliyun --packaged-app "release/mac-arm64/VideoFusion Studio.app"`。该验收使用本地模拟 AI 接口和真实 FFmpeg，不消费用户 API 额度；应用路径按实际生成的目录调整。
