# Software 与 Electrical 延迟链路排查计划

## Context

当前 Realtek Software Loop 三次结果均值为 19.434 ms，Electrical 模式使用 Realtek Stereo Mix 时约为 43 ms。需要验证这些数值到底由哪些代码路径产生，补齐设备、缓冲和健康状态证据，再用可重复实机测试检查算法与重复性。本轮只覆盖 Software 数字环回和 Stereo Mix 路径；物理 Line In/USB Electrical 与 Acoustic 不在本轮验收范围内。

计划阶段仅完成只读静态审查，尚未修改代码、运行自动测试或启动真实音频。

## 关键代码与数据流

1. `audio/audio_backend.py`、`audio/pyaudiowpatch_backend.py`：PortAudio/PyAudio 回调提供秒单位的 `outputBufferDacTime`、`inputBufferAdcTime`、`currentTime`。
2. `audio/playback_engine.py`：Chirp 按绝对播放样本调度，播放时间为 `DAC + 块内样本偏移 / sample_rate`。
3. `audio/capture_engine.py`、`audio/ring_buffer.py`：跨回调采集并保留全局样本起点。
4. `core/latency_analyzer.py`：`correlate(..., mode="valid")` 的峰索引表示参考 Chirp 起点在采集窗口中的 lag，抛物线插值仅修正峰位置。
5. `core/test_controller.py`、`audio/audio_synchronizer.py`：由 `capture_window_start_sample + lag` 映射到 ADC 时间，计算 `raw = capture_time - playback_time`，再且仅再减一次补偿。

预热只控制测量准入，lead 只移动调度位置，Chirp 长度只决定参考与采集长度，tail 只延长采集，回调等待和 FFT 计算耗时不额外叠加到结果。`currentTime` 仅用于遥测，没有代替 DAC/ADC 时间。静态推导未发现相关峰、单位或补偿的 off-by-one/重复计算。

## 已发现问题与证据

### 本次修复

1. **Software 的零时钟偏移依据不足。** `core/test_controller.py` 只比较 PyAudioWPatch 后端生成的共同 UUID，便传 `capture_clock_offset_seconds=0.0`。共同实例能支持相同 PortAudio 时间基准，但不能证明输出 DAC 与 Loopback ADC 管线偏移为零。现有 19.434 ms 应保留为时间戳差观测值，同时明确 `capture_clock_offset_uncalibrated`，不能宣称已校准绝对精度。
2. **实际流证据不足。** 当前只保留最新 DAC/ADC、帧数和累计回调数；没有形成包含请求格式、流采样率/延迟、回调大小范围、欠载/溢出、Ring Buffer 丢帧及设备信息的结构化报告。`wasapi_shared` 和 48 kHz 请求值不能冒充 Windows 端点混音格式或 APO 状态。
3. **短 lead 存在调度竞态。** `audio/playback_engine.py` 在回调期间仍把 `_frames_rendered` 保持为本块起点，调度线程可能把 Chirp 插入正在渲染的块，下一块再判为 `missed`。默认 100 ms lead 通常规避，但属于已确认的跨块边界缺陷，应修正并加回归测试。

### 保留的测量边界

- Electrical 已保留时钟偏移与漂移未校准限制；未知系统误差不能换算成可信的固定 ms 误差棒，也不能与 Software 结果直接相减归因。
- Stereo Mix 是驱动混音采集路径，本轮报告必须标为“Electrical 模式下的 Stereo Mix 观测”，不得勾成物理电气环回验收。
- 采集中途丢帧会直接终止会话而未形成当前单次无效记录；该项与本轮 20/43 ms 重复性验证无直接因果关系，列入后续风险，不在本轮顺带重构异常流程。

## 实施方案

### 1. 修正时间语义与调度边界

- `core/test_controller.py`：Software 保留共同时钟域关联检查，但改为 `capture_clock_offset_seconds=None`，保持 raw 数值算法不变并自动带上未校准限制。
- `audio/playback_engine.py`：记录正在渲染块的结束样本，调度时以“已提交位置与活动块结束位置的较大者”为基准，避免短 lead 插入当前块；不改变默认 lead 和正常测量数值。
- 更新 `tests/test_test_controller.py`、`tests/test_audio_engines.py`：Software 已知合成延迟仍保持原值但限制标记必须存在；增加活动回调内短 lead、非整块 Chirp/lag、不等采集块拼接和跨块定位测试。

