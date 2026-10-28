# 02 COMMANDS - 运行 / 测试 / 打包命令

在项目根目录执行；也可直接使用 `.venv\Scripts\python.exe`，无需激活虚拟环境。

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
python main.py
python main.py --simulate
python main.py --smoke-test
python -m app.audio_diagnostic --list-devices
python -m app.audio_diagnostic --warmup-seconds 3

# 环回批量诊断；设备索引仅为 Realtek 示例，执行前按实际枚举替换
python -m app.loopback_diagnostic --mode software --output-device 23 --sample-rate 48000 --blocksize 0 --count 20
python -m app.loopback_diagnostic --mode electrical --output-device 23 --input-device 26 --sample-rate 48000 --blocksize 480 --count 20
python -m pytest tests/ -q
python -m compileall -q main.py version.py app audio core data ui tests

# onedir：目录交付，启动较快
python -m PyInstaller spec/latency_analyzer.spec --clean --noconfirm
.\dist\WindowsAudioLatencyAnalyzer\WindowsAudioLatencyAnalyzer.exe --simulate --smoke-test
.\dist\WindowsAudioLatencyAnalyzer\WindowsAudioLatencyAnalyzer.exe --smoke-test

# onefile：单 exe 交付，启动时解压到临时目录
$env:LATENCY_ANALYZER_ONEFILE = "1"
python -m PyInstaller spec/latency_analyzer.spec --clean --noconfirm
Remove-Item Env:LATENCY_ANALYZER_ONEFILE
.\dist\WindowsAudioLatencyAnalyzer.exe --simulate --smoke-test
.\dist\WindowsAudioLatencyAnalyzer.exe --smoke-test
```

- Windows 环境通过 `requirements.txt` 安装 PyAudioWPatch，用于 Software Loop 的真实 WASAPI 数字环回；spec 会收集其 Python 模块、原生扩展与 DLL。
- `--simulate`：使用明确标注的离线模拟设备，不产生真实测量结果。
- `--smoke-test`：启动后自动退出，用于验证入口、主题资源、配置、数据库和资源初始化。
- `app.audio_diagnostic`：使用真实 sounddevice 后端验证独立播放/采集、连续背景音、预热和探针调度；设备参数可按 `--help` 覆盖，不计算声学延迟。
- `app.loopback_diagnostic`：按 `--count` 批量执行零补偿环回测量，默认写入用户 `logs/diagnostics/<时间-会话>/`；`--output-dir` 可改到源码树外。逐次更新 `records.jsonl` 与 `summary.json`，进度报告的 `report_finalized=false`，正常收尾后为 `true`；无效测量另存 NPZ 原始波形。`--blocksize 0` 表示自动，`--timeout-seconds` 限制会话等待。
- Electrical 模式选择 Stereo Mix 时，结果仅是所选播放到 Stereo Mix 采集的时间戳差，不得写成物理 Line In 电气环回；不同路由的结果不得直接相减归因于硬件。
- 默认 PyInstaller 命令输出 `dist\WindowsAudioLatencyAnalyzer\` onedir 目录，交付时必须连同 `_internal\` 一起复制。
- 设置 `LATENCY_ANALYZER_ONEFILE=1` 后，同一 spec 输出 `dist\WindowsAudioLatencyAnalyzer.exe` 单文件，交付时只需复制该 exe；onefile 启动时需要解压，通常比 onedir 慢。
- spec 使用项目根目录绝对路径并打入 `ui/styles/dark_theme.qss` 与 `ui/assets/` 下的运行时图像资源，EXE 图标使用 `ui/assets/app_icon.ico`。
- 冻结程序验证应设置 `LATENCY_ANALYZER_CONFIG_DIR` 指向临时目录，确认配置、SQLite 和日志不写入源码或交付目录；进程等待需有超时上限。
