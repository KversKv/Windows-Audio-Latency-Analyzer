# 02 COMMANDS - 运行 / 测试 / 打包命令

在项目根目录执行；也可直接使用 `.venv\Scripts\python.exe`，无需激活虚拟环境。

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
python main.py
python main.py --simulate
python main.py --smoke-test
python -m pytest tests/ -q
python -m compileall -q main.py app audio core ui tests
```

- `--simulate`：使用明确标注的离线模拟设备，不产生真实测量结果。
- `--smoke-test`：启动后自动退出，用于验证入口和资源初始化。
- 打包命令将在 P7 的 `spec/` 配置建立后启用。
