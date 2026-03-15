---
name: auto-task-runner
description: 自动任务执行器 v3.2 - 员工模式 + 任务队列 + 双模型协作 + 钉钉通知
author: OpenClaw
version: 3.2.0
triggers:
  - "员工模式"
  - "执行任务"
  - "自动任务"
  - "任务进度"
metadata:
  requires:
    bins: ["python3"]
  config:
    env:
      DINGTALK_WEBHOOK:
        description: "钉钉机器人 Webhook URL"
        default: ""
      WORKSPACE:
        description: "任务执行的工作目录（非文件存储目录）"
        default: "/home/admin/.openclaw/workspace"
      STEP_INTERVAL:
        description: "步骤间隔秒数，用于 GC 清内存"
        default: "30"
      QUEUE_TASK_INTERVAL:
        description: "队列任务间隔秒数"
        default: "60"
      HEARTBEAT_MINUTES:
        description: "心跳间隔分钟"
        default: "8"
---

# 自动任务执行器 v3.2

**老板模式：早会布置任务，晚上看结果**

---

## 🎯 核心特性

| 特性 | 说明 |
|------|------|
| **员工模式** | 触发词「员工模式」，AI确认理解后自动执行 |
| **任务队列** | 批量添加任务，依次自动执行 |
| **双模型协作** | qwen3.5-plus (主力) + claude-sonnet-4.6 (关键验证) |
| **钉钉通知** | 开始/完成/异常 全程推送 |
| **员工日志** | 每日报告存 `employee-logs/YYYY-MM-DD/` |
| **心跳保活** | 每8分钟状态落盘 + GC，2GB内存友好 |

---

## 🏗️ 架构

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Architect   │→ │ Inspector   │→ │ Executor    │→ │ Tester      │
│ (双模型商量) │  │ (依赖检查)  │  │ (qwen 执行)  │  │ (双模型验收)│
└─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘
                        ↑                ↓
                        └── Verifier ←───┘
                           (claude 验证)
```

### 四阶段流程

1. **📐 建筑师 (Architects)** - qwen ←→ claude 商量生成 PlanList.md
2. **🏃 执行循环** - 每个步骤：qwen 执行 → claude 验证 → 写检查点
3. **✅ 测试师 (Tester)** - qwen ←→ claude 商量生成 Report.md
4. **💓 心跳** - 每 8-10 分钟强制保存状态 + GC

---

## 📝 命令

### ⭐ 员工模式（最推荐）

触发词：`员工模式`

```bash
python3 scripts/task-runner.py 员工模式 "
1. 清理30天前的日志
2. 压缩workspace下的内存文件
3. 检查磁盘空间，生成报告
4. 做一个深入学习的skill
"
```

**对话流程：**
```
小智员工收到任务，正在理解中...

📋 收到老板！我来确认一下我的理解：

  任务1：扫描日志目录，压缩30天前的文件
  任务2：压缩workspace下的大文件
  任务3：df -h检查磁盘，生成使用报告
  任务4：设计并实现深入学习skill

以上理解正确吗？
- 直接回车 = 确认，开始执行
- 输入补充说明 = 更新理解后开始
- 输入 '取消' = 取消

> [用户回车或补充]

✅ 确认！开始执行，完成后钉钉通知你
```

**执行完成后报告目录：**
```
.runtime/employee-logs/2026-03-14/
  ├── task-01-清理30天前的日志.md
  ├── task-02-压缩workspace.md
  ├── task-03-检查磁盘空间.md
  ├── task-04-深入学习skill.md
  └── summary.md
