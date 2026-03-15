#!/usr/bin/env python3
"""
双专家编排引擎 v1.0

定位：执行类任务 —— 编码 / 架构设计 / 部署 / 批量操作。
有文件系统副作用，会执行 shell 命令。

纯分析 / 研讨 / 辩论请使用 dual-expert-chat。

四阶段流水线：
  Architect  : qwen 拆解步骤 → claude 盲审 → qwen 修订
  Inspector  : 本地检查依赖关系（零 token）
  Executor   : 逐步执行 shell 命令 + 按需 claude 验证
  Tester     : qwen 汇总 → claude 审查 → qwen 回应 → claude 最终认可

崩溃恢复：runtime_dir 由调用方传入（固定路径），重启后可从已完成
          checkpoint 继续，而非每次生成新时间戳目录。

成本控制：
  CLAUDE_VERIFY_EVERY_N=5  每 5 步调一次 claude 验证
  失败步骤 / 最后一步强制调用 claude
  每步执行后 GC + STEP_INTERVAL 秒等待，保护 2GB 内存

模型分工：
  MODEL_A (qwen)   — 主力执行，便宜
  MODEL_B (claude) — 关键验证，贵但准
"""

import gc
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

# ── 模型配置 ────────────────────────────────────────────────────
MODEL_A = os.environ.get("EXPERT_MODEL_A", "dashscope-coding/qwen3.5-plus")
MODEL_B = os.environ.get("EXPERT_MODEL_B", "openai/claude-sonnet-4-6")

STEP_INTERVAL        = int(os.environ.get("STEP_INTERVAL", "30"))
MAX_RETRIES          = 2
CLAUDE_VERIFY_EVERY_N = 5


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
# Phase 1: Architect
# ════════════════════════════════════════════════════════════════

def _architect(task: str, runtime_dir: Path) -> list:
    """
    qwen 拆解步骤 → claude 盲审 → qwen 修订（如需）→ 写 plans/
    返回步骤列表，保证非空。
    """
    _log("[Architect] qwen 拆解任务...")
    raw = call_model(
        prompt=(
            f"你是任务拆解专家。将以下任务拆解为可执行的步骤列表。\n\n"
            f"任务：{task}\n\n"
            "要求：每步独立可执行，耗时<8分钟，用 bash 命令或自然语言，3-10步。\n"
            "严格JSON输出：\n```json\n"
            '{"steps":[{"id":1,"name":"步骤名","cmd":"命令","dependencies":[],"estimated_minutes":2}]}\n'
            "```"
        ),
        model=MODEL_A, label="Architect-qwen"
    )
    steps = _parse_steps(raw)
    if not steps:
        steps = [{"id": 1, "name": task, "cmd": task,
                  "dependencies": [], "estimated_minutes": 5}]
    _log(f"[Architect] qwen 拆解 {len(steps)} 步")

    _log("[Architect] claude 盲审...")
    review = call_model(
        prompt=(
            f"审查任务拆解方案。\n原始任务：{task}\n\n"
            f"方案：\n```json\n{json.dumps({'steps': steps}, ensure_ascii=False, indent=2)}\n```\n\n"
            "方案合理回复 APPROVED+理由，需改回复 REVISE+问题+建议。"
        ),
        model=MODEL_B, label="Architect-claude"
    )
    gc.collect()

    if "REVISE" in review.upper():
        _log("[Architect] qwen 按审查意见修订...")
        raw2 = call_model(
            prompt=(
                f"根据审查意见修改方案。\n原始任务：{task}\n\n"
                f"原方案：\n```json\n{json.dumps({'steps': steps}, ensure_ascii=False, indent=2)}\n```\n\n"
                f"审查意见：\n{review}\n\n严格JSON输出：\n```json\n"
                '{"steps":[{"id":1,"name":"步骤名","cmd":"命令","dependencies":[],"estimated_minutes":2}]}\n'
                "```"
            ),
            model=MODEL_A, label="Architect-qwen-revise"
        )
        revised = _parse_steps(raw2)
        if revised:
            steps = revised
        gc.collect()

    _write_plan_md(task, steps, review, runtime_dir)
    _log(f"[Architect] 完成，{len(steps)} 步")
    return steps


# ════════════════════════════════════════════════════════════════
# Phase 2: Inspector
# ════════════════════════════════════════════════════════════════

def _inspector(steps: list) -> list:
    """纯逻辑检查依赖，零 token。移除引用了不存在步骤 ID 的依赖项。"""
    valid_ids = {s["id"] for s in steps}
    for s in steps:
        bad = [d for d in s.get("dependencies", []) if d not in valid_ids]
        if bad:
            _log(f"[Inspector] 步骤{s['id']} 移除无效依赖 {bad}")
            s["dependencies"] = [d for d in s["dependencies"] if d in valid_ids]
    return steps


