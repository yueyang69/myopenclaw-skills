# dual-expert-chat — 双专家研讨技能 v4.0

## 定位

**纯分析 / 研讨 / 辩论**。无 shell 执行，无文件系统副作用。

> 需要执行任务（编码 / 架构设计 / 部署）请使用 `expert-orchestrator`。

---

## 触发命令

| 命令 | 说明 |
|------|------|
| `/duo blind_review <问题>` | 真盲审：两位专家各自独立选取视角后分析 |
| `/duo debate <命题>` | 正反方辩论：A 支持、B 反对，主持人裁定 |
| `/duo panel <议题>` | 圆桌研讨：A/B 各提问题，交叉回答，主持人汇总 |
| `/duo debate_collaborate <问题>` | 同 blind_review（兼容旧命令） |

---

## 三种模式详解

### blind_review（真盲审）

```
A 独立选取视角 + 分析
          ↓  （互不知晓）
B 独立选取视角 + 分析
          ↓
主持人综合：共识 / 分歧 / 建议 / 待确认问题
```

- A 和 B **各自自主选取视角**，不由任何一方预先指定
- 适合：决策分析、方案比较、需要多角度独立判断的问题
- 模型调用：4 次（A分析 + B分析 + 主持人综合）

### debate（正反方辩论）

```
A（qwen）  → 正方陈述（强制支持命题）
B（claude）→ 反方陈述（强制反对命题）
          ↓
主持人裁定：得分 / 裁定结果 / 综合结论
```

- 适合：需要充分暴露利弊、评估极端情况的议题
- 模型调用：3 次

### panel（圆桌研讨）

```
A 提出 3 个关心的子问题
B 提出 3 个关心的子问题
          ↓
A 回答 B 的问题
B 回答 A 的问题
          ↓
主持人汇总：摘要 / 洞察 / 争议 / 行动建议
```

- 适合：需要多维度探讨的复杂议题，挖掘双方真正关心的核心问题
- 模型调用：5 次

---

## 模型分工

| 角色 | 默认模型 | 环境变量 |
|------|---------|----------|
| 专家A / 正方 / 主持人 | `dashscope-coding/qwen3.5-plus` | `EXPERT_MODEL_A` |
| 专家B / 反方 | `openai/claude-sonnet-4-6` | `EXPERT_MODEL_B` |

---

## 命令行用法

```bash
python main.py blind_review "我应该选择 PostgreSQL 还是 MongoDB？"
python main.py debate "微服务架构比单体架构更适合初创公司"
python main.py panel "如何设计一个高可用的消息队列系统"
```

报告自动保存至 `$WORKSPACE/reports/duo_<mode>_<时间戳>_<问题>.md`

---

## 与 expert-orchestrator 的边界

| 场景 | 用哪个 |
|------|--------|
| 分析利弊、比较方案、研讨决策 | `dual-expert-chat` |
| 辩论一个命题 | `dual-expert-chat` |
| 圆桌讨论一个复杂议题 | `dual-expert-chat` |
| 设计并实现一个系统 | `expert-orchestrator` |
| 编写代码并验证 | `expert-orchestrator` |
| 部署、迁移、执行批量操作 | `expert-orchestrator` |
