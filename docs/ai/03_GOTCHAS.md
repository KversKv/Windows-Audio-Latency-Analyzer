# 03 GOTCHAS - 踩坑记录

## PySide6 offscreen 测试中的工作线程信号等待

- **现象：** 在 PySide6 6.12 / offscreen 环境中，`QSignalSpy.wait(2000)` 可接收普通 `QTimer` 信号，但本项目的设备枚举、配置保存 queued 信号未在断言前分发；测试失败后还可能因窗口线程未清理而异常退出。
- **正确做法：** 使用 `QEventLoop` + 单发 `QTimer` 显式运行事件循环并设置超时；连续操作等待对应的 `finished` 信号，避免数据到达后线程尚未收尾的竞争。pytest fixture 必须在失败路径也执行窗口与线程清理。参考 `tests/test_main_window.py`。

## PyInstaller spec 位于子目录时的资源路径

- **现象：** 从项目根目录执行 `python -m PyInstaller spec/latency_analyzer.spec` 时，spec 中的 `main.py` 被解析为 `spec/main.py`，导致入口不存在。
- **正确做法：** 用 `Path(SPECPATH).parent` 构造入口、`pathex` 和 QSS 数据文件的项目根绝对路径；运行时读取的 `dark_theme.qss` 必须列入 `datas`。构建后分别以模拟和真实后端执行冻结 exe 的 `--smoke-test`，并用 `LATENCY_ANALYZER_CONFIG_DIR` 隔离配置、数据库和日志。

## pyqtgraph 0.14 打包不能直接排除 QtOpenGL

- `PlotCurveItem` 与 `Qt.OpenGLHelpers` 在导入阶段就加载 `QtOpenGL` / `QtOpenGLWidgets`；即使图表使用光栅渲染，排除这些 Python 绑定也会导致启动失败。
- 未启用 OpenGL 时可以过滤 `opengl32sw.dll` 软件渲染后端；后续若启用 pyqtgraph OpenGL 加速，必须同时移除该过滤。

## sounddevice 自带 PortAudio 的 WASAPI 热插拔更新限制

- **现象：** `PaWasapi_UpdateDeviceList` 符号存在，但调用固定返回 `paInternalError (-9986)`；monkeypatch 单元测试无法发现该真实兼容性问题。
- **原因：** sounddevice 0.5.6 随附的 PortAudio V19.7.0 使用默认构建，`PA_WASAPI_MAX_CONST_DEVICE_COUNT=0`，对应源码直接返回 `paInternalError`。
- **正确做法：** 优先调用专用更新接口；仅对 `-9986` 在无活动流、扫描单飞且未开始测量时通过 `sd._terminate()` / `sd._initialize()` 重建设备表，其他错误仍明确失败。必须执行真实 `python -m app.audio_diagnostic --list-devices` 与 Qt 后台线程冒烟，活动会话中禁止重建。