# ════════════════════════════════════════════════════════════════
# Phase 3: Executor + Verifier
# ════════════════════════════════════════════════════════════════

def _execute_step(step: dict, checkpoint_dir: Path) -> dict:
    """本地执行 shell 命令，写 executed checkpoint。"""
    result = {
        "step_id": step["id"], "step_name": step["name"],
        "executor": "local", "executed_at": datetime.now().isoformat(),
        "success": False, "executor_output": ""
    }
    try:
        proc = subprocess.run(
            step["cmd"], shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=300
        )
        result["executor_output"] = (proc.stdout + proc.stderr).strip()
        result["success"] = (proc.returncode == 0)
    except subprocess.TimeoutExpired:
        result["executor_output"] = "执行超时（>5分钟）"
    except Exception as e:
        result["executor_output"] = f"执行异常：{e}"
    cp = checkpoint_dir / f"step_{step['id']}_executed.json"
    cp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _verify_step(
    step: dict,
    exec_result: dict,
    checkpoint_dir: Path,
    verify_counter: int
) -> dict:
    """验证单步结果（成本控制：按需调 claude）。verify_counter 由调用方传入，无全局状态。"""
    verdict = {
        "step_id": step["id"], "step_name": step["name"],
        "verifier": "local", "verified_at": datetime.now().isoformat(),
        "verifier_decision": "failed", "verifier_reason": ""
    }

    exec_ok  = exec_result.get("success", False)
    is_last  = step.get("is_last", False)
    need_claude = (
        (not exec_ok)
        or (verify_counter % CLAUDE_VERIFY_EVERY_N == 0)
        or is_last
    )

    if not need_claude:
        verdict["verifier_decision"] = "done" if exec_ok else "failed"
        verdict["verifier_reason"]   = "本地判断"
    else:
        tag = "最后一步" if is_last else ("失败分析" if not exec_ok else f"每{CLAUDE_VERIFY_EVERY_N}步审查")
        _log(f"[Verifier] claude 验证步骤{step['id']}（{tag}）")
        out = str(exec_result.get("executor_output", ""))[:800]
        resp = call_model(
            prompt=(
                f"验证步骤执行结果。\n步骤{step['id']}: {step['name']}\n"
                f"命令: {step.get('cmd', '')}\n"
                f"输出:\n```\n{out}\n```\n状态: {'成功' if exec_ok else '失败'}\n\n"
                "严格JSON：```json\n{\"decision\":\"done\",\"reason\":\"理由\"}\n```\n"
                "decision 只能是 done/failed/retry。"
            ),
            model=MODEL_B, label=f"Verifier-step{step['id']}"
        )
        gc.collect()
        v = _parse_verdict(resp)
        verdict["verifier"]           = MODEL_B
        verdict["verifier_decision"]  = v.get("decision", "failed")
        verdict["verifier_reason"]    = v.get("reason", "")

    _log(f"[Verifier] 步骤{step['id']} → {verdict['verifier_decision']}")
    cp = checkpoint_dir / f"step_{step['id']}_verified.json"
    cp.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    return verdict


# ════════════════════════════════════════════════════════════════
# Phase 4: Tester
# ════════════════════════════════════════════════════════════════

def _tester(task: str, runtime_dir: Path) -> dict:
    """
    qwen 汇总 → claude 审查 → qwen 回应 → claude 最终认可 → Report.md
    空 checkpoint 时正确报告 status=failed，不误报 success。
    """
    checkpoint_dir = runtime_dir / "checkpoints"
    reports_dir    = runtime_dir / "reports"
    reports_dir.mkdir(exist_ok=True, parents=True)

    cps = []
    if checkpoint_dir.exists():
        for f in sorted(checkpoint_dir.glob("step_*_verified.json")):
            try:
                cps.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass

    # 空 checkpoint → 明确失败，不误报 success
    if not cps:
        return {
            "task": task, "status": "failed",
            "total": 0, "completed": 0, "failed": 0,
            "summary": "无步骤执行记录", "final": "",
            "created_at": datetime.now().isoformat()
        }

    completed  = sum(1 for c in cps if c.get("verifier_decision") == "done")
    failed_cnt = len(cps) - completed
    cp_preview = json.dumps(cps, ensure_ascii=False, indent=2)[:2000]

    _log("[Tester] qwen 汇总...")
    summary = call_model(
        prompt=(
            f"任务'{task}'执行完成，汇总结果。\n"
            f"完成:{completed}/{len(cps)}，失败:{failed_cnt}\n"
            f"检查点:\n```json\n{cp_preview}\n```\n\n"
            "总结：1.整体是否成功？2.哪些步骤好？3.哪些有问题？"
        ),
        model=MODEL_A, label="Tester-qwen-summary"
    )
    gc.collect()

    _log("[Tester] claude 审查...")
    review = call_model(
        prompt=f"审查任务总结，给出验收意见。\n\n{summary}\n\n判断：1.可否标记完成？2.是否需要返工？3.最终结论？",
        model=MODEL_B, label="Tester-claude-review"
    )
    gc.collect()

    _log("[Tester] qwen 回应...")
    response = call_model(
        prompt=f"Claude验收意见：\n{review}\n\n请回应并生成最终报告摘要（200字以内）。",
        model=MODEL_A, label="Tester-qwen-response"
    )

    _log("[Tester] claude 最终认可...")
    final = call_model(
        prompt=f"最终验收：\n{response}\n\n如认可，回复'验收通过'并确认报告可发布。",
        model=MODEL_B, label="Tester-claude-final"
    )
    gc.collect()

    if failed_cnt == 0:
        status = "success"
    elif failed_cnt < len(cps) * 0.5:
        status = "partial_success"
    else:
        status = "failed"

    report = {
        "task": task, "status": status,
        "total": len(cps), "completed": completed, "failed": failed_cnt,
        "summary": summary, "final": final,
        "created_at": datetime.now().isoformat()
    }
    _write_report_md(task, report, cps, reports_dir)
    return report


