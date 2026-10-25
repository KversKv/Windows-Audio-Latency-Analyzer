# audio - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件仅约束音频后端与设备管理。

- 所有 sounddevice / PortAudio 访问集中在 `audio_backend.py`；本目录禁止导入 PySide6、UI、data 或应用状态。
- `AudioBackend.list_devices()` 统一返回 `AudioDevice`；后端异常转换为 `AudioBackendError`，不得把后端异常直接抛入 UI。
- `AudioDevice.identifier` 对真实设备保存 PortAudio 索引，对模拟设备使用独立字符串 ID；播放与录音能力必须分别判断，禁止假设设备为双工。
- Windows 枚举优先保留 WASAPI 设备；`AudioDeviceManager.scan()` 必须基于同一次后端查询拆分输入、输出列表，避免不一致快照。
- 模拟后端采样率由调用方配置传入，仅提供明确标注的虚拟设备；不得伪造声学样本、时间戳或测量结果。
- UI 不得直接调用同步枚举；后续回调引擎仍须遵守根文件的连续流、warm-up 与回调线程红线。
- 修改后端后验证 `tests/test_audio_backend.py`，并执行真实枚举冒烟；专题 @see docs/ai/05_AUDIO_GUIDE.md、docs/ai/04_ARCHITECTURE.md。
