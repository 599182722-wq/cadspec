# -*- coding: utf-8 -*-
"""数据模型：条文卡片、规则、审查发现。

设计要点见 docs/design.md：
  知识层 (Clause) 与规则层 (Rule) 分开存放，Rule 通过 cite 指回 Clause。
  同一条 Cluase 可以支撑多条 Rule；一条 Rule 必须能溯源到 Clause（或显式标注无依据）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"      # 违反"应/必须/不得"类条文
    WARNING = "warning"  # 违反"宜/不宜"类条文，或依赖未确定的出图条件
    INFO = "info"        # 建议性、或无法自动判定需人工确认

    @property
    def rank(self) -> int:
        return {"error": 0, "warning": 1, "info": 2}[self.value]


class Status(str, Enum):
    EFFECTIVE = "effective"     # 现行
    SUPERSEDED = "superseded"   # 被替代（仍可能对既有工程有效）
    ABOLISHED = "abolished"     # 已废止
    DRAFT = "draft"             # 草案/征求意见稿


@dataclass
class Clause:
    """一条规范条文。"""

    clause_id: str                # "GBT50001-2017/13.0.1"
    standard: str                 # "GB/T 50001-2017"
    standard_name: str            # "房屋建筑制图统一标准"
    chapter: str                  # "13 计算机辅助制图文件图层"
    verbatim: str                 # 条文原文（短引，供引用与核对）
    interpret: str = ""           # 人话解读 / 常见误解
    keywords: list[str] = field(default_factory=list)
    severity_hint: Severity = Severity.WARNING   # 由用词"应/宜"推出
    status: Status = Status.EFFECTIVE
    superseded_by: list[str] = field(default_factory=list)
    effective_from: str = ""
    machine: dict[str, Any] | None = None         # 机器可读约束（可选）
    source_url: str = ""
    source_note: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = Status(self.status)
        if isinstance(self.severity_hint, str):
            self.severity_hint = Severity(self.severity_hint)


@dataclass
class Rule:
    """一条可执行的审查规则。"""

    rule_id: str
    title: str
    severity: Severity
    check: str                   # rules.py 里注册的函数名
    params: dict[str, Any] = field(default_factory=dict)
    cite: str | None = None      # Clause.clause_id；None 表示无规范依据
    rationale: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity)


@dataclass
class Finding:
    """一条审查结论。必须可定位（handle）且可溯源（cite）。"""

    rule_id: str
    severity: Severity
    message: str
    cite: str | None = None
    handle: str | None = None      # DXF entity handle，用于精确回指
    location: tuple[float, float] | None = None
    expected: Any = None
    actual: Any = None
    sample: list[str] = field(default_factory=list)   # 同类样本，报告里合并展示
    count: int = 1


@dataclass
class ReviewContext:
    """一张图纸被规则消费的全部事实。规则只读这个对象，不碰 DXF。"""

    path: str
    dxfversion: str
    insunits: int
    ltscale: float
    limmin: tuple[float, float]
    limmax: tuple[float, float]
    scale: int                                 # 出图比例分母，1:scale
    layers: list[dict[str, Any]]
    texts: list[dict[str, Any]]
    circles: list[dict[str, Any]]
    mlines: list[dict[str, Any]]
    entity_counts: dict[str, int]
    layer_entity_counts: dict[str, dict[str, int]]
    dim_count: int
    block_names: list[str]
    layout_names: list[str]
    extents: tuple[tuple[float, float], tuple[float, float]] | None
    rooms: list[dict[str, Any]] = field(default_factory=list)
    openings: list[dict[str, Any]] = field(default_factory=list)
    geometry_level: str = "basic"          # basic 不做几何识别 / strict 做
    geometry_note: str | None = None       # 几何识别失败或跳过的原因
    completeness: dict[str, Any] = field(default_factory=dict)  # 图面完整性清单

    # ---- 便捷判定 ----
    @property
    def is_metric(self) -> bool:
        return self.insunits == 4 or self.insunits == 0

    @property
    def paper_mm(self) -> tuple[float, float]:
        """图幅（按 LIMITS 取，退回 A3）。"""
        w = self.limmax[0] - self.limmin[0]
        h = self.limmax[1] - self.limmin[1]
        if w <= 0 or h <= 0:
            return (420.0, 297.0)
        return (w, h)

    def to_print_mm(self, model_units: float) -> float:
        """模型空间长度 -> 打印图纸上的 mm。"""
        return model_units / self.scale
