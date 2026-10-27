# audio - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件仅约束音频后端与设备管理。

- 所有 sounddevice / PortAudio 访问集中在 `audio_backend.py`；Software Loop 的 PyAudioWPatch / PortAudio 访问集中在 `pyaudiowpatch_backend.py`。本目录禁止导入 PySide6、UI、data 或应用状态。
- `AudioBackend.list_devices()` 统一返回 `AudioDevice`；后端异常转换为 `AudioBackendError`，不得把后端异常直接抛入 UI。
- `AudioDevice.identifier` 对真实设备保存 PortAudio 索引，对模拟设备使用独立字符串 ID；播放与录音能力必须分别判断，禁止假设设备为双工。
- Windows 枚举优先保留 WASAPI 设备；`SoundDeviceBackend.list_devices()` 必须先经 `PaWasapi_UpdateDeviceList` 更新设备表再查询。默认 PortAudio 构建未启用动态设备表时会返回 `paInternalError (-9986)`，此时只允许在无活动流、扫描单飞且未开始测量时通过 sounddevice 终止并重新初始化 PortAudio；其他原生错误转为 `AudioBackendError`，不得返回旧列表。`AudioDeviceManager.scan()` 必须基于同一次后端查询拆分输入、输出列表，避免不一致快照。
- `software_loopback_capability()` 只报告真实 PyAudioWPatch / WASAPI 环回端点能力。Software Loop 必须严格匹配所选输出及其 `[Loopback]` 端点，播放与采集共用同一个 `PyAudio` 实例和会话 `clock_domain`；禁止用 Stereo Mix、麦克风或离线模拟冒充 Software Loop。
- 模拟后端采样率由调用方配置传入，仅提供明确标注的虚拟设备；不得伪造声学样本、时间戳或测量结果。
- UI 不得直接调用同步枚举；后续回调引擎仍须遵守根文件的连续流、warm-up 与回调线程红线。
- `open_output_stream()` / `open_input_stream()` 统一返回 `AudioStream`；回调只接收后端映射后的时间戳与状态，引擎不得反向导入 sounddevice。
- `PlaybackEngine` 仅在 warm-up 后接受绝对样本索引调度；`CaptureEngine` 使用单生产者/单消费者、无锁非阻塞写入的 Ring Buffer，禁止在音频回调中等待锁；溢出、欠载、丢帧和意外停流由控制线程通过 `check_health()` 处理。
- `ChirpGenerator` 预生成只读单声道参考波形；参数由配置注入，刺激与背景音叠加后的削波检查仍由 `PlaybackEngine` 执行。
- `AudioSynchronizer` 只使用 DAC/ADC 锚点映射样本时间；时钟偏移或漂移为 `None` 时必须保留未校准限制，禁止回退到 `current_time` 或 Python 调用时间。
- `CaptureEngine.read_*_with_metadata()` 提供采集块绝对起点；发生丢帧后起点为 `None`，不得沿用连续时间轴。
- 修改后端或引擎后验证 `tests/test_audio_backend.py`、`tests/test_audio_engines.py` 和真实枚举；Software Loop 还须验证 `tests/test_pyaudiowpatch_backend.py` 与真实数字环回测量。普通真实链路用 `python -m app.audio_diagnostic` 独立验证，专题 @see docs/ai/05_AUDIO_GUIDE.md、docs/ai/04_ARCHITECTURE.md。
