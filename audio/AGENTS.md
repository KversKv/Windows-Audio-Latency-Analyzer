# audio - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件仅约束音频后端与设备管理。

- 所有 sounddevice / PortAudio 访问集中在 `audio_backend.py`；本目录禁止导入 PySide6、UI、data 或应用状态。
- `AudioBackend.list_devices()` 统一返回 `AudioDevice`；后端异常转换为 `AudioBackendError`，不得把后端异常直接抛入 UI。
- `AudioDevice.identifier` 对真实设备保存 PortAudio 索引，对模拟设备使用独立字符串 ID；播放与录音能力必须分别判断，禁止假设设备为双工。
- Windows 枚举优先保留 WASAPI 设备；`AudioDeviceManager.scan()` 必须基于同一次后端查询拆分输入、输出列表，避免不一致快照。
- 模拟后端采样率由调用方配置传入，仅提供明确标注的虚拟设备；不得伪造声学样本、时间戳或测量结果。
- UI 不得直接调用同步枚举；后续回调引擎仍须遵守根文件的连续流、warm-up 与回调线程红线。
- `open_output_stream()` / `open_input_stream()` 统一返回 `AudioStream`；回调只接收后端映射后的时间戳与状态，引擎不得反向导入 sounddevice。
- `PlaybackEngine` 仅在 warm-up 后接受绝对样本索引调度；`CaptureEngine` 使用非阻塞写入的 Ring Buffer，溢出、欠载、丢帧和意外停流由控制线程通过 `check_health()` 处理。
- 修改后端或引擎后验证 `tests/test_audio_backend.py`、`tests/test_audio_engines.py` 和真实枚举；真实链路用 `python -m app.audio_diagnostic` 独立验证，专题 @see docs/ai/05_AUDIO_GUIDE.md、docs/ai/04_ARCHITECTURE.md。
