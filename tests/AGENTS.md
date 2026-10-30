# tests - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件约束自动化测试与临时产物。

- 默认测试必须离线、确定性运行，不依赖物理声卡；真实设备验证使用独立冒烟步骤，不得冒充默认单元测试结果。
- Qt 测试在导入 PySide6 前设置 `QT_QPA_PLATFORM=offscreen`，共享 `QApplication`，并在 fixture 的失败路径也关闭窗口、等待线程和释放对象。
- 工作线程 queued 信号使用显式 `QEventLoop` + 有上限的单发 `QTimer` 等待；连续操作等待 `finished`，不要依赖本项目中已失效的 `QSignalSpy.wait()` 用法。详见 docs/ai/03_GOTCHAS.md。
- 子进程必须设置 `timeout`；启动测试通过 `LATENCY_ANALYZER_CONFIG_DIR` 指向 `tmp_path`，禁止写入真实用户配置。
- DEBUG 临时文件仅放本目录，测试结束删除配置、日志和导出产物；不得为让测试通过而删除异常样本或失败断言。
- 音频算法使用已知延迟、噪声和采样率的合成数据；容差必须来自已验证实现，不得预先宣称未验证精度。
- 真实流 fake backend 若要覆盖有效绝对延迟，必须显式提供同一时间原点的 `clock_domain`；仅使用同一个 backend/PyAudio 实例不构成共享时基证明。