# ════════════════════════════════════════════════════════════════
# 主流程入口
# ════════════════════════════════════════════════════════════════

def run_four_stage(task: str, runtime_dir: Path) -> dict:
    """
    四阶段主流程。

    runtime_dir 由调用方指定（固定路径），重启后传入相同路径可从
    已完成 checkpoint 继续，而非每次生成新时间戳目录。

    返回: {status, total_steps, completed_steps, failed_steps, report_path, elapsed}
    """
    started = datetime.now()

    for d in ["checkpoints", "plans", "reports", "logs"]:
        (runtime_dir / d).mkdir(exist_ok=True, parents=True)

    checkpoint_dir = runtime_dir / "checkpoints"

    # Phase 1
    steps = _architect(task, runtime_dir)
    if not steps:
        return {
            "status": "failed", "total_steps": 0,
            "completed_steps": 0, "failed_steps": 0,
            "report_path": "", "elapsed": "0m0s"
        }
    steps[-1]["is_last"] = True

    # Phase 2
    steps = _inspector(steps)
    total = len(steps)
    _log(f"[Engine] {total} 步，开始执行")

    # 读取已完成的 checkpoint，支持崩溃恢复
    completed_ids = set()
    for cp_file in checkpoint_dir.glob("step_*_verified.json"):
        try:
            data = json.loads(cp_file.read_text(encoding="utf-8"))
            if data.get("verifier_decision") == "done":
                completed_ids.add(data["step_id"])
        except Exception:
            pass
    if completed_ids:
        _log(f"[Engine] 崩溃恢复：已完成步骤 {sorted(completed_ids)}")

    completed  = list(completed_ids)
    failed_ids = []
    verify_counter = 0

    for i, step in enumerate(steps):
        if step["id"] in completed_ids:
            _log(f"[Engine] 跳过步骤{step['id']}（已完成）")
            continue

        _log(f"[Engine] [{i+1}/{total}] {step['name']}")

        if not all(d in completed for d in step.get("dependencies", [])):
            _log(f"[Engine] 跳过步骤{step['id']}（依赖未满足）")
            failed_ids.append(step["id"])
            continue

        verdict = None
        for attempt in range(1, MAX_RETRIES + 1):
            exec_result = _execute_step(step, checkpoint_dir)
            verify_counter += 1
            verdict = _verify_step(step, exec_result, checkpoint_dir, verify_counter)
            if verdict["verifier_decision"] == "done":
                break
            if verdict["verifier_decision"] == "retry" and attempt < MAX_RETRIES:
                _log(f"[Engine] 重试步骤{step['id']} ({attempt+1}/{MAX_RETRIES})")
                gc.collect()
                time.sleep(10)
            else:
                break

        if verdict and verdict["verifier_decision"] == "done":
            completed.append(step["id"])
        else:
            failed_ids.append(step["id"])

        if i < total - 1:
            gc.collect()
            time.sleep(STEP_INTERVAL)

    # Phase 4
    report = _tester(task, runtime_dir)
    safe = re.sub(r'[^\w\u4e00-\u9fff-]', '-', task)[:30]
    report_path = str(runtime_dir / "reports" / f"{safe}-report.md")

    elapsed_s = (datetime.now() - started).total_seconds()
    elapsed   = f"{int(elapsed_s//60)}m{int(elapsed_s%60)}s"
    _log(f"[Engine] 完成 | {report['status']} | {elapsed}")

    return {
        "status":          report["status"],
        "total_steps":     total,
        "completed_steps": len(completed),
        "failed_steps":    len(failed_ids),
        "report_path":     report_path,
        "elapsed":         elapsed,
    }


