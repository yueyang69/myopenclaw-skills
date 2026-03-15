#!/usr/bin/env python3
"""
双专家研讨技能 v4.0

定位：纯分析 / 研讨 / 辩论，无副作用，无 shell 执行。
两位专家各自独立选取视角，互不知晓对方立场（真盲审），
最后由主持人综合共识与分歧。

模式：
  blind_review        - 真盲审：两位专家独立选取视角后独立分析
  debate_collaborate  - 同 blind_review（兼容旧命令）
  debate              - 正反方辩论：强制 A 支持、B 反对，主持人裁定
  panel               - 圆桌研讨：A/B 各提 3 个子问题，交叉回答，主持人汇总

模型分工：
  MODEL_A (qwen)   - 主力，便宜
  MODEL_B (claude) - 关键验证，贵但准
"""

import gc
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

# ── 模型配置 ────────────────────────────────────────────────────
MODEL_A = os.environ.get("EXPERT_MODEL_A", "dashscope-coding/qwen3.5-plus")
MODEL_B = os.environ.get("EXPERT_MODEL_B", "openai/claude-sonnet-4-6")


# ── 工具函数 ────────────────────────────────────────────────────
def _log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def call_model(prompt: str, model: str, label: str = "", timeout: int = 300) -> str:
    """三级降级：sessions_spawn → sessions_send → openclaw cmd"""
    _log(f"[{label}] {model.split('/')[-1]}")

    try:
        from openclaw.tools.sessions_spawn import sessions_spawn
        r = sessions_spawn(
            mode="run", runtime="subagent", model=model,
            task=prompt + "\n\n请直接输出答案。",
            label=label, runTimeoutSeconds=timeout, cleanup="delete"
        )
        return r.get("result", "[无响应]")
    except Exception:
        pass

    try:
        from openclaw.tools.sessions_send import sessions_send
        r = sessions_send(
            message=prompt + "\n\n请直接输出答案。",
            model=model, timeoutSeconds=timeout
        )
        return r if isinstance(r, str) else str(r)
    except Exception:
        pass

    # 保底：openclaw 命令行（finally 确保临时文件必定清理）
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as f:
            f.write(prompt)
            tmp = f.name
        r = subprocess.run(
            ["openclaw", "send", "--model", model, f"@{tmp}"],
            capture_output=True, text=True, timeout=timeout
        )
        if r.returncode == 0:
            return r.stdout.strip()
        return f"[{model} 失败]"
    except Exception as e:
        return f"[{model} 错误: {e}]"
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


# ════════════════════════════════════════════════════════════════
# DualExpertSkill 主类
# ════════════════════════════════════════════════════════════════

