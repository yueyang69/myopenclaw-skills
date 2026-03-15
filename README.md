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

### 2. deep-learning-guide
**深度学习指导** - AI 学习助手

- 深度学习知识问答
- 学习路径规划
- 代码示例生成
- 概念解释

---

### 3. dual-expert-chat
**双专家对话** - 深度分析工具

- 双 AI 协作分析
- 多角度问题解答
- 深度研究报告

---

### 4. academic-deep-research
**学术深度研究** - 文献调研助手

- 文献检索与分析
- 研究趋势总结
- 学术论文辅助

---

## 🚀 快速开始

```bash
# 克隆仓库
git clone https://github.com/yueyang69/openclaw-skills.git
cd openclaw-skills

# 安装依赖（按技能需求）
pip install -r auto-task-runner/requirements.txt
```

---

## 📋 技能对比

| 技能 | 适用场景 | 模型 | 耗时 |
|------|---------|------|------|
| auto-task-runner | 自动化任务 | qwen + claude | ~25 秒/任务 |
| deep-learning-guide | 学习指导 | qwen | ~5 秒/问题 |
| dual-expert-chat | 深度分析 | qwen + claude | ~15 秒/问题 |
| academic-deep-research | 学术研究 | qwen + claude | ~30 秒/主题 |

---

## 🔧 配置

### auto-task-runner 配置
```bash
cd auto-task-runner
# 编辑 SKILL.md 配置环境变量
export DINGTALK_WEBHOOK="your-webhook-url"
export WORKSPACE="/path/to/workspace"
```

---

## 📊 模型使用

| 模型 | 用途 | 成本 |
|------|------|------|
| qwen3.5-plus | 任务执行、报告生成 | $0.01/1K tokens |
| claude-sonnet-4-6 | 方案审查、结果验证 | $0.03/1K tokens |

**单任务平均成本：** ~¥0.25

---

## 📝 更新日志

### 2026-03-15
- ✨ auto-task-runner v3.2 完整修复
- 🐛 修复 architect.py 默认命令生成
- 🐛 修复 verifier.py 关键词判断
- ✅ Python 3.10.14 支持

### 2026-03-14
- ✨ deep-learning-guide 初始版本
- ✨ dual-expert-chat 初始版本

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

## 📄 许可证

MIT License

---

**维护者：** [@yueyang69](https://github.com/yueyang69)