```

---

## 📁 文件结构

```
auto-task-runner/
├── scripts/
│   ├── task-runner.py          # 主入口 (orchestrator)
│   ├── architect.py            # 建筑师模块 (动态任务分解)
│   ├── executor.py             # 执行器模块 (本地执行)
│   ├── verifier.py             # 验证器模块 (claude 关键验证)
│   ├── model_client.py         # 模型调用客户端 (三级降级)
│   └── tester.py               # 测试师模块
├── .runtime/                   # 运行时数据（自动生成，不要手动修改）
│   ├── plans/                  # 任务分解计划
│   ├── reports/                # 执行报告
│   ├── checkpoints/            # 每步检查点
│   │   ├── step_1_executed.json
│   │   └── step_1_verified.json
│   ├── logs/
│   │   └── task-runner.log
│   └── state_snapshot.json     # 心跳快照（崩溃恢复用）
└── SKILL.md
```

---

## 📋 PlanList.md 格式

```markdown
# PlanList.md - 日志轮转脚本

| Step ID | 步骤名称 | 依赖 | 预计耗时 | 命令 | 状态 |
|---------|---------|------|---------|------|------|
| 1 | 分析现有日志结构 | 无 | 2min | `ls -lh logs/` | done |
| 2 | 编写轮转脚本 | 1 | 5min | `cat > script.sh...` | done |
| 3 | 测试脚本 | 2 | 3min | `bash script.sh` | pending |
| 4 | 配置 cron | 3 | 2min | `crontab -e` | pending |
| 5 | 验证运行 | 4 | 1min | `crontab -l` | pending |
```

---

## 🔍 检查点格式

### 执行检查点 (step_N_executed.json)
```json
{
  "step_id": 1,
  "step_name": "分析现有日志结构",
  "executor": "dashscope-coding/qwen3.5-plus",
  "executor_output": "logs/ 目录下有 3 个文件...",
  "success": true,
  "executed_at": "2026-03-14T20:00:00"
}
```

### 验证检查点 (step_N_verified.json)
```json
{
  "step_id": 1,
  "step_name": "分析现有日志结构",
  "verifier": "openai/claude-sonnet-4-6",
  "verifier_decision": "done",
  "verifier_reason": "输出包含完整的日志文件列表，分析完成",
  "verified_at": "2026-03-14T20:02:00"
}
```

---

## ⏱️ 心跳机制

### 时间分配（8-10 分钟）

| 操作 | 模型 | 耗时 |
|------|------|------|
| qwen 执行命令 | qwen3.5-plus | 3-4min |
| 切换模型 | - | 30s-1min |
| claude 验证 | claude-sonnet-4.6 | 2-3min |
| 保存检查点 + GC | - | 1min |
| **总计** | - | **7-9min** ✅ |

### 心跳触发时

1. **状态序列化** → `.state_snapshot.json`
2. **强制 GC** → 释放内存
3. **内存检查** → <300MB 触发额外清理
4. **重置计时器** → 继续执行

---

## 🛠️ 任务描述技巧

v3.1 支持自然语言，qwen + claude 自动拆解步骤，无需手动配置。

**好的任务描述示例：**
- `"检查 /var/log 目录，压缩30天前的日志，释放磁盘空间"`
- `"扫描工作区的 Python 文件，找出超过500行的大文件并生成报告"`
- `"测试服务器上的3个 API 接口是否正常响应，记录结果"`

**描述越具体，拆解越准确。**

---

## 📊 日志

位置：`/home/admin/.openclaw/workspace/logs/task-runner.log`

查看日志：
```bash
tail -f /home/admin/.openclaw/workspace/logs/task-runner.log
```

---

## ⚠️ 注意事项

1. **任务命名必须精确匹配** - "日志轮转脚本" 不能写成 "日志轮转"
2. **每步预计耗时 <8 分钟** - 适配心跳机制
3. **依赖关系必须正确** - 否则会被 Inspector 拦截
4. **检查点持久化** - 崩溃后自动恢复

---

## 🔄 崩溃恢复

任务执行中如果崩溃（内存溢出、网络中断等）：

1. 重新运行 `python3 task-runner.py run "任务名称"`
2. 自动加载 `.state_snapshot.json`
3. 从最后完成的步骤继续

---

*版本：3.1.0 | 最后更新：2026-03-14*
