# Vivado Agent 使用指南

## 适用场景

| 阶段 | 适用 | 说明 |
|------|------|------|
| RTL 功能仿真 | ✅ 核心场景 | 增量编译加速、波形裁剪、死锁预判、自动 Debug |
| 后综合时序仿真 | ✅ 支持 | 网表 + SDF 反标 + 时序检查 |
| 综合/实现(PPA) | ✅ 支持 | 自动 synth + place + route + 时序/面积/功耗报告 |
| 布局布线后仿真 | ⚠️ 部分 | 通过 PPA 分析间接覆盖，时序仿真需手动调用 |
| 流片级验证 | ❌ 未覆盖 | 建议使用专业 EDA 工具 |

## 按工程规模适配

### 小工程 (< 5000 行 RTL, 1-5 模块)

**特点**: 单文件、单 Testbench、迭代快

```bash
# 一步到位
python -m src.main run -p . --top top

# 自动决策结果:
#   threads: 2 (小工程不需要多线程)
#   incremental: 首次全量，后续缓存
#   waveform: compact 模式
```

**踩坑点**: 小工程增量编译收益有限，首次运行不会比全量编译快。连续修改再跑才能看到加速。

### 中工程 (5000-50000 行, 5-50 模块)

**特点**: 多文件、多目录、有子模块层次

```bash
# 先检测工程结构
python -m src.main detect -p .

# 用优化模式生成脚本，检查决策日志
python -m src.main optimize -p . -o run.tcl

# 确认静态扫描结果
# 如果发现 infinite_loop 或 handshake_deadlock，先修再跑

# 执行
python -m src.main run -p .
```

**最佳实践**:
- 首次运行后，第二次开始增量编译生效
- 如果修改了底层模块接口，依赖图会自动标记所有上层模块为"需重编译"
- 遇到仿真报错时，用 `monitor` 命令实时追 X/Z 传播

**踩坑点**:
- 增量编译缓存文件在 `xsim_cache/`，如果工程结构大改，执行 `clear-cache` 重置
- 依赖图解析依赖模块例化命名规范，`if/for/while` 等关键字不会被误认为模块名

### 大工程 (50000+ 行, 50+ 模块)

**特点**: 多 IP、多时钟域、AXI/PCIe 等复杂协议

```bash
# 1. 批量检测所有子工程
python -m src.main detect -p ip_a/
python -m src.main detect -p ip_b/

# 2. 逐个优化，检查静态扫描结果
python -m src.main optimize -p ip_a/
python -m src.main optimize -p ip_b/

# 3. 批量调度 (通过 BatchOrchestrator API)
```

**最佳实践**:
- 先跑静态扫描，修复 deadlock/CDC 问题，再跑仿真
- 使用波形裁剪减少仿真时间，大工程全信号 dump 会让仿真慢 3-5 倍
- 协议分析器会自动检测 AXI/PCIe 信号，在波形裁剪时保留协议关键信号

**踩坑点**:
- 大工程不要用 `log_wave -recursive *`，仿真速度直接腰斩
- 如果工程有跨时钟域，确保 `set_property xsim.simulate.log_all_signals false` 已设置
- PPA 分析 (synth + impl) 需要 30 分钟以上，只在对时序收敛有疑问时执行

## 常见问题

### 增量编译不生效
```bash
# 检查缓存
python -m src.main status
# 确认 xsim_cache 目录存在
# 如果缓存损坏，清除重来
python -m src.main clear-cache
```

### 仿真结果与预期不符
1. 检查静态扫描输出，确认没有 infinite_loop
2. 用 `monitor` 命令实时看 X/Z 传播
3. 如果是增量编译后出现，执行 `clear-cache` 全量重跑确认

### Verilator 不可用
```bash
# Windows: 安装 WSL + Verilator
wsl sudo apt install verilator
# 或直接用 Vivado 后端 (慢但可用)
# Agent 会自动降级，无需手动配置
```

### WDB 波形分析失败
- WDB 是闭源格式，必须通过 Vivado 读取
- 确保 Vivado 在 PATH 中，且 WDB 文件完整
- Agent 会通过 VCD 导出间接读取，不需要直接解析 WDB

## 与现有 Vivado 流程集成

### 作为 pre-synth 检查
```bash
# 在 synthesis 前运行，提前发现死锁/CDC/FSM 问题
python -m src.main run -p .
```

### 作为 post-synth 时序验证
```python
from src.tools.post_synth_flow import PostSynthFlow
flow = PostSynthFlow()
result = flow.run_timing_sim(rtl_dir, tb_path, "top")
print(f"Timing matched: {result.timing_matched}")
```

### 作为 CI 流程
在 GitHub Actions 中集成:
```yaml
- name: Vivado Agent check
  run: |
    pip install -e .
    python -m src.main run -p . --top top
```

## 设计决策

### 为什么不用 WDB 直接解析？
WDB 是 Xilinx 闭源二进制格式，没有公开的解析库。通过 TCL 导出 VCD 再解析是唯一可靠的方案。

### 为什么用 Verilator 做 lint 而不是 Vivado？
Verilator 的 `--lint-only` 可以在毫秒级完成语法检查，Vivado 的 `read_verilog` 需要 10s+ 启动时间。Windows 下通过 WSL 自动降级，不影响使用。

### 为什么不做布局布线后仿真？
布局布线后的时序仿真需要完整的实现后网表 + 寄生参数文件 (SPEF)，这些文件生成时间很长 (30min+) 且依赖具体的工艺库。对于前端调试阶段，综合后时序仿真 (SDF) 已经能覆盖 90% 的时序问题。