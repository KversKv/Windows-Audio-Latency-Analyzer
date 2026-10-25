# Windows Audio Latency Analyzer

基于 Python 3.12 + PySide6 + sounddevice/PortAudio + NumPy/SciPy 的 Windows 桌面应用：在连续音频流中插入 Chirp 激励，用外接有线麦克风采集声学响应并做互相关分析，测量耳机（有线 / USB / 2.4G / 蓝牙）的端到端延迟。结果持久化于 SQLite，支持历史查询、多会话对比与 CSV/Excel 导出。

## 当前状态

P0（项目骨架与 UI 基础）和 P1（音频引擎与连续流）已完成：应用支持设备独立枚举、JSON 配置、连续背景播放、采集环形缓冲、预热及按样本索引调度探针；真实 WASAPI 播放/采集链路诊断已通过。Chirp 延迟分析、测试控制器、数据库与图表将在 P2 及后续阶段接入，当前诊断结果不代表声学延迟测量。

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
│   └── styles/         # 深色主题 QSS
├── core/               # UI 工作线程封装；后续接入测试编排与分析
├── audio/              # 后端抽象 / 连续播放 / 采集 / 背景音 / 环形缓冲
├── data/               # SQLite / repository / 导出（后续阶段）
├── tests/              # 单元测试 / 启动与 UI 验证
├── logs/               # 本地日志目录（不入库）
└── docs/ai/            # AI 协作深度文档
```

分层依赖单向：`main.py → ui/ ⇄ core/ → audio/ → sounddevice/PortAudio`，`core/ → data/`；`audio/` 与 `data/` 互不依赖。

## 环境准备

项目自带虚拟环境 `.venv/`（根目录），命令前先激活：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 运行

```powershell
python main.py
```

无物理音频设备时，可明确以模拟模式检查界面：

```powershell
python main.py --simulate
```

默认配置写入 `%APPDATA%\WindowsAudioLatencyAnalyzer\config.json`，运行日志写入同目录的 `logs/`。测试或便携运行可通过 `LATENCY_ANALYZER_CONFIG_DIR` 环境变量指定独立配置目录。后端设置在下次启动后生效。

### 音频链路诊断

真实设备诊断使用配置中的设备和采样率，可用参数覆盖；先枚举设备，再执行连续流诊断：

```powershell
python -m app.audio_diagnostic --list-devices
python -m app.audio_diagnostic --warmup-seconds 3
```

可通过 `--output-device`、`--input-device` 和 `--sample-rate` 指定设备与采样率。诊断只验证连续播放、采集、预热和探针调度，不计算声学延迟；模拟后端不提供实时流。

## 测试

```powershell
python -m pytest tests/
```

## 打包

```powershell
python -m PyInstaller spec/latency_analyzer.spec --clean --noconfirm
```

## 开发路线

- Phase 1 项目基础：结构 / 主窗口 / 深色主题 / 导航 / 设备枚举 / 配置管理
- Phase 2 音频引擎：播放 / 采集 / 连续背景播放 / 激励调度 / 流生命周期
- Phase 3 延迟分析：Chirp / 互相关 / 时间戳映射 / 补偿 / 质量指标 / 离线模拟
- Phase 4 UI 集成：测试页 / 实时延迟显示 / 波形可视化 / 进度 / 安全启停
- Phase 5 数据持久化：SQLite / 会话与单次结果 / 历史页 / CSV 导出
- Phase 6 对比与打磨：对比图表 / 分布图 / 校准配置 / 错误处理 / 打包

## 备注

- 版本号唯一事实源为 `version.py`，文档与代码中不写死版本号字符串。
- 数据库文件、日志、导出产物（db / log / csv / xlsx）均不纳入 git 追踪。
- 模拟模式结果在 UI 与导出中会明确标注，不冒充真实测量。
