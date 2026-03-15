#!/usr/bin/env python3
"""
dual-expert-chat 入口

定位：纯分析 / 研讨 / 辩论，无 shell 执行，无文件系统副作用。
需要执行任务（编码/架构/部署）请使用 expert-orchestrator。

用法：
  python main.py blind_review "你的问题"       # 真盲审（推荐）
  python main.py debate "命题"                 # 正反方辩论
  python main.py panel "议题"                  # 圆桌研讨
  python main.py debate_collaborate "问题"     # 同 blind_review（兼容旧命令）

环境变量：
  EXPERT_MODEL_A   专家A模型（默认 dashscope-coding/qwen3.5-plus）
  EXPERT_MODEL_B   专家B模型（默认 openai/claude-sonnet-4-6）
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from dual_expert import DualExpertSkill

VALID_MODES = ["blind_review", "debate", "panel", "debate_collaborate"]


def print_help():
    model_a = os.environ.get("EXPERT_MODEL_A", "dashscope-coding/qwen3.5-plus")
    model_b = os.environ.get("EXPERT_MODEL_B", "openai/claude-sonnet-4-6")
    print(f"""
双专家研讨技能 v4.0

用法：
  python main.py <模式> <问题/议题>

模式：
  blind_review        真盲审 - 两位专家各自独立选取视角后独立分析，互不知晓对方立场
                      适合：需要多角度独立判断的决策、分析类问题

  debate              正反方辩论 - A 强制支持、B 强制反对，主持人裁定
                      适合：需要充分暴露利弊、评估风险的议题

  panel               圆桌研讨 - A/B 各提 3 个子问题，交叉回答，主持人汇总
                      适合：需要多维度探讨的复杂议题

  debate_collaborate  同 blind_review（兼容旧命令）

示例：
  python main.py blind_review "我应该选择 PostgreSQL 还是 MongoDB？"
  python main.py debate "微服务架构比单体架构更适合初创公司"
  python main.py panel "如何设计一个高可用的消息队列系统"

环境变量：
  EXPERT_MODEL_A={model_a}
  EXPERT_MODEL_B={model_b}

注意：本技能只做分析研讨，不执行任何 shell 命令。
     需要执行任务请使用 expert-orchestrator。
""")


def main():
    if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help", "help"):
        print_help()
        sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "help") else 1)

    mode = sys.argv[1].lower()
    question = " ".join(sys.argv[2:])

    if mode not in VALID_MODES:
        print(f"未知模式：{mode}")
        print(f"支持的模式：{', '.join(VALID_MODES)}")
        sys.exit(1)

    if not question.strip():
        print("请提供问题或议题描述")
        sys.exit(1)

    skill = DualExpertSkill()
    result = skill.run(mode=mode, question=question)

    # 保存报告
    output_dir = Path(
        os.environ.get("WORKSPACE", str(Path.home() / ".openclaw" / "workspace"))
    ) / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_q = question[:30].replace(" ", "-").replace("/", "-")
    output_file = output_dir / f"duo_{mode}_{ts}_{safe_q}.md"
    output_file.write_text(result, encoding="utf-8")
    print(f"\n报告已保存：{output_file}")


if __name__ == "__main__":
    main()