class DualExpertSkill:
    """
    双专家研讨技能 v4.0

    只做分析 / 研讨 / 辩论，无文件系统写入，无 shell 执行。
    需要执行任务（编码/架构/部署）请使用 expert-orchestrator。
    """

    def run(self, mode: str, question: str, **_) -> str:
        _log(f"=== DualExpert | mode={mode} ===")
        dispatch = {
            "blind_review":       self._run_blind_review,
            "debate_collaborate": self._run_blind_review,  # 兼容旧命令
            "debate":             self._run_debate,
            "panel":              self._run_panel,
        }
        fn = dispatch.get(mode)
        if fn is None:
            return (
                f"未知模式：{mode}。"
                "支持 blind_review / debate / panel"
            )
        return fn(question)

    # ── 模式1：真盲审 ──────────────────────────────────────────
    def _run_blind_review(self, question: str) -> str:
        """
        真盲审：A 和 B 各自独立选取分析视角，互不知晓对方立场。
        主持人综合后给出共识 / 分歧 / 建议。
        """
        sep = "─" * 60
        print(f"\n{sep}\n盲审模式：两位专家各自独立选取视角\n{sep}")

        # A 独立选取视角 + 分析（不给任何视角提示）
        _log("专家A 独立选取视角并分析...")
        ana_a = call_model(
            prompt=(
                "你是独立专业顾问。\n"
                "请先自行确定一个你认为最有价值的分析视角，"
                "然后从该视角对以下问题进行深度分析。\n\n"
                f"问题：{question}\n\n"
                "输出格式：\n"
                "## 我的分析视角\n[一句话说明你选择的视角]\n\n"
                "## 核心判断\n## 主要论据（3条）\n## 潜在风险\n## 建议"
            ),
            model=MODEL_A, label="专家A"
        )
        gc.collect()

        # B 独立选取视角 + 分析（不给任何视角提示，也不知道 A 选了什么）
        _log("专家B 独立选取视角并分析...")
        ana_b = call_model(
            prompt=(
                "你是独立专业顾问。\n"
                "请先自行确定一个你认为最有价值的分析视角，"
                "然后从该视角对以下问题进行深度分析。\n\n"
                f"问题：{question}\n\n"
                "输出格式：\n"
                "## 我的分析视角\n[一句话说明你选择的视角]\n\n"
                "## 核心判断\n## 主要论据（3条）\n## 潜在风险\n## 建议"
            ),
            model=MODEL_B, label="专家B"
        )
        gc.collect()

        # 主持人综合（看到两份报告后综合）
        _log("主持人综合两份独立报告...")
        synthesis = call_model(
            prompt=(
                "你是研讨会主持人，综合两份完全独立的专家分析报告。\n"
                f"原始问题：{question}\n\n"
                f"专家A 报告：\n{ana_a}\n\n"
                f"专家B 报告：\n{ana_b}\n\n"
                "请输出：\n"
                "## 共识\n## 分歧\n## 综合建议\n## 需要进一步确认的问题"
            ),
            model=MODEL_A, label="主持人-综合"
        )

        out = (
            f"\n{sep}\n盲审报告\n{sep}\n\n"
            f"问题：{question}\n\n"
            f"{sep}\n专家A 分析：\n{sep}\n{ana_a}\n\n"
            f"{sep}\n专家B 分析：\n{sep}\n{ana_b}\n\n"
            f"{sep}\n综合报告：\n{sep}\n{synthesis}\n{sep}\n"
        )
        print(out)
        return out

    # ── 模式2：辩论 ────────────────────────────────────────────
    def _run_debate(self, question: str) -> str:
        """
        正反方辩论：A 强制持支持/正方立场，B 强制持反对/负方立场。
        最后主持人裁定哪方论据更充分。
        """
        sep = "─" * 60
        print(f"\n{sep}\n辩论模式：正方 vs 反方\n{sep}")

        _log("正方（qwen）陈述...")
        pro = call_model(
            prompt=(
                "你是辩论赛正方，你必须支持以下命题，给出最有力的论据。\n\n"
                f"命题：{question}\n\n"
                "输出：\n## 正方立场\n## 核心论点（3条）\n## 支撑证据\n## 预判反方攻击及反驳"
            ),
            model=MODEL_A, label="正方"
        )
        gc.collect()

        _log("反方（claude）陈述...")
        con = call_model(
            prompt=(
                "你是辩论赛反方，你必须反对以下命题，给出最有力的论据。\n\n"
                f"命题：{question}\n\n"
                "输出：\n## 反方立场\n## 核心论点（3条）\n## 支撑证据\n## 预判正方攻击及反驳"
            ),
            model=MODEL_B, label="反方"
        )
        gc.collect()

        _log("裁判综合裁定...")
        verdict = call_model(
            prompt=(
                "你是公正裁判，评判以下辩论并给出裁定。\n\n"
                f"命题：{question}\n\n"
                f"正方：\n{pro}\n\n反方：\n{con}\n\n"
                "请输出：\n"
                "## 正方得分与优势\n## 反方得分与优势\n"
                "## 裁定结果\n## 理由\n## 综合结论（超越正反两方的客观判断）"
            ),
            model=MODEL_A, label="裁判"
        )

        out = (
            f"\n{sep}\n辩论报告\n{sep}\n\n"
            f"命题：{question}\n\n"
            f"{sep}\n正方（qwen）：\n{sep}\n{pro}\n\n"
            f"{sep}\n反方（claude）：\n{sep}\n{con}\n\n"
            f"{sep}\n裁判裁定：\n{sep}\n{verdict}\n{sep}\n"
        )
        print(out)
        return out

    # ── 模式3：圆桌研讨 ────────────────────────────────────────
    def _run_panel(self, question: str) -> str:
        """
        圆桌研讨：A 和 B 各自提出 3 个最关心的子问题，
        然后交叉回答对方的问题，最后主持人汇总。
        """
        sep = "─" * 60
        print(f"\n{sep}\n圆桌研讨模式\n{sep}")

        # 各自提问
        _log("专家A 提出关切问题...")
        q_a = call_model(
            prompt=(
                "你是领域专家，针对以下议题，列出你最关心的 3 个核心问题。\n\n"
                f"议题：{question}\n\n"
                "输出格式（纯文本，每行一个问题）：\n"
                "Q1: xxx\nQ2: xxx\nQ3: xxx"
            ),
            model=MODEL_A, label="专家A-提问"
        )
        gc.collect()

        _log("专家B 提出关切问题...")
        q_b = call_model(
            prompt=(
                "你是领域专家，针对以下议题，列出你最关心的 3 个核心问题。\n\n"
                f"议题：{question}\n\n"
                "输出格式（纯文本，每行一个问题）：\n"
                "Q1: xxx\nQ2: xxx\nQ3: xxx"
            ),
            model=MODEL_B, label="专家B-提问"
        )
        gc.collect()

        # 交叉回答：A 回答 B 的问题，B 回答 A 的问题
        _log("专家A 回答专家B 的问题...")
        ans_a = call_model(
            prompt=(
                f"请回答以下问题（背景议题：{question}）\n\n"
                f"{q_b}\n\n"
                "请逐一作答，每个答案 100 字以内。"
            ),
            model=MODEL_A, label="专家A-回答"
        )
        gc.collect()

        _log("专家B 回答专家A 的问题...")
        ans_b = call_model(
            prompt=(
                f"请回答以下问题（背景议题：{question}）\n\n"
                f"{q_a}\n\n"
                "请逐一作答，每个答案 100 字以内。"
            ),
            model=MODEL_B, label="专家B-回答"
        )
        gc.collect()

        # 主持人汇总
        _log("主持人汇总圆桌成果...")
        summary = call_model(
            prompt=(
                "你是圆桌研讨主持人，整理研讨成果。\n\n"
                f"议题：{question}\n\n"
                f"专家A 的问题：\n{q_a}\n\n"
                f"专家B 对 A 问题的回答：\n{ans_b}\n\n"
                f"专家B 的问题：\n{q_b}\n\n"
                f"专家A 对 B 问题的回答：\n{ans_a}\n\n"
                "请输出：\n"
                "## 研讨摘要\n## 关键洞察\n## 尚存争议\n## 行动建议"
            ),
            model=MODEL_A, label="主持人-汇总"
        )

        out = (
            f"\n{sep}\n圆桌研讨报告\n{sep}\n\n"
            f"议题：{question}\n\n"
            f"{sep}\n专家A 的问题：\n{sep}\n{q_a}\n\n"
            f"{sep}\n专家B 对 A 问题的回答：\n{sep}\n{ans_b}\n\n"
            f"{sep}\n专家B 的问题：\n{sep}\n{q_b}\n\n"
            f"{sep}\n专家A 对 B 问题的回答：\n{sep}\n{ans_a}\n\n"
            f"{sep}\n主持人汇总：\n{sep}\n{summary}\n{sep}\n"
        )
        print(out)
        return out
