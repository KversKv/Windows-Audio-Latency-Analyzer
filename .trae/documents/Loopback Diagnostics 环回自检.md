# Loopback Diagnostics（环回自检）最小改动计划

## Context

当前有线耳机声学实测延迟超过 80 ms，需要区分软件音频链路、硬件电气链路和完整声学链路，避免把不同路径的延迟混为一谈。本功能将在现有测试页中加入三种独立诊断模式，复用已有连续音频流、Chirp、DAC/ADC 时间映射和互相关分析，不复制测量引擎，不修改数据库或历史统计。

已确认当前环境为 sounddevice 0.5.6 + PortAudio V19.7.0-devel：Python 绑定没有 Loopback 参数，原生库也未提供可用的 Loopback 端点/接口，现有后端没有 WASAPI Loopback Capture 开流路径。因此本次诚实交付边界为：

- **Electrical Loop、Acoustic Loop**：实现真实测量入口和完整诊断展示，并通过确定性双工后端做自动化验证；物理接线和真实声学结果仍需硬件验收。
- **Software Loop**：实现模式入口、运行时能力检测、不可用原因和禁止启动；本机不执行、不生成延迟结果。此次不新增未经实测的原生 WASAPI 后端，也不用 Stereo Mix、麦克风或 OfflineLatencySimulator 冒充软件回环。

## 推荐实施方式

### 1. 增加 Software Loop 能力探测，保持设备扫描单飞

修改 `audio/audio_backend.py`、`audio/audio_device_manager.py`：

- 增加简单的不可变能力结果（`supported` + 中文原因），由 `AudioBackend` 提供默认“不支持”实现，避免破坏现有测试后端。
- `SoundDeviceBackend` 检查 Windows、sounddevice WASAPI 绑定和相关原生能力；即使发现部分接口，只要当前应用尚未接入并验证 Loopback 开流与时间戳映射，仍返回不支持。
- `SimulatedAudioBackend` 明确返回“离线模拟不提供真实 Software Loop”。
- `DeviceInventory` 在同一次后台扫描中携带该能力，不增加 UI 同步查询或第二个定时器；能力探测失败不得破坏普通设备枚举。
- 不修改普通输入/输出开流接口，不增加 `AudioDevice.is_loopback`，因为当前没有真实回环端点可供枚举。

### 2. 新增内嵌诊断面板，三种结果分开保存与解释

新增 `ui/diagnostics_panel.py`，复用现有深色卡片、标签、状态、进度和按钮样式：

- 提供 `Software Loop (WASAPI Loopback)`、`Electrical Loop (硬件电气环回)`、`Acoustic Loop (声学环回)` 三种模式。
- 面板持有独立 `TestController`，但每次诊断复用现有 `TestSessionWorker`，不新建音频 Worker。
- 每次诊断只测 1 次，补偿固定为 0、校准配置固定为空，并显示“原始延迟 / 未补偿”；普通测试页已有的补偿和校准不会作用于诊断。
- 三种模式各自只保留最新一次内存结果，切换模式不会串值；不提供保存，不写 SQLite，不进入历史、对比或普通测量统计。
- 分模式显示结果名称和含义：软件偏移、指定播放—采集设备的电气往返延迟、耳机—空气—麦克风声学链路延迟；不自动相减或据此推断未知时钟误差。
- 显示原始延迟、相关峰值、SNR、信号幅度、有效性、无效原因、测试配置、时间依据和未校准时钟限制；无结果显示“—”，无效结果保留并明确标注，不能以 0 或合成值代替。
- Electrical 显示接线、线路输入和增益/过载警告，明确其结果不是耳机声学延迟；Acoustic 显示外置麦克风、固定位置、环境噪声和完整声学路径说明。
- 对无信号、低 SNR、低相关、歧义峰、削波风险、采集超时、时间轴不连续及缺少 DAC/ADC 锚点给出对应的中文排查建议。
- 模拟后端下三种真实诊断均禁止启动；Software 不可用时可选中查看说明，但启动禁用且结果保持空态。

诊断配置通过 `dataclasses.replace` 从当前页面配置派生，保留实际设备、采样率、声道、warm-up、Chirp、质量阈值和采集窗口，仅覆盖单次测量与零补偿字段。

### 3. 嵌入现有测试页并保证音频操作互斥

修改 `ui/test_page.py`：

