# -*- coding: utf-8 -*-
"""cadspec —— 中国建筑制图规范知识库 + DXF 合规审查。

两层设计：
  Clause  知识层：条文原文 + 人话解读 + 机器可读约束（data/kb/*.yaml）
  Rule    规则层：可执行的检查，通过 cite 指回 Clause（data/rules/*.yaml）

同一条 Clause 可以支撑多条 Rule；一条 Rule 必须能溯源到 Clause，
或显式声明 cite=None 并在报告里标注"无规范依据"。
"""
from .model import Clause, Finding, ReviewContext, Rule, Severity, Status

__version__ = "0.1.0"
__all__ = ["Clause", "Rule", "Finding", "ReviewContext", "Severity", "Status", "__version__"]
