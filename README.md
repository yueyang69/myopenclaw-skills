# OpenClaw Skills

OpenClaw 智能体技能集合 - 高效、可复用的 AI 助手技能

---

## 📦 技能列表

### 1. auto-task-runner v3.2 ⭐
**员工模式** - 自动任务执行器

- 自然语言任务输入
- 双模型协作（qwen3.5-plus + claude-sonnet-4-6）
- 任务队列管理
- 钉钉通知集成
- 崩溃恢复

**使用示例：**
```bash
cd auto-task-runner
python3.10 scripts/task-runner.py 员工模式 "
1. 清理过期日志
2. 分析技能使用情况
"
```

---

### 2. dual-expert-chat v4.0
**双专家研讨** - 纯分析/辩论/决策

- **blind_review** - 真盲审（专家独立选视角）
- **debate** - 正反方辩论
- **panel** - 圆桌研讨
- 无 shell 副作用，纯分析

**使用示例：**
```bash
cd dual-expert-chat
python3 main.py blind_review "这个技术方案是否可行？"
```

---

### 3. expert-orchestrator v1.0 🆕
**执行引擎** - 编码/架构/部署/批量操作

- 四阶段流水线（Architect → Inspector → Executor → Tester）
- 断点续跑（`--runtime-dir`）
- 检查点持久化
- 会执行 shell 命令

**使用示例：**
```bash
cd expert-orchestrator
python3 main.py "创建一个 Flask API 项目"
python3 main.py --runtime-dir ./my-task "继续执行"
```

---

### 4. deep-learning-guide
**深度学习指导** - AI 学习助手

- 深度学习知识问答
- 学习路径规划
- 代码示例生成

---

### 5. academic-deep-research
**学术深度研究** - 文献调研助手

- 文献检索与分析
- 研究趋势总结
- 学术论文辅助

---

## 📋 技能对比

| 技能 | 定位 | 副作用 | 断点续跑 | 耗时 |
|------|------|--------|---------|------|
| auto-task-runner | 自动化任务队列 | ✅ | ✅ | ~25 秒/任务 |
| dual-expert-chat | 纯研讨分析 | ❌ | ❌ | ~15 秒/问题 |
| expert-orchestrator | 执行引擎 | ✅ | ✅ | ~30 秒/步骤 |
| deep-learning-guide | 学习指导 | ❌ | ❌ | ~5 秒/问题 |
| academic-deep-research | 学术研究 | ❌ | ❌ | ~30 秒/主题 |

---

## 🎯 如何选择

**需要执行命令/写代码？**
- 简单任务 → `auto-task-runner`
- 复杂项目 → `expert-orchestrator`

**只需要分析/讨论？**
- 深度研讨 → `dual-expert-chat`
- 学习问题 → `deep-learning-guide`
- 学术研究 → `academic-deep-research`

---

## 🔗 仓库

**GitHub:** https://github.com/yueyang69/myopenclaw-skills

```bash
git clone https://github.com/yueyang69/myopenclaw-skills.git
```

---

**维护者：** [@yueyang69](https://github.com/yueyang69)  
**更新时间：** 2026-03-15