- 在参数卡之后、普通测量概览之前嵌入诊断面板，复用上方播放/录音设备及当前基础参数；不新增第五个导航页面或模态对话框。
- 后台设备扫描结果同步到面板；诊断活动时沿用现有规则暂停自动/手动设备扫描，结束后补扫一次。
- 普通测量、诊断测量和设备扫描入口互相检查活动状态，禁止并发开流或扫描重建 PortAudio。
- 诊断运行期间锁定设备、普通参数、诊断模式与配置；普通“停止”和诊断“停止”分别只操作自己的控制器。
- `shutdown()` 同时有界收尾两个控制器和设备枚举线程；保持现有固定底部操作区、滚动和设备热插拔选择恢复行为。
- 不修改 `core/test_controller.py`、结果模型、状态机、分析器、应用配置、主窗口导航或持久化层。

### 4. 自动化验证

先运行现有完整套件建立本轮基线，再实施修改；完成后运行聚焦测试和完整套件。

修改/新增覆盖：

- `tests/test_audio_backend.py`：默认/模拟/当前 sounddevice 能力降级、非 Windows、探测异常不影响普通枚举、`DeviceInventory` 能力传递；部分 API 存在也不能误判为应用已支持。
- `tests/test_test_controller.py`：复用 `_FakeDuplexBackend` 的已知 10 ms 延迟和 Qt 有界等待，验证 Electrical/Acoustic 单次诊断、一次开流、warm-up、DAC/ADC 时间依据、零补偿、两模式结果隔离、无效记录保留、停止释放和 Software 不支持时零开流/零伪造结果。
- `tests/test_main_window.py`：模拟后端禁用真实诊断、普通测量与诊断互斥、诊断期间禁止扫描与设备切换、收尾恢复、关闭线程清理、1100×720 滚动与固定 footer 回归、诊断不触发普通保存。

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe -m pytest tests/test_audio_backend.py tests/test_test_controller.py tests/test_latency_analyzer.py tests/test_audio_engines.py tests/test_audio_synchronizer.py tests/test_main_window.py -q
.\.venv\Scripts\python.exe -m compileall -q main.py version.py app audio core data ui tests
.\.venv\Scripts\python.exe -m app.audio_diagnostic --list-devices
```

测试只记录实际执行结果。确定性后端验证不冒充物理 Electrical/Acoustic 精度或真实 WASAPI Loopback 验收；不主动播放真实测试音，物理硬件步骤保留为人工验收。

### 5. 文档与进度收尾

- 按实际改动精简更新 `audio/AGENTS.md`、`ui/AGENTS.md` 的局部接口/互斥约定，并同步 `docs/ai/05_AUDIO_GUIDE.md` 的能力探测和不支持边界。
- 更新根 `Schedule.md`：增加独立 Loopback Diagnostics 工作包，不改写既有 P7 的 5/9 口径；分别记录面板与两种真实链路代码、自动化结果、Software 能力降级及未完成的硬件验证。
- 只有实际实现且验证通过的子项才勾选；Software 实际测量保持阻塞，Electrical/Acoustic 物理验收未完成时不得把整个功能标为已完成。
- 如实填写本轮测试命令与结果、80 ms 问题仍待定位的风险、未覆盖项和下一步；保留历史验证记录，不拿旧的 98/111 项结果冒充本轮结果。

## 关键复用点

- `audio/playback_engine.py`：连续播放、warm-up、绝对样本调度、DAC 播放时间戳。
- `audio/capture_engine.py`：连续采集、绝对样本起点、溢出/丢帧健康检查。
- `audio/stimulus_generator.py`：确定性 Chirp。
- `audio/audio_synchronizer.py`：DAC/ADC 锚点、样本时间映射和独立时钟限制。
- `core/latency_analyzer.py`：归一化互相关、相关峰、SNR、歧义和无效原因。
- `core/test_controller.py`：现有后台测量、停止、错误和资源释放全过程。

## 验收边界

完成后应能使用同一 UI 分别执行 Electrical 和 Acoustic 的真实诊断路径，并看到互不混淆的配置、延迟和质量解释；当前机器上的 Software 必须明确显示不可用且无法产生结果。物理线材/接口、电气过载、真实麦克风布置以及 80 ms+ 的最终归因仍需后续实测，不在自动化通过后被标记为已验收。
