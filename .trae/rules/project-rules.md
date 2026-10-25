# Windows Audio Latency Analyzer — TRAE 规则

## 0. 前置义务
**开工前必读** [AGENTS.md](../../AGENTS.md)；按 docs/ai 分发表取细则。
按 AGENTS.md「加载决策 SOP」就近读命中子模块的 `AGENTS.md`，禁通读全量。

## 1. 栈
Windows 桌面 / PySide6 Qt Widgets · Python 3.12+ · sounddevice/PortAudio、NumPy/SciPy、SQLite、pyqtgraph · PyInstaller。分层 `main.py→ui/↔core/→audio/→sounddevice/PortAudio`，`core/→data/`；依赖单向，`audio/` 与 `data/` 互不依赖。

## 2. 硬红线
- 禁 `print()`，统一 `logging.getLogger(__name__)`（或 `log_config.get_logger(__name__)`）；异常日志带 `exc_info=True`，禁裸 `except`。
- 分层单向：`ui/→core/→audio/`，`core/→data/`；`audio/`、`data/` 禁 import UI / PySide6 / 应用状态；UI 禁阻塞 IO 与直接访问 `data/`，分别走 `core/` 的 QThread / Worker + Qt 信号和持久化封装。
- 音频设备访问统一走 `audio/audio_backend.py`；必须提供离线模拟后端；设备名、采样率、补偿值等禁硬编码，统一走配置。
- 对话框必传 `parent`；确认 / 取消按钮显式二元化（default / autoDefault）。
- 中文一律简体；不增删无关注释；不主动 `git commit`。
- 版本号唯一事实源为 `version.py`，禁写死版本号字符串，与 git 解耦。
- DEBUG 临时文件统一放 `tests/`；数据库、日志及导出产物禁入 git 追踪路径。
- 外部 IO 必须设超时；长等待 / 轮询必须有上限与退避策略，禁无界阻塞。
- PortAudio 回调线程禁 UI 操作、数据库写入、磁盘 IO、重数值计算、阻塞等待与不必要内存分配。
- 播放流在整个测量会话期间保持打开；正式测量前必须 warm-up（默认 3 s，可配 1–15 s），测量间保持背景音播放。
- 禁伪造音频时间戳、补偿值与测量数据；播放时间戳须由流时间戳 + 样本索引映射得出。
- 无效测量保留原始数据并记录 `invalid_reason`；统计仅用有效测量，禁静默删除有效离群值。
- 模拟结果须在 UI / 导出中明确标注；无校准配置时补偿为 0 并显示“未补偿”。
- SQL 统一参数化；schema 变更必须带版本号迁移；禁只存聚合统计而丢原始单次结果。

## 3. 触发式必读
音频→05+04+03；页面→06+01+03；流程/分析→07+04+08；数据库/导出→04+01+03；打包→03+08+02；报错→03+09+memory；架构→04+00+decisions；依赖→02+08；版本→10；开工→09+08。

## 4. 同步
硬红线/分层→根 `AGENTS.md`、各子模块 `AGENTS.md` 与本文件；目录/结构→根 `AGENTS.md` 子模块地图与 `README.md`；运行时资源→打包配置（`spec/`）；依赖→`requirements.txt`；决策→`docs/ai/decisions/`；上下文→`docs/ai/memory.md`；新坑→`docs/ai/03_GOTCHAS.md`；模块约定→对应层级 `AGENTS.md`；改动收尾→`docs/ai/08_CHECKLISTS.md` 同步矩阵。
