# examples

这里放两份真实输出，用来对照格式。**没有放 DXF** —— 示例图是别人的图纸，
体积也大（7.5MB）。想复现就用自己的图，命令在下面。

| 文件 | 是什么 |
|---|---|
| `1.report.strict.json` | 一张半成品住宅平面图（10 间房）的完整 JSON 报告，`--level strict` |
| `6-2.report.json` | 一张画满但零文字的图，看 `geometry_note` 与空 `rooms` 长什么样 |

```bash
cadspec check 你的图.dxf --scale 100 --level strict --format json -o out.json
cadspec doctor 你的图.dxf
```

JSON 的字段说明见 `skills/cadspec/SKILL.md`。两份报告里的绝对路径是清理前的原值，
你自己跑会不一样。
