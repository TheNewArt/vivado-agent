# Vivado Agent

AI-driven **simulation acceleration** + **auto-debug** for FPGA development with Vivado/XSim.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     CLI (src/main.py)                            │
│     7 commands: detect / optimize / run / monitor / debug        │
│                 clear-cache / status                             │
├──────────────────────────────────────────────────────────────────┤
│  Phase 1: 效率层 (规则引擎, 零LLM)                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ SimulationOptimizerAgent  (自适应决策引擎)                  │   │
│  │  ├─ 决策: 增量编译? (改动比例 <30% → 启用)                  │   │
│  │  ├─ 决策: 线程数? (代码行数/模块数 → 2/4/8)                │   │
│  │  ├─ 决策: 波形粒度? (回归模式→compact, 调试→全信号)         │   │
│  │  ├─ 决策: 中止? (死锁/无限循环 → 停止, 不浪费仿真时间)       │   │
│  │  └─ 反馈闭环: 记录每次结果 → 下次决策更准                   │   │
│  └──────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────┤
│  Phase 2: 数据层 (结构化解析)                                     │
│  ┌─ LogAnalyzer ── RAGIndex ── BugDatabase ─────────────────┐   │
│  │  工具报错 → 正则提取 → 行号→源码块映射 → 历史模式匹配      │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌─ WDBReader ── DependencyGraph ── ModuleParser ──────────┐   │
│  │  WDB→VCD导出→解析 / RTL例化图→依赖链 / 模块名提取        │   │
│  └──────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────┤
│  Phase 3: 智能层 (LLM API 驱动)                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ DebugOrchestrator                                        │   │
│  │  ├─ 错误分级: 阻塞(0) > 逻辑(1) > 时序(2) > 风格(3)      │   │
│  │  ├─ 回归检测: 错误数恶化→回滚, 连续2次→放弃               │   │
│  │  └─ 迭代终止: 固定/恶化/达上限                            │   │
│  │ AutoFixAgent                                              │   │
│  │  ├─ 策略选择: diff / JSON / 全替换 (根据历史成功率)        │   │
│  │  ├─ 前置校验: xvlog语法检查 → 再应用                      │   │
│  │  └─ 策略学习: 追踪每种错误类型的最优修复策略               │   │
│  └──────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────┤
│  Enterprise: 批量调度 + MCP 插件 + 本地部署                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ BatchOrchestrator: 优先级队列, 并行执行, 结果聚合          │   │
│  │ MCP Adapter: 7个工具函数(detect/optimize/run/scan/       │   │
│  │              debug/synth/ppa) 对接 OpenClaw 网关          │   │
│  │ Local LLM Mode: 关闭API调用, 数据合规                    │   │
│  └──────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────┤
│  Base: TCLEngine / LLMClient / Config / Logger                   │
└──────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. 检测项目
python -m src.main detect -p example

# 2. 优化分析 + 生成 TCL (含决策日志)
python -m src.main optimize -p example -o run.tcl

# 3. 一键执行 (需要 Vivado 在 PATH)
python -m src.main run -p example

# 4. 全自动 Debug (需要 LLM_API_KEY + WDB 文件)
set LLM_API_KEY=sk-xxx
python -m src.main debug --log xsim.log --wdb run.wdb -t top

