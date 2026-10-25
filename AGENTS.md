# Windows Audio Latency Analyzer - AI Agents 协作指引（通用入口 · 路由器）

> 本文件是 Windows Audio Latency Analyzer 全项目 **AI 协作通用指引（硬红线为通用铁律）**，同时承担路由职责：加载决策 SOP + 硬红线 + 子模块地图 + 分发索引。
> 深度知识按需从 docs/ai/ 与子模块 AGENTS.md 拉取，**禁止预防性通读全量文档**。

---

## 1. 加载决策 SOP（每次任务第一步，禁止跳过）

1. **判定任务涉及的【子模块路径】**（如 `ui/`、`core/`、`audio/`、`data/`）。
2. **读取命中路径的 `AGENTS.md`**（若存在），并**向我报告**：`已读 <路径>/AGENTS.md`。
   - 子模块 AGENTS.md 加载规则：**就近继承**——沿路径从上到下叠加（如改 `ui/test_page.py` → 依次读 `ui/AGENTS.md`（若有）），下层覆盖上层同名约定。
3. **查下方【分发索引表】**，加载命中行的"必读"深度文档（docs/ai/）。
4. 子模块 AGENTS.md 中出现 `@see docs/ai/xx` 时，**按需追加**加载该专题。
5. 仍无匹配 → 默认 `09_WORKFLOW` + `03_GOTCHAS`。
6. **全程只加载"命中"文档**，禁止一次性通读 docs/ai/ 或所有子模块 AGENTS.md。

### 收尾维护规则（任务结尾）

- **只维护本次真正改动所在的那一级 `AGENTS.md`**（不逐级、不全量）。修改要求明确、精简；只保留要求或注意的点，不详细展开。
- 维护内容仅限：本模块新增的局部约定 / 新踩的坑 / 接口契约变更；跨模块坑写入 `docs/ai/03_GOTCHAS.md`。
- 同步核对 `docs/ai/08_CHECKLISTS.md` 的【同步矩阵】。

---

## 2. 硬红线（贯穿全项目，任何任务都适用）

1. 禁 `print()`，统一 `logging.getLogger(__name__)`（或项目封装的 `log_config.get_logger(__name__)`）；异常日志带 `exc_info=True`，禁裸 `except`。
2. 分层：`main.py → ui/ ←→ core/ → audio/ → sounddevice/PortAudio`；`core/ → data/`。依赖单向、禁反向跨层：
   - UI 层禁阻塞 IO，走 `core/` 的 QThread / Worker + Qt 信号回调；
   - `audio/` 与 `data/` 为最底层，互不依赖，均禁 import UI / PySide6 / 应用状态；
   - UI 不直接访问 `data/`，持久化经 `core/` 封装。
3. 音频设备访问统一走 `audio/audio_backend.py` 抽象；必须提供离线模拟后端（simulation mode）以便无设备开发；设备名、采样率、补偿值等禁硬编码，统一走配置。
4. 对话框必传 `parent`；确认 / 取消按钮显式二元化（default / autoDefault）。
5. 中文一律简体；不增删无关注释；**不主动 `git commit`**。
6. 版本号唯一事实源 = `version.py`，禁止写死版本号字符串，与 git 解耦。
7. DEBUG 用临时文件禁放根目录，统一放 `./tests/`。
8. 数据产物 / 二进制文件（db / csv / xlsx / log 等）禁止保存在 git 追踪路径；SQLite 库与 `logs/` 统一落 `.gitignore` 已覆盖目录。
9. 所有外部 IO 调用必须设超时；长等待 / 轮询必须给出上限与退避策略，禁止无界阻塞。
10. PortAudio 回调线程内禁止：操作 UI 控件、数据库写入、磁盘 IO、重数值计算（互相关走专用 analysis worker）、阻塞等待其他线程、不必要内存分配。
11. 播放流在整个测量会话期间保持打开，禁止为单次测量反复开关流；正式测量前必须经 warm-up（默认 3s，可配 1–15s），两次测量之间保持背景音播放。
12. 禁止伪造音频时间戳 / 补偿值 / 测量数据：播放时间戳须由流时间戳 + 样本索引映射得出，禁把 `time.perf_counter()` 调用点当作实际声学播放时刻。
13. 无效测量必须保留原始数据并记录 `invalid_reason`，禁止静默丢弃；统计仅用有效测量；禁止静默移除有效离群值。
14. 模拟模式结果必须在 UI / 导出中明确标注，禁止冒充真实测量；无校准配置时补偿为 0 并明确显示"未补偿"。
15. SQL 统一参数化查询；数据库 schema 变更必须带版本号迁移，禁止只存聚合统计而丢原始单次结果。

