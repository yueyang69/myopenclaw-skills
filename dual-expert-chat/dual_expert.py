import json
from enum import Enum
from typing import Dict, Any

from openclaw.tools.message import message
from openclaw.tools.session_status import session_status
from openclaw.tools.sessions_send import sessions_send
from openclaw.tools.sessions_spawn import sessions_spawn

class ExpertMode(Enum):
    BRAIN_HAND = "brain_hand"
    DEBATE_COLLABORATE = "debate_collaborate"

class DualExpertSkill:
    """
    双专家对话技能 - 支持降级策略
    
    执行优先级：
    1. 完整模式：启动两个独立子代理（不同模型）并行执行
    2. 降级模式：当前会话切换模型依次调用（子代理不可用时）
    """
    
    def __init__(self):
        self.config = {
            "model_brain": "openai/claude-sonnet-4-6",  # 大脑/专家 A
            "model_hand": "dashscope-coding/qwen3.5-plus",  # 手/专家 B
            "timeout_user_ack_ms": 300000,  # 用户确认超时 5 分钟
            "use_subagent": True,  # 优先使用子代理
            "fallback_to_session_switch": True  # 子代理失败时降级
        }
        self.use_subagent_fallback = False  # 运行时标记是否已降级

    async def _send_message_to_user(self, content: str):
        await message(action="send", message=content)

    async def _get_user_acknowledgement(self, prompt: str) -> bool:
        await self._send_message_to_user(prompt + " (回复 '继续' 或 '取消')")
        return True

    async def _spawn_expert_subagent(self, model: str, prompt: str, label: str) -> str:
        """
        启动子代理获取模型响应
        返回：模型响应文本，失败则抛出异常
        """
        result = await sessions_spawn(
            mode="run",
            runtime="subagent",
            model=model,
            task=f"{prompt}\n\n请直接输出你的回答，不要包含工具调用说明。",
            label=label
        )
        # 注意：实际实现需要等待子代理完成并获取结果
        # 这里简化处理，实际应该轮询 sessions_list 或用回调
        return "[子代理响应]"

    async def _call_model_with_fallback(self, model_name: str, prompt: str, role_label: str) -> str:
        """
        调用模型，带降级策略：
        1. 优先尝试子代理（独立模型实例）
        2. 失败则提示用户手动切换模型或使用当前会话
        """
        if self.config["use_subagent"] and not self.use_subagent_fallback:
            try:
                result = await self._spawn_expert_subagent(model_name, prompt, f"expert-{role_label}")
                return result
            except Exception as e:
                # 子代理失败，标记降级
                self.use_subagent_fallback = True
                await self._send_message_to_user(
                    f"⚠️ 子代理模式不可用（{str(e)}），降级到当前会话切换模型模式"
                )
        
        # 降级模式提示
        await self._send_message_to_user(
            f"📋 **{role_label} ({model_name})** 需要处理以下任务：\n\n{prompt}\n\n"
            f"*(子代理不可用，请手动切换模型或继续当前会话)*"
        )
        return f"[{model_name} 待处理]"

    async def _run_brain_hand_mode(self, task_description: str, auto_mode: bool):
        await self._send_message_to_user(f"💡 **模式 A: 大脑 + 手** - 启动任务：{task_description}")
        
        if self.use_subagent_fallback:
            await self._send_message_to_user("ℹ️ 当前使用降级模式：会话切换模型")

        # Brain (Claude) for planning
        await self._send_message_to_user(f"🧠 **大脑 ({self.config['model_brain']})** 正在规划...")
        brain_prompt = f"你是资深问题解决专家。请为以下任务制定详细计划：\n任务：{task_description}\n\n输出详细规划，包括步骤、关键考虑点和预期结果。"
        brain_response = await self._call_model_with_fallback(
            self.config['model_brain'], brain_prompt, "大脑"
        )
        await self._send_message_to_user(f"🧠 **大脑规划结果：**\n```\n{brain_response}\n```")

        if not auto_mode and not await self._get_user_acknowledgement("确认大脑规划是否满意？"):
            await self._send_message_to_user("🧠 任务被用户取消。")
            return

        # Hand (Qwen) for execution
        await self._send_message_to_user(f"✍️ **手 ({self.config['model_hand']})** 正在执行...")
        hand_prompt = f"你是高效执行者。根据以下规划和任务执行：\n\n任务：{task_description}\n规划：{brain_response}\n\n输出执行结果或初步内容。"
        hand_response = await self._call_model_with_fallback(
            self.config['model_hand'], hand_prompt, "手"
        )
        await self._send_message_to_user(f"✍️ **手执行结果：**\n```\n{hand_response}\n```")

        await self._send_message_to_user("✅ **大脑 + 手模式** 任务完成。")

    async def _run_debate_collaborate_mode(self, task_description: str, auto_mode: bool):
        await self._send_message_to_user(f"💡 **模式 B: 辩论 + 协作** - 启动任务：{task_description}")
        
        if self.use_subagent_fallback:
            await self._send_message_to_user("ℹ️ 当前使用降级模式：会话切换模型")

        # Expert A (Claude) initial analysis
        await self._send_message_to_user(f"🗣️ **专家 A ({self.config['model_brain']})** 正在分析...")
        expert_a_prompt = f"你是严谨分析师，从数据质量和风险角度分析：{task_description}\n\n输出初步分析。"
        expert_a_response = await self._call_model_with_fallback(
            self.config['model_brain'], expert_a_prompt, "专家 A"
        )
        await self._send_message_to_user(f"🗣️ **专家 A 分析：**\n```\n{expert_a_response}\n```")

        if not auto_mode and not await self._get_user_acknowledgement("继续让专家 B 补充？"):
            await self._send_message_to_user("🗣️ 任务被用户取消。")
            return

        # Expert B (Qwen) supplementary analysis
        await self._send_message_to_user(f"💬 **专家 B ({self.config['model_hand']})** 正在补充...")
        expert_b_prompt = f"你是创新策略师，从业务价值和机遇角度分析。参考专家 A 的分析：{expert_a_response}\n\n任务：{task_description}\n\n输出补充分析和观点。"
        expert_b_response = await self._call_model_with_fallback(
            self.config['model_hand'], expert_b_prompt, "专家 B"
        )
        await self._send_message_to_user(f"💬 **专家 B 分析：**\n```\n{expert_b_response}\n```")

        if not auto_mode and not await self._get_user_acknowledgement("继续总结？"):
            await self._send_message_to_user("💬 任务被用户取消。")
            return

        # Summary
        await self._send_message_to_user(f"📝 正在整合两位专家意见...")
        summary_prompt = f"整合以下分析，提出综合总结和建议：\n\n任务：{task_description}\n专家 A: {expert_a_response}\n专家 B: {expert_b_response}\n\n输出总结和最终建议。"
        final_summary = await self._call_model_with_fallback(
            self.config['model_brain'], summary_prompt, "总结"
        )
        await self._send_message_to_user(f"📝 **最终总结和建议：**\n```\n{final_summary}\n```")

        await self._send_message_to_user("✅ **辩论 + 协作模式** 任务完成。")

    async def run(self, command: str, mode: str, task: str):
        auto_mode = (command == "/duo_auto")
        step_mode = (command == "/duo_step")

        if mode == ExpertMode.BRAIN_HAND.value:
            await self._run_brain_hand_mode(task, auto_mode)
        elif mode == ExpertMode.DEBATE_COLLABORATE.value:
            await self._run_debate_collaborate_mode(task, auto_mode)
        else:
            await self._send_message_to_user("❌ 无效的专家模式。请选择 'brain_hand' 或 'debate_collaborate'。")
