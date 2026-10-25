# Windows Audio Latency Analyzer

基于 Python 3.12 + PySide6 + sounddevice/PortAudio + NumPy/SciPy 的 Windows 桌面应用：在连续音频流中插入 Chirp 激励，用外接有线麦克风采集声学响应并做互相关分析，测量耳机（有线 / USB / 2.4G / 蓝牙）的端到端延迟。结果持久化于 SQLite，支持历史查询、多会话对比与 CSV/Excel 导出。

## 当前状态

P0–P6 已完成。P7 的自动化集成回归、真实 WASAPI 连续流诊断、PyInstaller onedir 打包及冻结程序启动已验证；完整真实声学延迟、各类物理设备和物理拔插仍须在相应硬件与声学布置下验收，阶段状态以 `Schedule.md` 为准。应用支持设备独立枚举、连续背景播放、预热、非阻塞测量、双波形与状态显示；统计仅使用有效样本，同时保留全部原始记录，并列展示原始与补偿后延迟。SQLite schema v1 保存会话、校准快照和全部单次结果，历史页支持搜索、筛选、排序、详情、备注、删除及 CSV/Excel 导出；校准编辑配置存入 JSON。

## 目标特性

- 连续流测量：播放流在整个测量会话期间保持打开，避免把蓝牙"冷启动"延迟计入结果
- Chirp 激励 + 滑动互相关 + 亚采样峰值插值，抗背景噪声与麦克风增益变化
- 区分原始延迟 / 补偿值 / 补偿后延迟，支持校准配置（Calibration Profile）
- 显式测试状态机，设备断开、采集溢出、播放欠载等错误不导致崩溃
- 离线模拟模式：无物理音频设备也可开发调试 UI 与分析流水线
- SQLite 持久化：保留每次原始测量（含无效测量及原因），不只存聚合统计
- 历史查询、多会话对比图表（pyqtgraph）、CSV / Excel 导出

## 技术栈

| 类别 | 选型 |
|---|---|
| 语言 | Python 3.12+ |
| UI | PySide6 (Qt Widgets) + pyqtgraph |
| 音频 | sounddevice / PortAudio（优先 WASAPI 共享模式） |
| 数值计算 | NumPy / SciPy |
| 持久化 | SQLite |
| 打包 | PyInstaller |

## 项目结构

```
├── main.py             # 入口，仅调用应用组装层
├── requirements.txt    # 项目与测试依赖
├── version.py          # 版本号唯一事实源
├── log_config.py       # 日志配置
├── app/                # 应用组装 / JSON 配置管理
├── ui/                 # 四大功能页（测试 / 历史 / 对比 / 设置）+ 主窗口
│   ├── widgets/        # 通用控件
│   ├── assets/         # 应用图标（SVG / PNG / ICO）
│   └── styles/         # 深色主题 QSS
├── core/               # 测试编排 / 延迟分析 / 统计 / 校准 / UI 工作线程
├── audio/              # 后端抽象 / 连续播放 / 采集 / 背景音 / 环形缓冲
├── data/               # SQLite schema / repository / CSV 与 Excel 导出
├── tests/              # 单元测试 / 启动与 UI 验证
├── spec/               # PyInstaller 打包配置（含运行时 QSS 与应用图标）
├── logs/               # 本地日志目录（不入库）
└── docs/ai/            # AI 协作深度文档
```

分层依赖单向：`main.py → ui/ ⇄ core/ → audio/ → sounddevice/PortAudio`，`core/ → data/`；`audio/` 与 `data/` 互不依赖。

## 源码安装与运行