---

## 3. 项目一句话概述

**Windows Audio Latency Analyzer** 是基于 Python 3.12 + PySide6 + sounddevice/PortAudio + NumPy/SciPy 的 Windows 桌面应用：在连续音频流中插入 Chirp 激励、用外接有线麦克风采集声学响应并做互相关分析，测量耳机（有线 / USB / 2.4G / 蓝牙）的端到端延迟，结果持久化于 SQLite，支持历史查询、多会话对比与 CSV/Excel 导出。

## 4. 分层（铁律）

```

main.py → ui/ ←→ core/ → audio/ → sounddevice / PortAudio
                  └────→ data/ (SQLite)
                      ↑
          log_config / 公共配置 / version.py

```

> 单向依赖：`ui → core`；`core → audio`、`core → data`；`audio` 与 `data` 互不依赖。
> 详细边界见 [docs/ai/04_ARCHITECTURE.md](./docs/ai/04_ARCHITECTURE.md)。

---

## 5. 子模块地图（路由用 · 定位任务落在哪）

> 判定任务子模块路径后，必须就近读取已存在的局部 `AGENTS.md`；未建文件的目录继承最近上层约定。

| 子模块路径 | 职责 | 局部 AGENTS.md | 深度专题 @see |
|---|---|---|---|
| `ui/`（test / history / comparison / settings 页 + 主窗口） | 四大功能页 | [AGENTS.md](./ui/AGENTS.md)，页级按需 | 06_PAGE_GUIDE · 01_CONVENTIONS |
| `ui/widgets/` | 通用控件 | 继承 `ui/AGENTS.md`，独立约定按需 | 01_CONVENTIONS · 03_GOTCHAS |
| `ui/styles/` | 深色主题 QSS / 样式 | 继承 `ui/AGENTS.md` | 01_CONVENTIONS |
| `core/` | 测试编排 / 状态机 / 延迟分析 / 统计 / 校准 | [AGENTS.md](./core/AGENTS.md) | 07_FLOW_GUIDE · 04_ARCHITECTURE |
| `audio/` | 设备管理 / 连续播放 / 采集 / 同步 / 激励生成 | [AGENTS.md](./audio/AGENTS.md) | 05_AUDIO_GUIDE · 04_ARCHITECTURE |
| `data/` | SQLite / repository / 导出 | [AGENTS.md](./data/AGENTS.md) | 04_ARCHITECTURE · 01_CONVENTIONS |
| `app/` | 应用组装 / 配置管理 | [AGENTS.md](./app/AGENTS.md) | 00_OVERVIEW · 01_CONVENTIONS |
| `tests/` | 单元测试 / 合成信号用例 | [AGENTS.md](./tests/AGENTS.md) | 09_WORKFLOW · 03_GOTCHAS |
| 根目录/全局 | 入口、版本、环境、跨模块 | 本文件 | 见分发表 |

---

## 6. 运行 / 打包

