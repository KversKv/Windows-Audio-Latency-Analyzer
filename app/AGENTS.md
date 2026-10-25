# app - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件仅约束应用组装与配置管理。

- `application.py` 负责 CLI、日志、QApplication、后端与主窗口组装；`main.py` 只调用 `run()`，禁止堆入业务逻辑。
- `config.py` 保持纯 Python，不导入 PySide6、UI、audio 或 data；配置模型使用不可变 dataclass，跨层共享时只传值，不传可变应用状态。
- 配置默认值集中在 `AudioConfig` / `MeasurementConfig` / `CalibrationConfig` / `UiConfig`，其他模块不得另建设备名、采样率、预热时间、补偿值等默认事实源。
- `ConfigManager.load()` 对缺失或无效配置返回默认值并记录日志；`save()` 使用同目录临时文件替换，UI 调用保存必须经 `core/` 工作线程。
- 默认配置与日志落在用户目录；测试通过 `LATENCY_ANALYZER_CONFIG_DIR` 隔离，禁止读写开发者的真实配置。
- 修改组装或配置后，至少验证 `tests/test_config.py` 与 `tests/test_startup.py`；命令同步 @see docs/ai/02_COMMANDS.md。
