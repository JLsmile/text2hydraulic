# text2hydraulic

论文 *text2hydraulic: An AI-Assisted Method for Hydraulic Circuit Synthesis, Simulation-Based Verification, and Industrial Adaptation* 的配套 demo。输入自然语言工况，系统通过 Design、Review、Simulation 三类智能体完成回路设计、参数计算、模型构建与仿真，在网页中展示模型图、响应曲线和报告。

[英文首页](README.md) · [详细安装](docs/setup.md) · [Demo 使用说明](docs/demo.md) · [方法与代码](docs/method.md)

## 安装环境

使用 Linux 或 WSL2 中的 Linux，准备 Python 3.10+、已配置模型服务并登录的 Claude Code CLI、OpenModelica 及编译工具链、Modelica 标准库、OpenHydraulics 和 GNU `timeout`。

论文使用 OpenModelica 1.26.4、OpenHydraulics 2.0.0。demo 使用本机配置的模型服务。下载或克隆仓库后，在仓库根目录操作：

```bash
python3 --version
claude --version
omc --version
timeout --version

# 替换为实际安装位置。
export T2H_OPENHYDRAULICS=/absolute/path/to/OpenHydraulics/package.mo

# 检查并安装模型图渲染依赖。
python3 setup_renderer.py --check
python3 setup_renderer.py --install

# 启动。
bash start.sh --no-browser
```

打开 **http://127.0.0.1:8766**。如果图形渲染检测不到 OpenModelica 的 `generate_icons.py`，在 `config.json` 的 `icon_exporter` 中填写其实际安装路径。渲染还依赖 Cairo 系统库。网页前端不需要 npm 构建。

## 运行示例

1. 打开右上角环境设置，确认 CLI、编译器和模型库路径。
2. 选择 **Worktable**，或粘贴 [示例工况](examples/worktable.txt)。
3. 点击 **Start design**。
4. 查看模型图，在曲线面板选择 CSV 与信号。
5. 点击 **Read final report** 阅读报告，使用 **Export run** 下载运行产物。

示例工况为水平工作台：质量 500 kg、摩擦系数 0.15、最大速度 0.1 m/s、行程 0.5 m、最高系统压力 10 MPa。示例按钮只填写输入，图形和曲线由实际生成的文件提供。

页面打开时不调用模型；开始设计后使用本机配置的模型服务。默认任务超时为 30 分钟，运行记录位于 `runs/<run-id>/`。程序结束与工程验收分别记录，失败时保留已有产物。

## 代码入口

- `bundle/.claude/skills/text2hydraulic/SKILL.md`：完整流程和共享状态约定。
- `bundle/.claude/agents/`：三个专业智能体定义。
- `bundle/.claude/skills/text2hydraulic/reference/`：回路手册、组件目录和状态模板。
- `bundle/.claude/skills/text2hydraulic/scripts/`：九个确定性工程脚本。
- `server.py`、`static/`：demo 后端与前端。

`python3 scripts/check_repository.py` 检查仓库资源和文档链接；`python3 package_demo.py` 生成便携源码 ZIP。个人配置、凭据、运行输出和虚拟环境不随源码包发布。
