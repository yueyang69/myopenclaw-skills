#!/usr/bin/env python3
"""
expert-orchestrator 入口

定位：执行类任务 —— 编码 / 架构设计 / 部署 / 批量操作。
有文件系统副作用，会执行 shell 命令。

纯分析 / 研讨 / 辩论请使用 dual-expert-chat。

用法：
  python main.py "帮我设计并实现一个 Python 日志轮转工具"
  python main.py --runtime-dir /path/to/runtime "任务描述"   # 指定 runtime 目录（支持断点续跑）

环境变量：
  EXPERT_MODEL_A   专家A模型（默认 dashscope-coding/qwen3.5-plus）
  EXPERT_MODEL_B   专家B模型（默认 openai/claude-sonnet-4-6）
  STEP_INTERVAL    步骤间隔秒数（默认 30）
  WORKSPACE        报告输出根目录（默认 ~/.openclaw/workspace）
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from orchestrator import ExpertOrchestrator


def print_help():
    model_a = os.environ.get("EXPERT_MODEL_A", "dashscope-coding/qwen3.5-plus")
    model_b = os.environ.get("EXPERT_MODEL_B", "openai/claude-sonnet-4-6")
    print(f"""
expert-orchestrator v1.0 — 双专家编排引擎

用法：
  python main.py <任务描述>
  python main.py --runtime-dir <目录> <任务描述>   # 指定 runtime 目录，支持断点续跑

适合场景：
  - 设计并实现一个系统 / 工具
  - 编写代码并验证
  - 部署、迁移、执行批量操作
  - 任何需要执行 shell 命令的任务

不适合场景（请用 dual-expert-chat）：
  - 分析利弊、比较方案
  - 辩论一个命题
  - 圆桌讨论一个复杂议题

示例：
  python main.py "帮我设计并实现一个 Python 日志轮转工具"
  python main.py "把 workspace/data/ 下所有 CSV 文件合并为一个"
  python main.py --runtime-dir .runtime/my-task "构建 FastAPI 用户认证服务"

环境变量：
  EXPERT_MODEL_A={model_a}
  EXPERT_MODEL_B={model_b}
  STEP_INTERVAL={os.environ.get('STEP_INTERVAL', '30')}s
""")


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        sys.exit(0 if args and args[0] in ("-h", "--help", "help") else 1)

    # 解析 --runtime-dir
    runtime_dir = None
    if args[0] == "--runtime-dir":
        if len(args) < 3:
            print("--runtime-dir 需要提供目录路径和任务描述")
            sys.exit(1)
        runtime_dir = Path(args[1])
        task = " ".join(args[2:])
    else:
        task = " ".join(args)

    if not task.strip():
        print("请提供任务描述")
        sys.exit(1)

    # 若未指定 runtime_dir，按任务内容生成固定目录名（非时间戳）
    # 相同任务重启后使用相同目录，支持断点续跑
    if runtime_dir is None:
        workspace = Path(
            os.environ.get("WORKSPACE", str(Path.home() / ".openclaw" / "workspace"))
        )
        import re
        safe_task = re.sub(r'[^\w\u4e00-\u9fff-]', '-', task)[:40]
        runtime_dir = workspace / ".runtime" / f"orch_{safe_task}"

    print(f"runtime 目录：{runtime_dir}")
    print(f"任务：{task}\n")

    orchestrator = ExpertOrchestrator()
    result = orchestrator.run(task_description=task, output_dir=runtime_dir)

    # 同时在 WORKSPACE/reports/ 存一份摘要
    output_dir = Path(
        os.environ.get("WORKSPACE", str(Path.home() / ".openclaw" / "workspace"))
    ) / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_q = task[:30].replace(" ", "-").replace("/", "-")
    summary_file = output_dir / f"orch_{ts}_{safe_q}.md"
    summary_file.write_text(
        f"# ExpertOrchestrator 报告\n\n"
        f"任务：{task}\n\n"
        f"状态：{result['status']}\n"
        f"耗时：{result['elapsed']}\n"
        f"报告：{result['report_path']}\n",
        encoding="utf-8"
    )
    print(f"\n摘要已保存：{summary_file}")


if __name__ == "__main__":
    main()

