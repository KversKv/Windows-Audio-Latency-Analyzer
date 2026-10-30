# 03 GOTCHAS - 踩坑记录

## PortAudio 独立流的时间戳不能默认直接相减

- **现象：** 输入与输出各自打开独立 PortAudio 流，`inputBufferAdcTime` 和 `outputBufferDacTime` 都有值，但原始延迟仍可能偏移。
- **原因：** PortAudio API 将时间戳关联到对应流；跨流能否相减取决于具体 host API 和构建。Fake 测试若都从相同常数起算，会掩盖未经验证的时间基准假设。
- **正确做法：** 后端只有在目标 host API/build 的实现能证明共享原点时才给两条流相同 `clock_domain`。本项目只对 Windows WASAPI + PortAudio 19.7.0 按共同 QPC 时基标记；其他组合未知/不匹配时保留波形和互相关数据、将绝对延迟置空并标无效。硬件验收还要用已知参考延迟验证 ADC/DAC 延迟估计、偏移和漂移。

## PySide6 offscreen 测试中的工作线程信号等待

- **现象：** 在 PySide6 6.12 / offscreen 环境中，`QSignalSpy.wait(2000)` 可接收普通 `QTimer` 信号，但本项目的设备枚举、配置保存 queued 信号未在断言前分发；测试失败后还可能因窗口线程未清理而异常退出。
- **正确做法：** 使用 `QEventLoop` + 单发 `QTimer` 显式运行事件循环并设置超时；连续操作等待对应的 `finished` 信号，避免数据到达后线程尚未收尾的竞争。pytest fixture 必须在失败路径也执行窗口与线程清理。参考 `tests/test_main_window.py`。

## 后台设备扫描导致页面自动滚动

- **现象：** 设备下拉框获得焦点后，后台扫描将其临时禁用，Qt 把焦点移到下方的 `QSpinBox`，`QScrollArea` 随之自动滚动，看起来像页面无故向下跳转。
- **正确做法：** 扫描只禁用重复刷新和测量 / 诊断启动，保持设备与诊断模式选择可用；音频活动期间的禁用逻辑不变。焦点回归测试的 fixture 必须在窗口销毁前执行 `clearFocus()`，否则共享 `QApplication` 的组合测试可能在后续线程收尾时发生进程级访问冲突。

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
- **CFFI 能力探测：** `hasattr(sd._lib, 未导出符号)` 可能直接抛 `ffi.error`，不能当作普通 `AttributeError`，普通枚举不应因此失败。Software Loop 不再依据 sounddevice 的 Loopback 符号判定，统一由 `pyaudiowpatch_backend.py` 检测并使用真实 PyAudioWPatch / WASAPI 端点。

## QThread 活动会话的有界关闭

- **现象：** UI 调用 `TestController.shutdown()` 并阻塞等待时，Worker 已请求停止但线程仍等到超时；完整 offscreen 套件还曾在收尾点进程级中止。
- **正确做法：** `worker.finished → thread.quit` 显式使用 `Qt.ConnectionType.DirectConnection`，使工作线程完成时直接请求事件循环退出，不依赖被阻塞的 UI 先处理 queued 槽。关闭仍必须有超时，连续操作优先异步等待 `finished`，并验证停止后资源释放和下一次会话可启动。

## QThread finished 槽内同步释放 Python Worker

- **现象：** Realtek WASAPI Loopback 连续 20 次测量均有效后，进程在 `TestController._on_thread_finished()` 的 `self._worker = None` 处原生中止；仅提前关闭音频流不能消除。保留引用或推迟释放后，相同测量正常完成并退出。
- **正确做法：** 音频引擎和后端仍在 Worker 收尾路径关闭；`finished` 槽只释放线程引用并发出通知，用 `QTimer.singleShot(0, ...)` 在事件循环下一轮清空 Worker，且校验实例避免误清新会话。回归测试须断言 `finished` 触发时 Worker 尚未同步销毁，处理后引用再清空；真实批量测量还需检查进程退出码，不能只看有效样本数。
