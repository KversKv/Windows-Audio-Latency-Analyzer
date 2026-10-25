# 03 GOTCHAS - 踩坑记录

## PySide6 offscreen 测试中的工作线程信号等待

- **现象：** 在 PySide6 6.12 / offscreen 环境中，`QSignalSpy.wait(2000)` 可接收普通 `QTimer` 信号，但本项目的设备枚举、配置保存 queued 信号未在断言前分发；测试失败后还可能因窗口线程未清理而异常退出。
- **正确做法：** 使用 `QEventLoop` + 单发 `QTimer` 显式运行事件循环并设置超时；连续操作等待对应的 `finished` 信号，避免数据到达后线程尚未收尾的竞争。pytest fixture 必须在失败路径也执行窗口与线程清理。参考 `tests/test_main_window.py`。

## PyInstaller spec 位于子目录时的资源路径

- **现象：** 从项目根目录执行 `python -m PyInstaller spec/latency_analyzer.spec` 时，spec 中的 `main.py` 被解析为 `spec/main.py`，导致入口不存在。
- **正确做法：** 用 `Path(SPECPATH).parent` 构造入口、`pathex` 和 QSS 数据文件的项目根绝对路径；运行时读取的 `dark_theme.qss` 必须列入 `datas`。构建后分别以模拟和真实后端执行冻结 exe 的 `--smoke-test`，并用 `LATENCY_ANALYZER_CONFIG_DIR` 隔离配置、数据库和日志。
