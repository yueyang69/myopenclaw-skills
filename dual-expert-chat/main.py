#!/usr/bin/env python3
import argparse
import asyncio
from dual_expert import DualExpertSkill, ExpertMode

async def main():
    parser = argparse.ArgumentParser(description="Dual-Expert Chat Skill")
    parser.add_argument("command", help="Command to run: /duo, /duo_auto, /duo_step, /duo_cancel")
    parser.add_argument("mode", nargs="?", help="Expert mode: brain_hand or debate_collaborate")
    parser.add_argument("task", nargs="*", help="Task description")

    args = parser.parse_args()
    task_description = " ".join(args.task)

    skill = DualExpertSkill()

    if args.command == "/duo_cancel":
        await skill._send_message_to_user("🛑 双专家会话已取消。")
        return

    if not args.mode or not task_description:
        await skill._send_message_to_user("❌ 使用方法：/duo[_auto|_step] <brain_hand|debate_collaborate> <任务描述>")
        return

    await skill.run(args.command, args.mode, task_description)

if __name__ == "__main__":
    asyncio.run(main())
