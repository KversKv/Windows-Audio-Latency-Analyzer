# 03 GOTCHAS - 踩坑记录

## PySide6 offscreen 测试中的工作线程信号等待

- **现象：** 在 PySide6 6.12 / offscreen 环境中，`QSignalSpy.wait(2000)` 可接收普通 `QTimer` 信号，但本项目的设备枚举、配置保存 queued 信号未在断言前分发；测试失败后还可能因窗口线程未清理而异常退出。
- **正确做法：** 使用 `QEventLoop` + 单发 `QTimer` 显式运行事件循环并设置超时；连续操作等待对应的 `finished` 信号，避免数据到达后线程尚未收尾的竞争。pytest fixture 必须在失败路径也执行窗口与线程清理。参考 `tests/test_main_window.py`。
