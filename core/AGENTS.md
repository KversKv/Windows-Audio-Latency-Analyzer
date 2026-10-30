# core - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件仅约束核心服务与 Qt 工作线程边界。

- `DeviceService` 是 UI 与音频设备管理之间的同步 facade；UI 只能经 `DeviceEnumerationWorker` 调用，禁止在界面线程直接扫描设备。
- Worker 的 `succeeded` / `failed` / `finished` 必须成对设计：`finished` 无论成功或异常都发出，用于退出线程和恢复 UI；异常日志带 `exc_info=True`。
- `ConfigSaveWorker` 只执行调用方注入的保存回调；回调与工作线程内禁止访问控件，结果只能经 Qt 信号返回。
- QThread、Worker 的引用由调用方保留至收尾；连续操作等待 `finished`，关闭时采用有上限的等待，禁止销毁仍在运行的线程。`TestController` 的 `worker.finished → thread.quit` 使用 `DirectConnection`，确保 UI 有界等待期间无需先处理 queued 退出事件；线程完成槽内禁止同步清空 Python Worker，须用零延时 `QTimer` 在下一轮释放并校验实例。
- 状态机、分析和统计放在本层；耗时数值处理使用专用 Worker，不得移入 UI 或 PortAudio 回调线程。
- `LatencyAnalyzer` 在分析线程执行归一化局部互相关；无效结果返回 `LatencyMeasurement.invalid_reason`，超范围延迟保留原值，不得改成静默丢弃。独立播放/采集流只有提供经验证的共同时间原点后才能生成有效绝对延迟，否则保留波形并将延迟置空。
- `StatisticsEngine` 仅聚合有效测量且保留无效原因计数；`CalibrationManager` 只使用显式补偿值，所选配置必须严格匹配设备、采样率和音频模式。
- `OfflineLatencySimulator` 只生成明确标记 `is_simulation` 的合成分析；真实与模拟结果均须保留 `timing_basis` 和 `timing_limitations`。
- `PersistenceService` 是 core → data 的持久化外观，负责会话快照映射和明细统计重算；UI 只能经 `PersistenceController` 的串行 QThread 调用，禁止直接访问仓储。对比查询返回会话与重算统计的不可变组合，列表和明细使用专用信号避免跨页面串扰。
- `TestSessionConfig.session_id` 从测试开始到保存保持稳定，用于数据库防重复写入；保存 pending 期间不得替换或清空当前快照。音频错误结束但已有记录的部分会话允许保存，保留原错误终态。
- 修改线程协议后验证 `tests/test_main_window.py`，修改持久化协议后同时验证 `tests/test_persistence.py` 与 `tests/test_comparison.py`；流程专题 @see docs/ai/07_FLOW_GUIDE.md、docs/ai/04_ARCHITECTURE.md。