### 2. 增加轻量音频诊断快照

修改 `audio/stream_common.py`、`audio/audio_backend.py`、`audio/pyaudiowpatch_backend.py`、`audio/playback_engine.py`、`audio/capture_engine.py`、`core/test_models.py`：

- 回调线程只更新整数/浮点字段：最新、最小、最大回调帧数、回调数、欠载/溢出状态；不增加日志、IO或动态容器。
- 控制线程读取 sounddevice 的 `samplerate`、`blocksize`、`latency` 和 PyAudioWPatch 的输入/输出延迟；区分 requested、PortAudio stream info、实际观察回调三类来源。
- 记录设备 ID/名称/HostAPI、请求采样率/声道/dtype/blocksize、流采样率/延迟、回调帧数范围、开流/启动次数、欠载/溢出、Ring Buffer 丢帧及最新 DAC/ADC/currentTime。
- 共享模式依据开流参数记录；Windows 端点混音格式、硬件实际缓冲和 APO/音效状态没有可靠接口时写 `unknown`，不猜测、不解析注册表伪造状态。
- Worker 在关闭引擎前采集会话级诊断，作为可选字段附到 `TestSessionSnapshot`；不改变 SQLite schema 和普通测量记录。

### 3. 新增可重复 headless 诊断入口

新增 `app/loopback_diagnostic.py`，复用 `DeviceService + TestController + StatisticsEngine`，不复制测量算法、不调用 UI 或 PersistenceService：

- 参数支持模式、输出/输入设备、采样率、blocksize、次数、输出目录及有界超时；设备 ID 只由命令行或当前配置传入，不写死。
- 每次会话写入用户数据目录 `logs/diagnostics/<时间-会话ID>/`：`records.jsonl` 保存逐次结果、质量、时间依据及限制，`summary.json` 保存配置、诊断快照、终态、错误和 mean/P50/P90/P95/min/max/std，无效记录另存 NPZ 原始波形。
- 超时采用 request_stop 与 bounded shutdown；开流、测量或收尾失败均保留报告并非零退出，不宣称通过。
- 新增 `tests/test_loopback_diagnostic.py` 覆盖参数、序列化、统计、无效波形和超时/失败收尾；更新后端与引擎诊断测试。

### 4. 实机验证矩阵

先枚举并核对 Realtek 输出与 Stereo Mix 输入，再固定 48 kHz、3 s warm-up、100 ms lead、零补偿及相同 Chirp/质量门限，顺序执行：

| 路由 | auto blocksize | 显式 480 帧 |
| --- | --- | --- |
| Software：Realtek WASAPI Render → 对应 Loopback | 20 次 | 20 次 |
| Electrical 模式：Realtek Playback → Realtek Stereo Mix | 20 次 | 20 次 |

若 480 帧开流失败，保留失败报告并记录能力限制；可另试 960 帧作为不同配置，但只有实际完成的组才计入对比。分别报告四组有效/无效数、原始观测统计、相关峰/SNR、回调与健康状态；重复性用 std/分位数描述，时钟偏移和漂移作为未定量的系统限制，不跨路由相减、不生成固定校准值。

### 5. 收尾记录

- 实际运行聚焦测试、完整 `pytest`、`compileall` 和差异检查；只报告真实执行结果。
- 更新 `Schedule.md`：记录诊断命令、设备、四组实测证据、unknown 项、失败/无效样本、残余风险和下一步；保留物理 Electrical 与 Acoustic 未完成状态，并修正“共同实例即零偏移”的旧表述。
- 按改动同步 `docs/ai/02_COMMANDS.md`、`docs/ai/05_AUDIO_GUIDE.md` 及命中的局部 AGENTS 约定，不提前标记实机验收完成。

## Verification

1. 离线：已知整数/分数数字延迟、非整块与跨块定位、短 lead 活动回调、Software 未校准限制、流诊断字段、批量报告与异常收尾。
2. 实机：完成上述至少 20 次/组矩阵，核对原始 JSONL 与汇总统计一致，并确认诊断未写 SQLite。
3. 结论仅覆盖本次实际路由和配置；物理 Electrical、Acoustic、端点混音格式/APO 原生核验及采集中途丢帧单次留痕继续列为未解决项。