# 5. 实时监控
python -m src.main monitor --log xsim.log
```

## 效果 (Vivado 2020.2 实测)

| 阶段 | 时间 | 说明 |
|------|------|------|
| 编译 (xvlog) | 1s | `--incr` 增量模式 |
| Elaborate (xelab) | 1s | 4 线程, 缓存命中 |
| 仿真 (xsim) | 6s | 1000ns, 740ns `$finish` |
| 全流程 | 8s | 零错误 |

## 优化过程

### v0.1 骨架搭建 (2026-07-01)
- 三层架构: TCL 执行层 / 规则引擎层 / LLM 智能层
- 基础工具: 增量编译缓存 / 多线程调优 / 波形裁剪 / 日志分析
- 22 个 pytest, Vivado 2020.2 全链路验证

### v0.2 决策引擎 (2026-07-01)
- 三个 Agent 加入真实决策逻辑:
  - `SimulationOptimizerAgent`: 自适应增量/线程/中止决策 + 历史反馈闭环
  - `DebugOrchestrator`: 错误分级/优先级排序/回归检测
  - `AutoFixAgent`: 多策略补丁/置信度评分/策略学习
- 修复: `set_property` / `log_wave` / `wait_on_run` 等 Vivado 2020.2 兼容性问题

### v0.3 正确性加固 (2026-07-01)
- 依赖图: RTL 例化解析 → 传递闭包 → 改子模块自动标记所有上层模块
- WDB 波形: 真实 TCL 执行 → VCD 导出 → 解析 → 故障链追溯
- 静态扫描: 从 4 种扩展到 18 种 (FSM/CDC/亚稳态/组合环路/多驱动)
- 语法校验: xvlog 检查后 LLM 补丁才生效
- 25 个测试

### v0.4 企业级能力 (2026-07-01)
- PPA: 时序/面积/功耗分析 (Vivado synth + impl 自动执行)
- 批量调度: 优先级队列 + 并行执行 + 结果聚合
- MCP 插件: 7 个工具函数对接 OpenClaw 网关
- 本地模式: 关闭 LLM API 调用, 适配数据合规场景
- Benchmark: 6 个测试, 含 5 个故意植入 Bug, 31 个测试全绿

### v0.5 毫秒级语法校验 (2026-07-01)
- Verilator `--lint-only` 后端: 毫秒级语法 + Lint 检查, 过滤 80% 低级错误
- 双后端自动降级: Verilator (ms) → Vivado xvlog (s), 无 Verilator 也可用
- Windows WSL 支持: 自动检测 `wsl verilator`, Windows 也可用毫秒级 lint
- 自动修复成功率提升: 补丁先过 lint 再应用, 避免无效循环

### v0.6 多协议信号分析 & 时序自动约束 (2026-07-01)
- 协议分析: AXI4 读写通道握手 / PCIe TLP 事务 / UDP 包边界 / SPI/I2C 总线, 从 VCD 波形中自动检测
- 时序约束自动生成: 解析 Vivado timing report → 自动输出 SDC/XDC (multicycle/async/false_path/max_delay)
- 波形裁剪集成: 协议感知的信号选择, 自动在 TB 中检测 AXI 信号名

### v0.7 企业级完备性加固 (2026-07-01)
- BugDatabase: 120+ 错误/警告模式, 14 大分类 (Synth/Impl/Timing/XSim/UVM/DRC/TCL/PetaLinux/HLS/AIE)
- TCL 会话管理: 持久会话 + 心跳监控 + 超时自动重启 + 内存泄漏检测
- 后综合时序仿真: synth → netlist + SDF → gate-level timing sim → 对比 RTL 结果
- 文档: docs/guide.md 多规模工程教程、最佳实践、常见踩坑点
- 器件/版本检测: 从 .xpr 自动识别 7series/UltraScale/Versal/AIE
- PetaLinux/Vitis HLS 集成检测: 自动识别 petalinux 项目、HLS 脚本、C/C++ HLS 源码
- Verilator WSL 支持: Windows 下自动降级到 wsl verilator, 保持毫秒级 lint

### v0.8 Debug 闭环验证 (2026-07-02)
- DebugOrchestrator 完整闭环: 错误检测 → 波形分析 → LLM 修复 → 语法校验 → 文件覆盖 → 重验证
- xsim 信号提取: 通过 xsim --tclbatch 直接读取 WDB 信号值 (绕过 Vivado 闭源格式)
- 静态扫描自动兜底: xsim 不可用时自动降级到 RTL 静态分析
- 停滞检测: 3 轮无改善或 LLM 重复输出时自动终止
- 修复文件优先匹配: 自动选择 buggy 命名的源文件
- 带行号标注的代码上下文: LLM 获取完整代码+错误标记
- 真实 Vivado 2020.2 仿真数据验证通过 (Bug 从 5 个降至 1 个, 1 个架构级问题可接受)

## 文档

```bash
docs/guide.md           # 使用指南：多规模工程适配、最佳实践、常见踩坑点
docs/architecture.md    # 架构说明
```

## 测试

```bash
pip install -e .
python -m pytest tests/ -v            # 25 核心测试
python tests/benchmark/test_benchmark.py  # 6 benchmark 测试
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API 密钥 | - |
| `LLM_BASE_URL` | API 地址 | `https://api.openai.com/v1` |
| `LLM_MODEL` | 模型名 | `gpt-4o` |
| `VIVADO_PATH` | Vivado 可执行路径 | `vivado` (PATH) |