# Software Loop 数字环回实现计划

## Context

Hardware / Acoustic Loop 已复用现有双工测量链路，但 Software Loop 目前仍被能力检测和 UI 禁用。现有 sounddevice 0.5.6 / PortAudio 构建没有可用的 WASAPI Loopback Capture 接口，直接放行会错误采集麦克风。此次目标是采集**所选播放设备的真实数字环回**，复用连续播放、warm-up、Chirp、RingBuffer、互相关和无效记录机制；禁止回退到麦克风、Stereo Mix、模拟后端或伪造时间戳。

## 实施方案

### 1. 新增真实 WASAPI 数字环回后端

- 新增 `audio/pyaudiowpatch_backend.py`，使用 Windows 专用依赖 PyAudioWPatch；懒加载导入，非 Windows 或依赖缺失时返回明确不可用原因。
- 每个 Software 诊断会话创建一个后端实例，**播放流和 Loopback 采集流共用同一个 `PyAudio` / PortAudio 实例**，避免与 sounddevice 混用后无法证明时钟基准一致。
- 按现有 `AudioDevice` 的名称和 WASAPI 类型严格匹配输出端点，再唯一匹配其 `[Loopback]` 端点；跨库设备索引不直接复用。重名、多个候选、无对应环回或格式不支持均明确失败，不选择其他设备替代。
- 实现现有 `AudioBackend` / `AudioStream` 契约：适配 bytes 与 float32、回调状态、流生命周期和 `input_buffer_adc_time` / `output_buffer_dac_time`；回调中不执行 UI、IO 或分析。Loopback 原生声道与配置声道不一致时只做确定性的声道映射，Chirp 仍取现有第一采集声道分析。
- `audio/audio_backend.py` 的 `software_loopback_capability()` 改为探测真实依赖、WASAPI 和环回端点；普通设备枚举及 Electrical / Acoustic 的 sounddevice 路径保持不变。

### 2. 显式校验时间戳和共享时钟域

- 在 `AudioCallbackTime`、`StreamTiming` 和 `ScheduledStimulus` 中传递可选 `clock_domain`；新后端的两个流写入同一个会话级标识，默认字段不破坏现有后端。
- `core/test_controller.py` 仅当 Software 模式的播放与采集时钟域非空且一致时，才按共享 PortAudio 时钟使用零时钟偏移建立同步器；不一致、缺失或非法时间戳时保留原始波形，令延迟值为 `None`，记录明确 `invalid_reason`，禁止用 `current_time` 或 Python 时间替代。
- `audio/audio_synchronizer.py` 保留现有未校准限制机制，并允许携带时间依据类型；`core/latency_analyzer.py` 将 Software 结果标为 WASAPI 数字环回流时间戳，避免误称真实 ADC 声学采集。
- `ui/diagnostics_panel.py` 增加相应时间依据和无效原因文案，结果仍明确为“软件偏移、原始、未补偿”。

### 3. 将诊断模式传入 core，隔离采集路由

- `core/test_models.py` 的 `TestSessionConfig` 增加受限的 `diagnostic_mode`（普通测量为 `None`），core 不导入 UI 枚举。
- `DiagnosticsPanel.start()` 为三种诊断显式写入模式；Software 配置清空输入设备 ID / 名称，Electrical / Acoustic 行为不变。
- `TestSessionWorker` 在 Software 下只验证输出设备，创建并持有上述专用后端，将其同时注入现有 `PlaybackEngine` 和 `CaptureEngine`；其他模式继续注入 `DeviceService.backend`。会话结束先关闭两个引擎，再终止专用后端，异常路径也完整释放。
- 复用现有单次开流 / start、连续背景音、warm-up、采集窗口、分析和无效结果保留，不另写一套测量算法，不修改数据库或导出。

### 4. UI 配置与状态

- `ui/test_page.py` 允许 Software Loop 在未选择、甚至没有输入设备时生成诊断配置；普通测量和 Hardware Loop 仍强制要求输入设备，Software 不套用输入设备校准。
- `ui/diagnostics_panel.py` 只在非 Software 模式要求输入设备；能力缺失时仍禁用启动。Software 配置摘要显示“所选播放设备 → 其 WASAPI 数字环回”，不展示麦克风路由。
- 保持三种模式结果隔离、测量 / 诊断互斥、设备扫描暂停和后台扫描不抢焦点的现有行为。

### 5. 依赖、打包和局部约定

- `requirements.txt` 添加仅 Windows 生效的 PyAudioWPatch 依赖；`spec/latency_analyzer.spec` 收集其 Python 模块、原生扩展和 DLL，确保冻结程序不包含缺失后端。
- 按同步矩阵核对 `README.md`、`docs/ai/02_COMMANDS.md`，仅在安装或冒烟说明需要时做最小更新。
- 更新 `audio/AGENTS.md` 与 `docs/ai/05_AUDIO_GUIDE.md` 中“PortAudio 仅走 sounddevice”的局部约定，记录新后端只允许输出绑定的数字环回及共实例时钟要求；`Schedule.md` 按实际验证结果更新，不把自动化合成测试写成实机验收。

## 验证

1. **后端单元测试**（`tests/test_audio_backend.py`，必要时新增聚焦测试文件）：模拟 PyAudioWPatch 回调、严格端点匹配、声道 / bytes 适配、能力缺失、重名拒绝、共实例开流与资源释放；断言不会打开普通输入设备。
2. **时间与分析测试**（`test_audio_engines.py`、`test_audio_synchronizer.py`、`test_latency_analyzer.py`）：时钟域传播、匹配时零偏移、缺失 / 不匹配时无延迟值且原始记录保留、Software 时间依据正确。
3. **controller / UI 回归**（`test_test_controller.py`、`test_main_window.py`）：确定性数字环回得到已知偏移；双流仅打开和启动一次并完成 warm-up；无输入设备可执行 Software；Electrical / Acoustic、停止释放、模式结果隔离和模拟后端禁用保持通过。
4. 运行完整 `python -m pytest tests/`、`compileall` 和 `git diff --check`，再构建 PyInstaller 包验证后端随包可用。
5. **真实 Windows WASAPI 冒烟**：选择一个实际输出设备，在无输入设备路由的情况下执行 Software Loop，确认采集来自该端点数字环回、DAC/ADC 时间锚点与共时钟校验通过、互相关峰和多次结果稳定；若无法验证，则继续报告不可用 / 无效，不以模拟或麦克风结果冒充完成。
