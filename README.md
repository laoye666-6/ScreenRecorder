# 屏幕录制工具（ScreenRecorder）

Windows 桌面录屏工具：自由框选录制区域、自定义帧率与输出分辨率、录制时自动隐藏控制窗口、
全局快捷键操作。采集走系统原生合成画面（桌面复制 API 优先，WGC 兜底），视频 1:1 原生像素，
流式直写编码器，内存占用恒定。

## 下载即用

到 [Releases](../../releases) 下载 `ScreenRecorder-vX.Y.Z-windows-x64.exe`，双击运行，
**无需安装 Python**（exe 已内置 ffmpeg，仅依赖系统自带组件）。

- 单文件模式启动时需要短暂解压，首次运行等几秒属正常现象
- 个别杀毒软件可能对 PyInstaller 单文件误报，可在源码自行构建（见下文）

## 功能

- **录制区域**：全屏半透明框选（每个显示器均可选）、双击选整屏，或直接输入 X/Y/宽/高 物理像素坐标
- **帧率**：15~240 可选可自定义（受采集源上限约束，见下方"性能说明"）
- **输出分辨率**：默认原生尺寸（零缩放）；也可指定宽×高，ffmpeg lanczos 高质量缩放
- **录制时隐藏主窗口**：倒计时结束后自动隐藏，托盘图标显示状态，F9/F10 全程可用
- **全局快捷键**（可自定义，支持 Ctrl/Alt/Shift/Win 组合）：
  - `F9` 开始 / 停止
  - `F10` 暂停 / 继续（暂停通过无损分段拼接实现，暂停区间不会出现在成片中）
- **编码**：自动探测硬件编码器（NVENC / QSV / AMF），无硬件编码时回落 libx264；
  H.264 / H.265 可选；画质 超高 / 高 / 均衡 / 无损（无损为 x264/x265 yuv444p qp=0，文件很大）
- **鼠标指针**：WGC 后端由系统原生合成；DDA 后端自动用光标位图叠加，均可开关
- 输出 mp4（H.264 用 BT.709 色彩标记，播放器色彩还原正确），录制参数持久化到 settings.ini

## 安装与运行（源码方式）

1. 安装依赖：双击 `安装依赖.bat`（需要已安装 Python 3.10+ 与 ffmpeg；
   ffmpeg 不在 PATH 时可把 `ffmpeg.exe` 放到 `bin\` 目录下）
2. 启动：双击 `启动录屏工具.bat`

### 自己打包单文件 exe

```
.venv\Scripts\python.exe -m pip install pyinstaller pillow
.venv\Scripts\python.exe packaging\prepare.py     # 下载静态 ffmpeg 到 packaging\
.venv\Scripts\python.exe -m PyInstaller --clean -y screenrec.spec
```

产物为 `dist\ScreenRecorder.exe`（已内置 ffmpeg，约 100+ MB）。

命令行自检（无 GUI 跑通采集→编码→产物校验）：

```
.venv\Scripts\python.exe -m screenrec --test 5 30 --direct
```

## 使用

1. 选择显示器，点击"框选区域…"拖拽框选（或点"整个屏幕"），也可以手输坐标
2. 设置帧率、分辨率、画质、输出目录
3. 点"● 开始录制"（或按 F9）→ 倒计时 → 窗口自动隐藏，开始录制
4. 按 F10 暂停/继续，按 F9 停止；文件自动保存并弹出通知
5. 关闭主窗口不会退出程序（最小化到托盘），托盘菜单可退出

## 架构与性能设计

```
采集线程（windows-capture）
  DDA 桌面复制（优先，无边框、低开销） → 失败自动降级 WGC（Windows.Graphics.Capture）
      │ BGRA 原生帧，零缩放零色彩转换
      ▼
录制循环（固定节拍 timeBeginPeriod(1) + 绝对时间对齐，第一帧起拍）
      │ 仅保留最新一帧；静止画面按对象身份跳过重复拷贝；区域切片单次拷贝
      ▼
ffmpeg stdin 流式管道（BGRA rawvideo）
      │ 硬件编码优先（QSV/NVENC/AMF），mp4 直写磁盘
      ▼
暂停 = 结束当前分段；停止 = concat demuxer 无损拼接 + faststart
```

内存占用恒定：采集侧任意时刻只保留 1~2 帧缓冲，帧数据不落盘、不进队列，
全程背压流式传输。典型 1080p60 总内存（Python + ffmpeg）约 300~500MB。

视频速度与真实时间一致：节拍循环按绝对时钟推进，编码器跟不上时丢弃（而非延迟）帧，
状态栏实时显示实际帧率。

## 性能说明（本机实测）

本机（AMD R5 M330 + Intel HD 620，Windows 10 22H2）：

- **桌面复制（DDA）不可用**：Windows Update 下载的 AMD 通用驱动不支持
  `IDXGIOutput::DuplicateOutput`（错误 0x887A0004）。更新 AMD 官方完整版驱动后，
  本工具会自动改用 DDA（更快、无屏框）。
- **WGC 上限约 40fps**：Intel 核显驱动下 WGC 帧交付实测 ~38-43fps。因此 60fps 目标
  实际帧源约 40fps（多出的帧由重复帧补齐，画面与源一致）；**推荐使用 30fps**。
- 编码吞吐实测：h264_qsv（QSV 硬编）~72fps、libx264 veryfast ~59fps（1080p BGRA 输入，
  最坏情况随机噪声），均满足 30fps 实时；60fps 建议用硬件编码器。

## 故障排查

- **提示采集初始化失败**：查看错误中的 DDA/WGC 两段原因。WGC 失败通常是系统组件问题；
  两者都失败时无法录制（如远程会话、安全模式）。
- **Win10 使用 WGC 时屏幕边缘有黄色边框**：系统行为（Win11 才允许关闭），**不会进入视频**。
- **快捷键无效**：可能被其他程序占用，换一个组合键；Qt 事件循环空闲时依然全局生效。
- **帧率达不到目标**：受采集源上限（见上）；状态栏"实际 fps"即为真实值。
- **帧率低 + CPU 高**：把画质调低一档，或改用硬件编码器。
- 多显示器混 DPI 环境下框选坐标若有少量偏移，可手输坐标微调。

## 目录结构

```
screenrec/
  main.py              入口（DPI 感知 / GUI / --test 自检）
  core/
    monitors.py        物理显示器枚举
    capture.py         DDA/WGC 后端 + 录制节拍循环 + 分段管理
    encoder.py         ffmpeg 定位/探测/参数/拼接
    cursor.py          DDA 后端光标合成
    hotkeys.py         RegisterHotKey 全局快捷键
  ui/
    main_window.py     主窗口/托盘/状态机
    region_overlay.py  全屏框选
diagnostics/           环境诊断脚本（显卡能力探测等）
packaging/             打包脚本（下载 ffmpeg / 图标 / spec）
```

## 许可

本项目代码以 [MIT](LICENSE) 发布。发行包内附带的 `ffmpeg.exe` 来自
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/)（FFmpeg essentials 构建），
遵循 GPL-3.0 许可，其源码可在 [FFmpeg 官方仓库](https://git.ffmpeg.org/ffmpeg.git) 获取。

