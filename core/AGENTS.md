# core - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件仅约束核心服务与 Qt 工作线程边界。

- `DeviceService` 是 UI 与音频设备管理之间的同步 facade；UI 只能经 `DeviceEnumerationWorker` 调用，禁止在界面线程直接扫描设备。
- Worker 的 `succeeded` / `failed` / `finished` 必须成对设计：`finished` 无论成功或异常都发出，用于退出线程和恢复 UI；异常日志带 `exc_info=True`。
- `ConfigSaveWorker` 只执行调用方注入的保存回调；回调与工作线程内禁止访问控件，结果只能经 Qt 信号返回。
- QThread、Worker 的引用由调用方保留至收尾；连续操作等待 `finished`，关闭时采用有上限的等待，禁止销毁仍在运行的线程。
- 后续状态机、分析和统计放在本层；耗时数值处理使用专用 Worker，不得移入 UI 或 PortAudio 回调线程。
- `LatencyAnalyzer` 在分析线程执行归一化局部互相关；无效结果返回 `LatencyMeasurement.invalid_reason`，超范围延迟保留原值，不得改成静默丢弃。
- `OfflineLatencySimulator` 只生成明确标记 `is_simulation` 的合成分析；真实与模拟结果均须保留 `timing_basis` 和 `timing_limitations`。
- 修改线程协议后验证 `tests/test_main_window.py`；流程专题 @see docs/ai/07_FLOW_GUIDE.md、docs/ai/04_ARCHITECTURE.md。
