---
name: auto-task-runner
description: 自动任务执行器 v4.0 - Layer 1 调度层（纯调度，不执行）
author: OpenClaw
version: 4.0.0
triggers:
  - "任务队列"
  - "批量任务"
  - "员工模式"
metadata:
  requires:
    bins: ["python3"]
  config:
    env:
      DINGTALK_WEBHOOK:
        description: "钉钉机器人 Webhook URL"
        default: ""
      WORKSPACE:
        description: "任务执行的工作目录"
        default: "/home/admin/.openclaw/workspace"
      STEP_INTERVAL:
        description: "步骤间隔秒数"
        default: "30"
      QUEUE_TASK_INTERVAL:
        description: "队列任务间隔秒数"
        default: "60"
      HEARTBEAT_MINUTES:
        description: "心跳间隔分钟"
        default: "8"
---

# auto-task-runner v4.0

**Layer 1 调度层：早上给任务，晚上看结果**

---

## 🎯 核心职责

| 职责 | 说明 |
|------|------|
| **任务解析** | 自然语言任务清单 → tasks.json |
| **两步判断** | ① 能直接执行吗？② 哪个 skill 最合适？ |
| **路由分发** | 调用对应 skill / 直接执行 shell |
| **状态持久化** | 每个任务完成后写 checkpoint |
| **心跳保活** | 每 8 分钟 GC + 状态落盘 |
| **崩溃恢复** | 读 state_snapshot.json 从断点继续 |
| **钉钉通知** | 开始/完成/异常 全程推送 |

**不负责：**
- ❌ 任何执行逻辑（交给各 skill）
- ❌ 模型调用（交给 skill）
- ❌ 任务拆解（交给 dual-expert-chat）

---

## 🏗️ 架构定位

```
Layer 0：守护层（pm2）
    ↓ 崩溃重启
Layer 1：调度层（本 skill）← 你在这里
    ↓ 路由分发
Layer 2：执行层（各 skill）
```

**本 skill 只管「排队、判断、路由、状态、恢复」，不管「怎么执行」。**

---

## 📋 两步任务判断

### 第一步：能直接执行吗？

判断条件：
- 有明确 shell 关键词（清理/删除/压缩/检查/统计/查看）
- 且没有「分析/设计/调研/生成/编写」等思考词

→ 是：直接执行 shell 命令，不调任何 skill  
→ 否：进入第二步

### 第二步：哪个 skill 最合适？

关键词匹配 `config/router.json`：

| 关键词 | 路由到 |
|--------|--------|
| PDF、合并、拆分 | pdf-toolkit |
| 搜索、网络查找 | searxng |
| PR、代码审查 | pr-reviewer |
| bug、报错、debug | debug-pro |
| 股票、A股、K线 | a-stock |
| 代码分析、复杂度 | code-mentor |
| 调研、研究、综述 | academic-deep-research |
| **未匹配（兜底）** | **dual-expert-chat** |

---

## 📝 命令

### 添加任务到队列

```bash
python3 scripts/task-runner.py queue add "清理30天前的日志"
python3 scripts/task-runner.py queue add "分析服务器架构，提出优化方案"
python3 scripts/task-runner.py queue add "调研主流向量数据库"
```

### 查看队列

```bash
python3 scripts/task-runner.py queue list
```

输出示例：
```
──────────────────────────────────────────────────────────────────────
任务队列 (3 个任务)
──────────────────────────────────────────────────────────────────────
  [1] ⏳ 清理30天前的日志
  [2] ⏳ 分析服务器架构，提出优化方案
  [3] ⏳ 调研主流向量数据库
──────────────────────────────────────────────────────────────────────
```

### 启动执行队列

```bash
python3 scripts/task-runner.py queue start
```

**然后去忙别的，晚上看结果。**

### 清空队列

```bash
python3 scripts/task-runner.py queue clear   # 只清除待执行任务
python3 scripts/task-runner.py queue reset   # 完全清空
```

---

## 📁 文件结构

```
auto-task-runner/
├── scripts/
│   ├── task-runner.py          # 主入口（调度核心）
│   ├── router.py               # 路由器（两步判断）
│   └── resource_guard.py       # 资源守卫（内存/磁盘/模型限流）
├── config/
│   ├── router.json             # 路由规则（可扩展）
│   └── skill_registry.json     # skill 注册表
├── .runtime/                   # 运行时数据（自动生成）
│   ├── tasks.json              # 任务队列
│   ├── state_snapshot.json     # 心跳快照
│   ├── model_usage.json        # 模型调用次数统计
│   ├── checkpoints/            # 每个任务的 checkpoint
│   └── logs/
│       └── task-runner.log
└── SKILL.md
```

---

## 🔄 崩溃恢复

任务执行中如果崩溃（内存溢出、网络中断等）：

1. pm2 自动重启 `task-runner.py`
2. 读取 `state_snapshot.json`
3. 从上一个完成的任务继续
4. 钉钉通知：「任务已从断点恢复」

---

## 🛡️ 资源管控

### 内存规则

| 可用内存 | 动作 |
|---------|------|
| < 300MB | 警告 + 强制 GC |
| < 150MB | 暂停任务，等待 60s 后重试 |

### 磁盘规则

| 项目 | 限制 |
|------|------|
| `.runtime/` 总大小 | 2GB |
| `checkpoints/` | 保留最近 100 个文件 |
| `logs/` | 单文件 100MB，最多 3 个 |

### 模型限流

| 模型 | 每小时上限 | 超限动作 |
|------|----------|---------|
| qwen | 40 次 | 暂停 10 分钟 |
| claude | 15 次 | 降级为 qwen 验证 |

---

## 📊 日志

位置：`.runtime/logs/task-runner.log`

查看日志：
```bash
tail -f .runtime/logs/task-runner.log
```

---

## 🔧 扩展

### 新增 skill 路由

编辑 `config/router.json`，加一条规则：

```json
{
  "keywords": ["新关键词1", "新关键词2"],
  "skill": "new-skill-name"
}
```

编辑 `config/skill_registry.json`，加一条注册：

```json
"new-skill-name": {
  "script": "skills/new-skill/main.py",
  "invoke": "subprocess",
  "args": []
}
```

无需改代码，重启即生效。

---

## ⚠️ 注意事项

1. **本 skill 只管调度**，不实现任何执行逻辑
2. **所有复杂任务**最终都会路由到 `dual-expert-chat`（兜底）
3. **直接执行任务**仅限简单 shell 命令（清理/检查/压缩等）
4. **模型调用次数**由 Layer 0 守护层监控，超限自动限流

---

*版本：4.0.0 | 最后更新：2026-03-15*