# ════════════════════════════════════════════════════════════════
# 辅助：解析 / 写文件
# ════════════════════════════════════════════════════════════════

def _parse_steps(text: str) -> list:
    m = re.search(r'```json\s*(.+?)\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1)).get("steps", [])
        except Exception:
            pass
    try:
        return json.loads(text).get("steps", [])
    except Exception:
        pass
    return []


def _parse_verdict(text: str) -> dict:
    m = re.search(r'```json\s*(.+?)\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    t = text.lower()
    if "done" in t or "成功" in t or "完成" in t:
        return {"decision": "done",  "reason": "关键词判断"}
    if "retry" in t or "重试" in t:
        return {"decision": "retry", "reason": "关键词判断"}
    return {"decision": "failed", "reason": "无法解析"}


def _write_plan_md(task: str, steps: list, review: str, runtime_dir: Path):
    safe  = re.sub(r'[^\w\u4e00-\u9fff-]', '-', task)[:30]
    plans_dir = runtime_dir / "plans"
    plans_dir.mkdir(exist_ok=True, parents=True)
    f = plans_dir / f"{safe}.md"
    lines = [
        f"# PlanList - {task}", "",
        "| ID | 步骤 | 依赖 | 耗时 | 命令 |",
        "|----|------|------|------|------|"
    ] + [
        f"| {s['id']} | {s['name']} | {s.get('dependencies',[])} "
        f"| {s.get('estimated_minutes','?')}min | `{str(s.get('cmd',''))[:40]}` |"
        for s in steps
    ] + [
        "", "## 审查意见", "", review[:300], "",
        "*qwen3.5-plus + claude-sonnet-4-6 协作生成*"
    ]
    f.write_text("\n".join(lines), encoding="utf-8")


def _write_report_md(task: str, report: dict, cps: list, reports_dir: Path):
    safe = re.sub(r'[^\w\u4e00-\u9fff-]', '-', task)[:30]
    f    = reports_dir / f"{safe}-report.md"
    icon = {"success": "OK", "partial_success": "PARTIAL", "failed": "FAIL"}
    lines = [
        f"# Report - {task}", "",
        f"状态: [{icon.get(report['status'],'?')}] {report['status']}",
        f"完成: {report['completed']}/{report['total']}", "",
        "| ID | 步骤 | 状态 | 原因 |", "|-----|------|------|------|"
    ] + [
        f"| {c['step_id']} | {c.get('step_name','')} "
        f"| {c.get('verifier_decision','')} | {c.get('verifier_reason','')[:50]} |"
        for c in cps
    ] + [
        "", "## 总结", "", report.get("summary", ""), "",
        "## 最终验收", "", report.get("final", ""), "",
        "*qwen3.5-plus + claude-sonnet-4-6 协作生成*"
    ]
    f.write_text("\n".join(lines), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# ExpertOrchestrator 主类（Layer 2 统一接口）
# ════════════════════════════════════════════════════════════════

class ExpertOrchestrator:
    """
    双专家编排引擎 v1.0

    Layer 2 统一接口：
      输入：task_description (str), output_dir (Path)
      输出：{ status, report_path, elapsed }

    只做执行类任务。纯分析/研讨/辩论请使用 dual-expert-chat。
    """

    def run(
        self,
        task_description: str,
        output_dir: Path,
        **_
    ) -> dict:
        """
        执行四阶段引擎。

        output_dir 由调用方（Layer 1）指定固定路径，
        崩溃重启后传入相同 output_dir 即可从断点继续。
        """
        _log(f"=== ExpertOrchestrator | task={task_description[:50]} ===")
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)

        result = run_four_stage(task_description, output_dir)

        sep  = "─" * 60
        icon = {"success": "OK", "partial_success": "PARTIAL", "failed": "FAIL"}
        lines = [
            "", sep, "双专家编排报告（四阶段引擎）", sep,
            f"任务：{task_description}", "",
            f"[{icon.get(result['status'],'?')}] {result['status']}",
            f"步骤：{result['completed_steps']}/{result['total_steps']} 完成",
            f"耗时：{result['elapsed']}",
        ]
        rp = result.get("report_path", "")
        if rp and Path(rp).exists():
            lines += [
                "", f"报告：{rp}", "", sep, "Report.md", sep,
                Path(rp).read_text(encoding="utf-8")
            ]
        lines.append(sep)
        print("\n".join(lines))

        return {
            "status":      result["status"],
            "report_path": result["report_path"],
            "elapsed":     result["elapsed"],
        }
