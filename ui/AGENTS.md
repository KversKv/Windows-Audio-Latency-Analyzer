# ui - AI Agents 局部约定

> 继承根 `AGENTS.md`；本文件覆盖主窗口、四页面、`widgets/` 与 `styles/`。

- `MainWindow` 只负责页面组装、导航和跨页面信号；页面内部交互留在对应页面文件，禁止把业务逻辑上移到 `main.py`。
- UI 不直接 import audio / data，也不直接调用 sounddevice；设备访问经 `core.device_service`，配置保存经 `core.config_worker`，历史持久化与导出经 `core.persistence_controller`。
- `app.config` 仅作为纯配置模型共享；界面线程不得直接调用其文件读写。设备枚举与配置保存期间要禁用重复操作，并以 `finished` 恢复状态。
- 未接入的测量、历史、对比功能必须保持禁用或空状态，禁止展示虚构数值；模拟后端始终显示“非真实测量”提示。
- 深色样式集中在 `styles/dark_theme.qss`，通过 `ui.styles.load_dark_theme()` 加载；新增运行时资源须纳入 P7 打包核对。
- `QLabel` 默认背景保持透明；SpinBox 按钮与 SVG 箭头须显式设置子控件尺寸，资源路径由主题加载器解析，禁依赖原生箭头的高分屏表现。
- 对话框必须传 `parent`，确认/取消按钮显式设置 default / autoDefault；中文一律简体。
- 对比图表仅使用有效单次明细；统计柱状图纵轴包含 0，无有效样本显示空态而非 0，显示精度不得冒充测量精度。
- 修改页面或线程交互后验证 `tests/test_main_window.py`，历史页同时验证 `tests/test_persistence.py`，对比页同时验证 `tests/test_comparison.py`；页面专题 @see docs/ai/06_PAGE_GUIDE.md、docs/ai/03_GOTCHAS.md。