> 项目自带虚拟环境 `.venv/`（根目录），命令前先激活。

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
python -m pytest tests/
python -m PyInstaller spec/latency_analyzer.spec --clean --noconfirm   # spec/ 随 Phase 6 建立
```

更多命令见 [docs/ai/02_COMMANDS.md](./docs/ai/02_COMMANDS.md)。

---

## 7. 分发索引表（深度专题按需加载）

> 定位任务类型 → 加载"必读"；"可选"按需拉取。命中多行取**并集**。

| 任务类型（触发词） | 必读（Must-Read） | 可选（On-Demand） |
| -------------------- | ------------------- | ------------------- |
| **新增 / 修改音频引擎（audio/）** | [05_AUDIO_GUIDE](./docs/ai/05_AUDIO_GUIDE.md) · [04_ARCHITECTURE](./docs/ai/04_ARCHITECTURE.md) · [03_GOTCHAS](./docs/ai/03_GOTCHAS.md) | [01_CONVENTIONS](./docs/ai/01_CONVENTIONS.md) |
| **新增 / 修改 UI 页面 / 对话框 / 控件** | [06_PAGE_GUIDE](./docs/ai/06_PAGE_GUIDE.md) · [01_CONVENTIONS](./docs/ai/01_CONVENTIONS.md) · [03_GOTCHAS](./docs/ai/03_GOTCHAS.md) | [04_ARCHITECTURE](./docs/ai/04_ARCHITECTURE.md) |
| **新增 / 修改测量流程 / 状态机 / 分析算法（core）** | [07_FLOW_GUIDE](./docs/ai/07_FLOW_GUIDE.md) · [04_ARCHITECTURE](./docs/ai/04_ARCHITECTURE.md) · [08_CHECKLISTS](./docs/ai/08_CHECKLISTS.md) | [01_CONVENTIONS](./docs/ai/01_CONVENTIONS.md) |
| **数据库 / 导出（data/）** | [04_ARCHITECTURE](./docs/ai/04_ARCHITECTURE.md) · [01_CONVENTIONS](./docs/ai/01_CONVENTIONS.md) · [03_GOTCHAS](./docs/ai/03_GOTCHAS.md) | [08_CHECKLISTS](./docs/ai/08_CHECKLISTS.md) |
| **打包 / spec 修改** | [03_GOTCHAS](./docs/ai/03_GOTCHAS.md) · [08_CHECKLISTS](./docs/ai/08_CHECKLISTS.md) · [02_COMMANDS](./docs/ai/02_COMMANDS.md) | — |
| **排查 Bug / 回归报错** | [03_GOTCHAS](./docs/ai/03_GOTCHAS.md) · [09_WORKFLOW](./docs/ai/09_WORKFLOW.md) · [memory.md](./docs/ai/memory.md) | [decisions/](./docs/ai/decisions/) |
| **重构 / 架构调整 / 分层变动** | [04_ARCHITECTURE](./docs/ai/04_ARCHITECTURE.md) · [00_OVERVIEW](./docs/ai/00_OVERVIEW.md) · [decisions/](./docs/ai/decisions/) | [01_CONVENTIONS](./docs/ai/01_CONVENTIONS.md) |
| **新增依赖 / 环境配置** | [02_COMMANDS](./docs/ai/02_COMMANDS.md) · [08_CHECKLISTS](./docs/ai/08_CHECKLISTS.md) | — |
| **版本号 / 发版 / 改版本** | [10_VERSIONING](./docs/ai/10_VERSIONING.md) | [08_CHECKLISTS](./docs/ai/08_CHECKLISTS.md) |
| **按 SOP 走 / 大任务开工** | [09_WORKFLOW](./docs/ai/09_WORKFLOW.md) · [08_CHECKLISTS](./docs/ai/08_CHECKLISTS.md) | 上述所有 |
| **纯文档 / 规则维护** | [08_CHECKLISTS §同步矩阵](./docs/ai/08_CHECKLISTS.md) · [.trae/rules/project-rules.md](./.trae/rules/project-rules.md) | — |

### 阅读规则

1. 命中多行任务类型 → **并集必读**。
2. 找不到匹配 → 默认 [09_WORKFLOW](./docs/ai/09_WORKFLOW.md) + [03_GOTCHAS](./docs/ai/03_GOTCHAS.md)。
3. 跨层改动（ui ↔ core ↔ audio / data）必加读 [04_ARCHITECTURE](./docs/ai/04_ARCHITECTURE.md)。
4. 改动完成前必须核对 [08_CHECKLISTS](./docs/ai/08_CHECKLISTS.md) 的同步矩阵。

---

## 8. 编辑铁律：向文件追加 / 定位 EOF（高频坑，必读）

> **背景**：`SearchReplace` 类编辑只替换**首个**匹配。当 `old_str` 选用了文件中**多处重复**的模板片段（典型：同类结构共有的尾部方法 / `return` 模板行），会误命中**靠前**那处，把新代码**插进别的结构中间**，劈裂已有类/函数。**严禁再犯。**

### 必须遵守

1. **追加到文件末尾 ≠ 凭印象找 EOF**。动手前先用 `Grep -n "^class "` 或 `Read`（带 offset）确认真实最后一行。
2. **`old_str` 必须全局唯一**。先 `Grep` 统计候选锚点出现次数：

   - 1 次 → 可直接用；≥ 2 次 → **禁止**，须叠加上文唯一行扩展锚点直到整段唯一。
3. **不要用各类通用尾部模板**（同名方法尾行 / `def close` / `return ...`）单独做锚点。
4. 每次编辑后**立即复核结构**：`Grep -n "^class |def <特征方法>"`，确认目标落在 EOF 且相邻结构未被劈裂。
5. 误插入后先精确删除误插块、恢复原结构，再以唯一锚点重新追加。
6. 重要的debug代码可以保留;

### 附：PowerShell 命令注意（Windows 项目）

- **PowerShell 不支持 heredoc**：禁用 `bash` 风格的 `$(cat <<'EOF' ... EOF)` 多行消息语法（`<` 为保留运算符，直接解析报错）。多行内容改用多个 `-m` 参数拼接（如 `git commit -m "标题" -m "正文段1" -m "正文段2"`），或临时消息文件。
