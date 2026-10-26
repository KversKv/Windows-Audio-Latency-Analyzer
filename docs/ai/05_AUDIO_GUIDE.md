# 05 AUDIO GUIDE - 音频子系统指引

## P1 已实现接口

- `audio_backend.py`：唯一 sounddevice 入口。`AudioBackend` 定义设备枚举及独立输入/输出开流；Windows/WASAPI 枚举先经 `PaWasapi_UpdateDeviceList` 更新原生设备表。默认 PortAudio 构建未启用动态设备表时该接口返回 `-9986`，后端仅在无活动流、扫描单飞且未开始测量时通过 sounddevice 终止并重新初始化 PortAudio 作为兼容回退；禁止在活动会话中重启 PortAudio。`AudioStream` 定义启动、停止、关闭和健康状态。后端将 PortAudio 回调参数映射为 `AudioCallbackTime` / `AudioStreamStatus`，后端异常转为 `AudioBackendError`。
- `playback_engine.py`：整个会话只打开并启动一次播放流。背景音连续输出，`schedule_stimulus()` 使用绝对 `start_sample`；重叠或已过期的调度会被拒绝，错过回调位置会记录错误而不是伪造播放时间。
- `capture_engine.py`：独立输入流连续采集到 `AudioRingBuffer`，控制线程读取样本。输入溢出、缓冲丢帧和意外停流通过 `check_health()` 报告。
- `background_generator.py`：初始化时预生成确定性的带限噪声或粉红噪声，回调仅拷贝循环缓冲；默认配置集中在 `app.config.AudioConfig`。
- `stream_common.py`：保存最近回调的样本索引、累计样本数及 ADC/DAC 时间戳。`ScheduledStimulus.playback_time_seconds` 由 `outputBufferDacTime + 块内偏移 / 采样率` 得出；时间不可用则为 `None`，禁止用 Python 调用时间替代。

## 回调与生命周期约束

- 正常回调不执行日志、磁盘 IO、UI、数据库或数值分析；Ring Buffer 在回调侧以非阻塞方式取锁，冲突帧计入丢弃计数。
- `open()` 与 `start()` 分离；正式调度要求播放流运行且完成 1–15 秒预热。`close()` 幂等，停止并释放流。
- 引擎不自动重连。设备断开或音频状态错误后，由上层安全关闭并创建新会话，避免沿用不连续样本时间轴；设备表自动刷新只在非活动会话中经后台 Worker 执行，会话收尾后再补扫，不得换设备续测。
- 连续流、预热和非零背景音是测试条件，不代表完全消除蓝牙低功耗行为。

## 诊断与边界

```powershell
python -m app.audio_diagnostic --list-devices
python -m app.audio_diagnostic --warmup-seconds 3
```

诊断只验证真实播放/采集链路、样本计数、探针调度和时间戳可用性，不计算声学延迟。当前 `SimulatedAudioBackend` 只提供明确标注的虚拟设备，不提供实时流；P2 的合成延迟与离线分析不得冒充真实设备测量。

### Loopback Diagnostics 当前边界

- Electrical / Acoustic 复用现有连续双工流、Chirp、DAC/ADC 时间戳映射和互相关分析；面板强制单次、零补偿、无校准，结果只保留在内存中，不进入历史统计。独立采集时钟偏移与采样率漂移未校准时仍保留限制标记。
- Software Loop 当前只提供运行时能力检测与不可用原因，不提供真实 WASAPI 回环测量。当前捆绑 sounddevice / PortAudio 缺少完整 Loopback 接口；即使接口齐全，在回环采集与时间戳映射接入并验证前也必须保持 `supported=False`，不得回退到 Stereo Mix、麦克风或模拟结果。
- CFFI 查询未导出的原生符号可能抛 `ffi.error` 而非 `AttributeError`，应按预期能力缺失处理；普通设备枚举不得因此失败。

## P2 已实现接口

- `stimulus_generator.py`：预生成确定性、只读的单声道 Chirp；采样率、频段、时长、窗函数和峰值幅度由调用方从配置注入。
- `audio_synchronizer.py`：以 DAC/ADC 时间戳和样本索引建立播放/采集映射，支持显式时钟偏移和 ppm 漂移修正。未提供的校准值保持 `None` 并写入限制标记，不自动估计或宣称样本级同步。
- `CaptureEngine.read_*_with_metadata()`：保留原数组读取接口，同时提供采集块绝对样本起点；丢帧后时间位置不可继续假定连续。
- `core/latency_analyzer.py`：在局部采集窗口内做归一化滑动互相关、抛物线亚采样插值、幅度/噪声/SNR 与质量判定；结果包含 raw/compensated latency、有效性、无效原因和时间限制。
- `core/offline_simulation.py`：以已知分数样本延迟、增益和噪声生成离线分析输入，结果及波形明确标记为模拟，不生成实时音频流或真实设备时间戳。
