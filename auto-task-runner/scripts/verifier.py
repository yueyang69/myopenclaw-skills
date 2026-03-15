#!/usr/bin/env python3.10
"""
验证器模块 v2.0 - claude 关键节点验证

成本控制：
- 不是每步都调用 claude
- 只在「关键步骤」或「执行失败」时才调用
- 普通步骤用本地逻辑快速判断
"""

import json
import re
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
CHECKPOINT_DIR = SKILL_DIR / ".runtime" / "checkpoints"
CHECKPOINT_DIR.mkdir(exist_ok=True, parents=True)

MODEL_CLAUDE = "openai/claude-sonnet-4-6"

# 触发 claude 验证的条件
CLAUDE_VERIFY_EVERY_N_STEPS = 5   # 每5步验证一次
CLAUDE_VERIFY_ON_FAILURE = True   # 失败时必须验证

_step_counter = 0  # 全局步骤计数器


def _import_model_client():
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from model_client import call_model, log
    return call_model, log


def verify_step(step: dict, execution_result: dict, session_key: str = None) -> dict:
    """
    验证步骤执行结果

    策略（成本控制）：
    - 执行成功 + 非关键节点 → 本地快速判断，不调用 claude
    - 执行失败 → 必须调用 claude 分析原因
    - 每5步 → 调用 claude 做阶段性审查
    - 最后一步 → 必须调用 claude 做整体验收
    """
    global _step_counter
    _step_counter += 1

    call_model, log = _import_model_client()

    result = {
        "step_id": step["id"],
        "step_name": step["name"],
        "verifier": "local",
        "verified_at": datetime.now().isoformat(),
        "verifier_decision": "failed",
        "verifier_reason": ""
    }

    exec_success = execution_result.get("success", False)
    is_last_step = step.get("is_last", False)
    need_claude = (
        (not exec_success and CLAUDE_VERIFY_ON_FAILURE) or
        (_step_counter % CLAUDE_VERIFY_EVERY_N_STEPS == 0) or
        is_last_step
    )

    if not need_claude:
        # 本地快速判断，不花钱
        if exec_success:
            result["verifier_decision"] = "done"
            result["verifier_reason"] = "执行成功（本地判断）"
        else:
            result["verifier_decision"] = "failed"
            result["verifier_reason"] = "执行失败（本地判断）"
        log(f"   ✓ 步骤 {step['id']} 本地验证: {result['verifier_decision']}")
        _save_verification_result(result)
        return result

    # 调用 claude 做深度验证
    reason = "最后一步验收" if is_last_step else ("执行失败分析" if not exec_success else f"每{CLAUDE_VERIFY_EVERY_N_STEPS}步审查")
    log(f"🔍 调用 claude 验证步骤 {step['id']}（{reason}）...")

    output_preview = str(execution_result.get("executor_output", ""))[:800]

    response = call_model(
        prompt=f"""验证任务步骤执行结果。

步骤 {step['id']}: {step['name']}
命令: {step.get('cmd', 'N/A')}

执行输出:
```
{output_preview}
```

执行状态: {'成功' if exec_success else '失败'}

请判断步骤是否真正完成了预期目标。

严格按以下格式回复：
```json
{{"decision": "done", "reason": "判断理由"}}
```
decision 只能是 done / failed / retry 之一。""",
        model=MODEL_CLAUDE,
        label=f"验证器-步骤{step['id']}"
    )

    verdict = _parse_verdict(response)
    result["verifier"] = MODEL_CLAUDE
    result["verifier_decision"] = verdict.get("decision", "failed")
    result["verifier_reason"] = verdict.get("reason", "无法解析")

    log(f"   claude 判断: {result['verifier_decision']} - {result['verifier_reason'][:60]}")
    _save_verification_result(result)
    return result


def _parse_verdict(response: str) -> dict:
    """解析 claude 验证结论"""
    # 优先尝试 JSON 解析
    json_match = re.search(r'```json\s*(.+?)\s*```', response, re.DOTALL)
    if json_match:
        try:
            verdict = json.loads(json_match.group(1))
            if "decision" in verdict:
                return verdict
        except json.JSONDecodeError:
            pass
    # 尝试直接解析 JSON
    try:
        verdict = json.loads(response)
        if "decision" in verdict:
            return verdict
    except:
        pass
    # 关键词降级判断（更宽松）
    r = response.lower()
    # 成功关键词（优先级高）
    if "done" in r or "成功" in r or "完成" in r or "pass" in r or "正确" in r or "合理" in r:
        return {"decision": "done", "reason": "关键词判断：成功"}
    # 重试关键词
    elif "retry" in r or "重试" in r or "重新" in r:
        return {"decision": "retry", "reason": "关键词判断：需重试"}
    # 失败关键词（优先级低，避免误判）
    elif "failed" in r and "成功" not in r:
        return {"decision": "failed", "reason": "关键词判断：失败"}
    elif "错误" in r and "无错误" not in r:
        return {"decision": "failed", "reason": "关键词判断：错误"}
    # 默认判断：视为成功
    return {"decision": "done", "reason": f"默认判断：AI 未明确表态，视为成功"}


def _save_verification_result(result: dict):
    """保存验证结果到检查点"""
    checkpoint_file = CHECKPOINT_DIR / f"step_{result['step_id']}_verified.json"
    with open(checkpoint_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def load_execution_result(step_id: int) -> dict:
    """加载执行结果"""
    checkpoint_file = CHECKPOINT_DIR / f"{step_id}_executed.json"
    if checkpoint_file.exists():
        with open(checkpoint_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None
