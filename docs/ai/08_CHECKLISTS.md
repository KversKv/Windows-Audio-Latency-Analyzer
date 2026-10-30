# 08 CHECKLISTS - 检查清单

## 同步矩阵

> 改动某模块后需同步检查 / 更新的文档与代码位置。

| 改动位置 | 需同步核对 |
|---|---|
| 根目录 AGENTS.md 硬红线 / 分层 | .trae/rules/project-rules.md、各子模块 AGENTS.md |
| 新增 / 修改子模块 AGENTS.md | 根 AGENTS.md 子模块地图；仅记录局部约定，避免复制根硬红线 |
| 应用入口 / 配置 / 依赖 | README.md、docs/ai/02_COMMANDS.md、requirements.txt、tests/test_config.py、tests/test_startup.py |
| UI 页面 / 设备刷新 / 配置保存线程 | tests/test_main_window.py；线程信号测试问题同步 docs/ai/03_GOTCHAS.md |
| data/ SQLite、repository、导出 / core 持久化服务 | tests/test_persistence.py、tests/test_main_window.py；局部接口约束同步 data/AGENTS.md、core/AGENTS.md |
| core/ 测量终态、跨流时间映射或部分会话保存 | tests/test_test_controller.py、tests/test_test_state_machine.py、tests/test_persistence.py；UI 保存入口同步 ui/AGENTS.md；时间域契约同步 audio/AGENTS.md、docs/ai/05_AUDIO_GUIDE.md |
| ui/styles/ 运行时 QSS | P7 建立 spec/ 时纳入打包资源，并执行启动冒烟验证 |
| audio/ 后端与设备枚举 | tests/test_audio_backend.py；真实设备枚举冒烟；后续接口约定同步 docs/ai/05_AUDIO_GUIDE.md |
| audio/ 刺激与时间映射、core/ 延迟分析 | tests/test_latency_analyzer.py、tests/test_audio_synchronizer.py、tests/test_audio_engines.py；接口限制同步 docs/ai/05_AUDIO_GUIDE.md |
