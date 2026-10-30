# 04 ARCHITECTURE - 架构与分层

## 依赖方向

```text
main.py → ui/ → core/ → audio/ → sounddevice / PortAudio
                   └──→ data/ → SQLite
```

- `ui/` 负责交互、状态呈现和发起请求，不执行音频枚举、分析或数据库访问。
- `core/` 负责会话状态机、线程编排、同步映射、延迟分析、统计和持久化外观。
- `audio/` 负责后端抽象、流生命周期、绝对样本调度、回调采集和流诊断；不依赖 UI、SQLite 或应用状态。
- `data/` 负责事务、schema、repository 和导出；不依赖 core、audio 或 UI。

## 线程边界

- UI 线程只发起操作并接收 Qt 信号；设备扫描、会话测量和持久化使用各自的 QThread/Worker。
- PortAudio 回调只搬运样本和轻量遥测。分析、统计、日志、磁盘访问和控件更新都在回调之外。
- 会话 Worker 拥有播放/采集流的创建、监控和释放。停止使用事件标记；采集轮询、warm-up、关闭等待有上限。
- QThread 与 Worker 引用保留到线程真正结束；关闭超时后窗口不得销毁仍运行的线程对象。

## 测量时间基准

- 播放 DAC 和采集 ADC 时间戳可能来自各自 stream-specific 的时间基准。只有后端明确证明二者具有相同时间原点时，才允许在该 `clock_domain` 中相减。本项目的 Windows WASAPI + PortAudio 19.7.0 根据 WASAPI 实现与 Windows QPC 映射声明共享域，其他版本/host API 不推定共享。
- `clock_domain=None` 表示未知。相同后端实例、设备名或 UUID 标签本身不构成共同时间原点证明。
- 未知或不一致的跨流时间基准必须使绝对延迟无效并置空；仍保存互相关峰、样本索引和原始波形用于诊断。不能把未校准的偏移默认为 0。
- 完整设备验证记录应覆盖后端版本、设备、采样率、缓冲、时间戳可用性、已知物理延迟、偏移/漂移修正和测量不确定度。

## 信号与持久化

```text
TestPage → TestController → TestSessionWorker
    ↑          ↑                 ├─ AudioBackend / engines
    └─ Qt signals ←───────────────└─ MeasurementRecord / snapshot
TestPage → PersistenceController → PersistenceService → SessionRepository
```

- 每个已完成测量（含无效测量）先进入会话记录；统计只读有效测量。
- 正常完成、用户停止或音频错误后的部分记录均可请求保存。数据库保存快照中的实际终态与全部明细；保存 UI 状态不覆盖会话终态。
- SQLite 变更经版本迁移；原始波形和 `invalid_reason` 不静默丢弃。