需要 Windows 和 Python 3.12+。在项目根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
python main.py
```

无物理音频设备时，可明确以模拟模式检查界面与分析流程：

```powershell
python main.py --simulate
```

默认配置为 `%APPDATA%\WindowsAudioLatencyAnalyzer\config.json`，数据库为同目录的 `latency_analyzer.db`，日志位于同目录的 `logs/`。测试或便携运行可通过 `LATENCY_ANALYZER_CONFIG_DIR` 指定独立数据目录。后端切换在下次启动后生效。

## Windows 交付包

从源码构建 onedir 交付包：

```powershell
python -m PyInstaller spec/latency_analyzer.spec --clean --noconfirm
```

输出到 `dist\WindowsAudioLatencyAnalyzer\`。交付或复制时必须保留整个目录及 `_internal\`，运行其中的 `WindowsAudioLatencyAnalyzer.exe` 即可，无需另装 Python。该交付物是便携目录，不是单文件安装程序。

可用自动退出的冒烟模式检查交付包；`--simulate` 验证离线流程，不带该参数则验证真实 PortAudio 后端初始化：

```powershell
.\dist\WindowsAudioLatencyAnalyzer\WindowsAudioLatencyAnalyzer.exe --simulate --smoke-test
.\dist\WindowsAudioLatencyAnalyzer\WindowsAudioLatencyAnalyzer.exe --smoke-test
```

## 设备设置

1. **输出设备**选择待测耳机，**录音设备**选择独立的有线麦克风或音频接口；不要把同一蓝牙耳机切换到通话模式后的麦克风当作独立声学采集链路。
2. 在 Windows 与应用中选择设备支持的采样率，并记录系统音效、空间音频、驱动 DSP、增强功能和音量状态。这些环节可能改变延迟；对比时应保持条件一致。
3. 将麦克风固定在耳机发声单元对应的测量位置，保持每次距离、角度、佩戴/密封方式一致。先从低输出幅度开始，避免削波、听力损伤和麦克风过载；测试时不要直接佩戴耳机听取激励。
4. 蓝牙设备应先完成配对并稳定连接，再开始测试。默认预热 3 秒，预热及两次测量之间保持连续背景音；不要为单次测量手动重启播放流。
5. 若出现无声、爆音、输入溢出或输出欠载，应停止测试并检查设备占用、连接、采样率、音量和增益，不要将异常会话当作有效基准。

## 测量步骤

1. 在测试页刷新设备，分别选择播放与录音设备，确认后端和设备状态。
2. 设置测试次数、预热时间和输出幅度。未使用可追溯校准时保持补偿为 0 ms，界面会明确显示“未补偿”。
3. 开始测试并等待连续流预热完成。每次结果均检查有效性、SNR、相关系数和无效原因，不要只看平均值。
4. 停止会安全收尾并保留已完成样本；保存会话后可在历史页查看单次明细、编辑备注或导出 CSV/Excel。
5. 对比页可选择多条会话并切换原始/补偿后口径。只有在声学布置、音频条件和补偿定义可比时，结果差异才有解释意义。

## 结果解释

- **原始延迟**：由播放 DAC 与采集 ADC 流时间锚点、样本索引和相关峰值映射计算，包含播放链路、耳机电声转换及麦克风采集链路；不把 Python 调用时间当作播放时刻。
- **补偿后延迟**：`原始延迟 - 用户补偿值`。补偿只来自用户显式输入或所选校准配置；0 ms 表示未补偿，不表示延迟为零。
- **统计口径**：平均、P50、P90、P95、最小、最大和总体标准差仅使用有效样本。无效样本及其原因、原始波形和有效离群值均保留，不会静默删除。
- **质量指标**：SNR 越高、相关系数越接近 1 通常越可靠；低 SNR、低相关、峰值歧义、超出延迟范围或时间戳不完整会使样本无效。阈值通过不代表绝对精度已获校准。
- **重复性**：标准差和分布反映本次条件下的离散程度，不等于系统误差或绝对准确度；样本过少时，高分位数不具备稳定代表性。

## 已知限制

- 测量是端到端声学延迟，不是单独的蓝牙编码延迟；麦克风与发声单元之间的空气传播时间也包含在结果中。
- 独立播放/采集设备的时钟偏移与采样率漂移目前未自动估计或校准。手动补偿不会消除这些限制，绝对精度需要额外参考链路和外部校准验证。
- 连续流和预热可减少冷启动影响，但不能保证完全消除蓝牙或驱动的低功耗行为；系统切换音频 Profile 也可能改变链路。
- Windows 共享模式、驱动缓冲、系统音效、设备 DSP、环境噪声、反射、麦克风位置和增益都会影响结果。跨设备比较必须记录并控制这些条件。
- 模拟结果仅用于界面和算法流程验证，在 UI 与导出中明确标注，不能作为真实设备性能数据。真实硬件验收覆盖范围见 `Schedule.md`。

## 音频链路诊断

真实设备诊断使用配置中的设备和采样率，可用参数覆盖；先枚举设备，再执行连续流诊断：

```powershell
python -m app.audio_diagnostic --list-devices
python -m app.audio_diagnostic --warmup-seconds 3
```

可通过 `--output-device`、`--input-device` 和 `--sample-rate` 指定设备与采样率，其他参数见 `--help`。诊断只验证连续播放、采集、预热和探针调度，不计算声学延迟；模拟后端不提供实时流。

## 测试

```powershell
python -m pytest tests/
```

## 开发路线

- Phase 1 项目基础：结构 / 主窗口 / 深色主题 / 导航 / 设备枚举 / 配置管理
- Phase 2 音频引擎：播放 / 采集 / 连续背景播放 / 激励调度 / 流生命周期
- Phase 3 延迟分析：Chirp / 互相关 / 时间戳映射 / 补偿 / 质量指标 / 离线模拟
- Phase 4 UI 集成：测试页 / 实时延迟显示 / 波形可视化 / 进度 / 安全启停
- Phase 5 数据持久化：SQLite / 会话与单次结果 / 历史页 / CSV 导出
- Phase 6 对比与打磨：对比图表 / 分布图 / 校准配置 / 错误处理
- Phase 7 集成与交付：真实链路验证 / 异常释放 / Windows 打包 / 使用与限制说明

## 备注

- 版本号唯一事实源为 `version.py`，文档与代码中不写死版本号字符串。
- 数据库文件、日志、导出产物（db / log / csv / xlsx）均不纳入 git 追踪。
- 模拟模式结果在 UI 与导出中会明确标注，不冒充真实测量。
