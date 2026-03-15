# expert-orchestrator — 双专家编排引擎 v1.0

## 定位

**执行类任务**：编码 / 架构设计 / 部署 / 批量操作。有文件系统副作用，会执行 shell 命令。

> 纯分析 / 研讨 / 辩论请使用 `dual-expert-chat`。

---

## 触发命令

| 命令 | 说明 |
|------|------|
| `/orch <任务描述>` | 启动四阶段编排引擎执行任务 |
| `/orch_resume <runtime目录> <任务描述>` | 从指定 runtime 目录断点续跑 |

---

## 四阶段流水线

```
Architect
  qwen 拆解步骤 → claude 盲审 → qwen 修订（如需）→ 写 plans/PlanList.md
        ↓
Inspector
  本地检查依赖关系，移除无效依赖（零 token）
        ↓
Executor + Verifier Loop（每步）
  执行 shell 命令
    → 本地判断（成功且非关键步骤）
    → claude 验证（失败 / 每 5 步 / 最后一步）
    → 失败可重试（最多 2 次）
  写 checkpoints/step_N_executed.json
  写 checkpoints/step_N_verified.json
        ↓
Tester
  qwen 汇总 → claude 审查 → qwen 回应 → claude 最终认可
  写 reports/<任务名>-report.md
```

---

## 崩溃恢复

`runtime_dir` 由调用方传入**固定路径**（非时间戳目录）。
重启后传入相同 `runtime_dir`，引擎自动读取已完成的 checkpoint，**跳过已完成步骤，从断点继续**。

```bash
# 第一次运行（崩溃）
python main.py --runtime-dir .runtime/my-task "构建 FastAPI 用户认证服务"

# 崩溃恢复（传入相同目录）
python main.py --runtime-dir .runtime/my-task "构建 FastAPI 用户认证服务"
```

---

## 成本控制

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CLAUDE_VERIFY_EVERY_N` | 5 | 每 N 步调一次 claude 验证 |
| `STEP_INTERVAL` | 30s | 每步执行后 GC + 等待，保护 2GB 内存 |
| `MAX_RETRIES` | 2 | 每步失败最大重试次数 |

claude 只在以下情况被调用：
- 步骤执行失败
- 每 5 步定期审查
- 最后一步强制验证
- Architect 阶段盲审（1次）
- Tester 阶段审查 + 最终认可（2次）

---

## 模型分工

| 角色 | 默认模型 | 环境变量 |
|------|---------|----------|
| 主力执行（Architect/Executor/Tester汇总） | `dashscope-coding/qwen3.5-plus` | `EXPERT_MODEL_A` |
| 关键验证（Architect审查/Verifier/Tester验收） | `openai/claude-sonnet-4-6` | `EXPERT_MODEL_B` |

---

## Layer 2 统一接口

```python
from orchestrator import ExpertOrchestrator

orchestrator = ExpertOrchestrator()
result = orchestrator.run(
    task_description="构建 FastAPI 用户认证服务",
    output_dir=Path(".runtime/my-task")   # 固定路径，支持断点续跑
)
# result: { status, report_path, elapsed }
```

---

## 命令行用法

```bash
# 直接运行
python main.py "帮我设计并实现一个 Python 日志轮转工具"

# 指定 runtime 目录（支持断点续跑）
python main.py --runtime-dir .runtime/log-tool "帮我设计并实现一个 Python 日志轮转工具"
```

报告输出：
- `<runtime_dir>/plans/<任务名>.md` — 步骤计划
- `<runtime_dir>/checkpoints/step_N_*.json` — 每步 checkpoint
- `<runtime_dir>/reports/<任务名>-report.md` — 最终报告
- `$WORKSPACE/reports/orch_<时间戳>_<任务名>.md` — 摘要（可选）

---

## 与 dual-expert-chat 的边界

| 场景 | 用哪个 |
|------|--------|
| 分析利弊、比较方案、研讨决策 | `dual-expert-chat` |
| 辩论一个命题 | `dual-expert-chat` |
| 圆桌讨论一个复杂议题 | `dual-expert-chat` |
| 设计并实现一个系统 | `expert-orchestrator` |
| 编写代码并验证 | `expert-orchestrator` |
| 部署、迁移、执行批量操作 | `expert-orchestrator` |
| Layer 1 路由到的兜底执行 skill | `expert-orchestrator` |

