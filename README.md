# Vivado Agent

AI-driven **simulation acceleration** + **auto-debug** for FPGA development with Vivado/XSim.

## Architecture

```
Phase 1: 效率优化层 (规则引擎, 零 LLM)
  ├─ IncrementalCompileManager — 模块级哈希缓存, 只重编变更模块
  ├─ MultithreadTuner — CPU/设计规模自适应线程数
  ├─ WaveformTrimmer — 断言+IO信号动态选择, 避免全信号dump拖慢
  ├─ StaticScanner — forever死锁/未初始化复位/握手无限等待预判
  └─ SimulationMonitor — 实时X/Z传播追踪, 断言失败自动停止

Phase 2: 数据解析层
  ├─ LogParserAgent — 工具错误语义化翻译, RAG行号→源码映射
  ├─ BugDatabase — 常见FPGA Bug模式匹配
  └─ WaveformAnalysisAgent — WDB信号快照提取, 故障传导链追溯

Phase 3: Debug 智能层 (LLM API 驱动)
  ├─ DebugOrchestrator — 错误→波形→LLM补丁→重仿真闭环
  └─ AutoFixAgent — 在线LLM生成最小补丁 (OpenAI/DeepSeek等兼容API)

Base: TCLEngine — 纯TCL执行封装, 无智能决策
```

## Quick Start

```bash
# 1. 设置 LLM API (Phase 3 需要)
copy .env.example .env
# 编辑 .env 填入 LLM_API_KEY

# 2. 检测项目
python -m src.main detect -p /path/to/vivado/project

# 3. 生成优化仿真脚本
python -m src.main optimize -p /path/to/project -o run.tcl

# 4. 执行并监控 (需要 Vivado 在 PATH)
python -m src.main run -p /path/to/project

# 5. 全自动 Debug 闭环 (需要 .wdb 波形文件)
python -m src.main debug --log xsim.log --wdb run.wdb -t top_module

# 6. 实时日志监控
python -m src.main monitor --log xsim.log
```

## Phase 1 效果预估

| 优化项 | 预期提速 |
|--------|---------|
| 增量编译 (模块级) | 连续仿真编译减少 60%+ |
| 多线程自适应 | 充分利用多核 CPU |
| 波形智能裁剪 | 仿真速度提升 30~50% |
| 死锁预判 | 避免空跑几小时挂死 |

## Debug 闭环流程

```
仿真报错 → LogParser 解析 (RAG映射到源码行)
         → WaveformAnalysis 提取故障信号快照
         → LLM 生成最小补丁
         → 自动覆盖原文件
         → 重跑仿真验证
         → 循环直到断言全通过
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API 密钥 | - |
| `LLM_BASE_URL` | API 地址 | `https://api.openai.com/v1` |
| `LLM_MODEL` | 模型名 | `gpt-4o` |
| `VIVADO_PATH` | Vivado 可执行路径 | `vivado` (PATH) |

## 开发

```bash
pip install -e .
python -m pytest tests/ -v
```