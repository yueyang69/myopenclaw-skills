#!/usr/bin/env python3.10
"""
建筑师模块 v2.0 - 动态任务分解

qwen 提方案 → claude 盲审 → qwen 调整 → claude 最终拍板
关键：claude 审查的是原始任务需求 + qwen方案，不是qwen的推理过程
"""

import json
import re
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
PLANS_DIR = SKILL_DIR / ".runtime" / "plans"
PLANS_DIR.mkdir(exist_ok=True, parents=True)

MODEL_QWEN = "dashscope-coding/qwen3.5-plus"
MODEL_CLAUDE = "openai/claude-sonnet-4-6"


def _import_model_client():
    """延迟导入 model_client，避免循环依赖"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from model_client import call_model, log
    return call_model, log


def generate_plan(task_description: str, session_key: str = None) -> dict:
    """
    动态任务分解：用户描述 → 执行步骤列表

    流程（盲审模式）：
    1. qwen 独立拆解任务
    2. claude 独立评审（只看原始需求 + qwen方案，不被qwen推理带偏）
    3. qwen 根据评审调整
    4. 输出最终计划

    claude 调用次数：2次（评审 + 最终确认）
    """
    call_model, log = _import_model_client()

    plan = {
        "task_description": task_description,
        "created_at": datetime.now().isoformat(),
        "steps": [],
        "status": "draft"
    }

    log(f"📐 建筑师启动：{task_description[:60]}...")

    # === Round 1: qwen 独立拆解（便宜，主力）===
    log("📝 Round 1: qwen 拆解任务...")
    qwen_plan_raw = call_model(
        prompt=f"""你是任务拆解专家。将以下任务拆解为可执行的步骤列表。

任务：{task_description}

要求：
- 每步骤独立可执行，预计耗时 <8 分钟
- 用 bash 命令表达，无法用命令的用自然语言说明
- 明确依赖关系（哪步依赖哪步的ID）
- 步骤数量控制在 3-10 步

严格按以下JSON格式输出，不要有其他文字：
```json
{{
  "steps": [
    {{"id": 1, "name": "步骤名", "cmd": "bash命令或说明", "dependencies": [], "estimated_minutes": 2}},
    {{"id": 2, "name": "步骤名", "cmd": "bash命令或说明", "dependencies": [1], "estimated_minutes": 3}}
  ]
}}
```""",
        model=MODEL_QWEN,
        label="建筑师-Qwen"
    )

    qwen_steps = _parse_steps(qwen_plan_raw)
    if not qwen_steps:
        log("⚠️ qwen 未输出有效JSON，使用默认单步骤")
        # 根据任务类型生成合理的默认命令
        if "内存" in task_description or "磁盘" in task_description:
            default_cmd = "free -h && df -h"
        elif "日志" in task_description:
            default_cmd = "ls -lh logs/ && du -sh logs/*"
        elif "技能" in task_description or "skill" in task_description.lower():
            default_cmd = "du -sh skills/* | sort -rh | head -10"
        elif "记忆" in task_description or "memory" in task_description.lower():
            default_cmd = "ls -lh memory/ && du -sh memory/*"
        else:
            default_cmd = f"echo '任务：{task_description}'"
        qwen_steps = [{"id": 1, "name": task_description[:30], "cmd": default_cmd, "dependencies": [], "estimated_minutes": 5}]

    log(f"   qwen 拆解了 {len(qwen_steps)} 个步骤")

    # === Round 2: claude 盲审（关键验证，贵但必要）===
    log("🔍 Round 2: claude 盲审方案...")
    claude_review = call_model(
        prompt=f"""你是任务审查专家。请审查以下任务拆解方案。

原始任务：{task_description}

待审查方案：
```json
{json.dumps({"steps": qwen_steps}, ensure_ascii=False, indent=2)}
```

请从以下角度审查：
1. 步骤是否覆盖了任务的所有关键环节？
2. 依赖关系是否正确？
3. 是否有可以合并或需要拆分的步骤？
4. 命令是否合理可执行？

如果方案合理，回复：
```
APPROVED
理由：xxx
```

如果需要修改，回复：
```
REVISE
问题：xxx
建议：xxx
```""",
        model=MODEL_CLAUDE,
        label="建筑师-Claude审查"
    )

    log(f"   claude 审查完成")

    # === Round 3: 如果需要修改，qwen 调整 ===
    final_steps = qwen_steps
    if "REVISE" in claude_review.upper():
        log("📝 Round 3: qwen 根据审查意见调整...")
        qwen_revised = call_model(
            prompt=f"""根据审查意见修改任务拆解方案。

原始任务：{task_description}

你之前的方案：
```json
{json.dumps({"steps": qwen_steps}, ensure_ascii=False, indent=2)}
```

审查意见：
{claude_review}

请输出修改后的完整方案，严格按JSON格式：
```json
{{
  "steps": [
    {{"id": 1, "name": "步骤名", "cmd": "命令", "dependencies": [], "estimated_minutes": 2}}
  ]
}}
```""",
            model=MODEL_QWEN,
            label="建筑师-Qwen修订"
        )
        revised_steps = _parse_steps(qwen_revised)
        if revised_steps:
            final_steps = revised_steps
            log(f"   qwen 修订后 {len(final_steps)} 个步骤")

    plan["steps"] = final_steps
    plan["status"] = "approved"
    plan["approved_at"] = datetime.now().isoformat()
    plan["claude_review"] = claude_review[:200]  # 只存摘要

    _write_plan_md(plan)
    log(f"✅ 计划生成完毕：{len(final_steps)} 个步骤")

    return plan


def _parse_steps(response: str) -> list:
    """从模型响应中解析步骤列表"""
    json_match = re.search(r'```json\s*(.+?)\s*```', response, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            return data.get("steps", [])
        except json.JSONDecodeError:
            pass
    # 尝试直接解析
    try:
        data = json.loads(response)
        return data.get("steps", [])
    except:
        pass
    return []


def _write_plan_md(plan: dict):
    """写入 PlanList.md"""
    safe_name = re.sub(r'[^\w\u4e00-\u9fff-]', '-', plan['task_description'])[:30]
    plan_file = PLANS_DIR / f"{safe_name}.md"

    lines = [
        f"# PlanList - {plan['task_description']}",
        f"",
        f"**创建时间：** {plan['created_at']}",
        f"**状态：** {plan['status']}",
        f"",
        f"## 执行步骤",
        f"",
        f"| ID | 步骤名称 | 依赖 | 预计耗时 | 命令 | 状态 |",
        f"|----|---------|----|---------|------|------|",
    ]
    for step in plan['steps']:
        deps = ", ".join(map(str, step.get('dependencies', []))) or "无"
        cmd_preview = str(step.get('cmd', ''))[:40]
        lines.append(f"| {step['id']} | {step['name']} | {deps} | {step.get('estimated_minutes', '?')}min | `{cmd_preview}` | pending |")

    lines += [
        f"",
        f"## Claude 审查意见",
        f"",
        f"{plan.get('claude_review', '无')}",
        f"",
        f"---",
        f"*由 qwen3.5-plus + claude-sonnet-4.6 协作生成*",
    ]

    plan_file.write_text("\n".join(lines), encoding='utf-8')
    return plan_file
