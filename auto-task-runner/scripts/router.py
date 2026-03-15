#!/usr/bin/env python3
"""
router.py - Layer 1 两步任务判断

第一步：能直接执行吗？（纯关键词，零 token）
  - 有明确 shell 关键词（清理/删除/压缩/检查/统计/查看）
  - 且没有「分析/设计/调研/生成/编写」等思考词
  → 返回 ("direct", shell_cmd)

第二步：哪个 skill 最合适？（关键词匹配）
  - 匹配 router.json 中的规则
  → 返回 ("skill", skill_name)
  - 未匹配 → 兜底 ("skill", "dual-expert-chat")
"""

import json
import re
from pathlib import Path
from typing import Tuple

SKILL_DIR = Path(__file__).parent.parent
ROUTER_CONFIG = SKILL_DIR / "config" / "router.json"


def _load_router_config() -> dict:
    """加载路由配置"""
    try:
        return json.loads(ROUTER_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {"direct_keywords": [], "skill_routes": []}


def _is_direct_task(desc: str) -> bool:
    """
    判断是否为直接执行任务（第一步）
    
    条件：
      - 有明确 shell 关键词
      - 且没有思考类关键词
    """
    direct_kw = ["清理", "删除", "压缩", "检查", "统计", "查看", "列出", "显示", "备份", "复制", "移动"]
    think_kw  = ["分析", "设计", "调研", "生成", "编写", "实现", "开发", "优化", "改进", "重构"]
    
    has_direct = any(kw in desc for kw in direct_kw)
    has_think  = any(kw in desc for kw in think_kw)
    
    return has_direct and not has_think


def _extract_shell_cmd(desc: str) -> str:
    """
    从任务描述中提取 shell 命令
    
    示例：
      "清理 /var/log 下30天前的日志" → "find /var/log -mtime +30 -delete"
      "检查磁盘空间" → "df -h"
      "压缩 workspace 下的大文件" → "find workspace -size +100M -exec gzip {} +"
    """
    # 简单启发式规则
    if "清理" in desc or "删除" in desc:
        if "日志" in desc:
            return "find /var/log -mtime +30 -type f -delete"
        elif "临时" in desc or "tmp" in desc:
            return "rm -rf /tmp/*"
        else:
            return "find . -type f -mtime +30 -delete"
    
    if "检查" in desc or "查看" in desc:
        if "磁盘" in desc:
            return "df -h"
        elif "内存" in desc:
            return "free -h"
        elif "进程" in desc:
            return "ps aux"
        else:
            return "ls -lh"
    
    if "压缩" in desc:
        return "gzip -r ."
    
    if "统计" in desc:
        return "wc -l *"
    
    # 默认
    return "echo '无法识别的直接执行任务'"


def route(description: str) -> Tuple[str, str]:
    """
    主路由函数
    
    返回 (action, target)：
      - action="direct", target=shell_cmd
      - action="skill", target=skill_name
    """
    # 第一步：判断是否直接执行
    if _is_direct_task(description):
        cmd = _extract_shell_cmd(description)
        return ("direct", cmd)
    
    # 第二步：关键词匹配 skill
    config = _load_router_config()
    skill_routes = config.get("skill_routes", [])
    
    for route_rule in skill_routes:
        keywords = route_rule.get("keywords", [])
        skill_name = route_rule.get("skill")
        
        # 任意关键词匹配即可
        if any(kw in description for kw in keywords):
            return ("skill", skill_name)
    
    # 未匹配 → 兜底
    return ("skill", "dual-expert-chat")


if __name__ == "__main__":
    # 测试
    test_cases = [
        "清理30天前的日志",
        "检查磁盘空间",
        "分析服务器架构，提出优化方案",
        "调研主流向量数据库",
        "合并 reports 下的 PDF",
        "审查 PR 代码",
        "修复 bug：内存泄漏",
    ]
    
    for desc in test_cases:
        action, target = route(desc)
        print(f"{desc:30} → {action:8} {target}")

