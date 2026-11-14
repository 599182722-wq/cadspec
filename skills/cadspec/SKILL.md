---
name: cadspec
description: 检查 DXF 平面图是否符合中国建筑规范（房间面积、门宽、图层命名、字高、轴号、出图比例），并给出条文原文、严重级别和实体定位。需要判断"这张图画得合不合规""这个卧室够不够大""查一下国标怎么要求的"时使用。
---

# cadspec

一张 DXF 进来，一批带条文依据的问题出去。

## 什么时候用

- 用户问「这张图合规吗」「帮我检查一下」「这个卧室够大吗」「门是不是太窄了」
- agent 画完图要自检，改完要复验
- 需要查某条中国国标的原文，而不是网上抓来的二手转述

## 什么时候不用

- **要改图**。cadspec 只判不改，一条修改命令都没有。改图用 `autocad-mcp` 之类的工具。
- **立面、剖面、结构计算、设备系统**。目前只覆盖平面图。
- **没有 DXF 只有截图**。它读的是矢量实体，不是看图。

## 跑一次

```bash
cadspec doctor <图纸.dxf> --format json -o doctor.json    # 先看这张图能不能审
cadspec check  <图纸.dxf> --scale 100 --level strict --format json -o report.json
```

**先跑 doctor，再跑 check。** doctor 回答的是「这张图现在能被自动审到什么程度」，
输出里的 `capabilities` 直接告诉你有哪几类检查跑不了：

```jsonc
{"capabilities": {"expression": true, "geometry": false, "room": false, "dimension": false}}
```

- `geometry: false` → 墙没围合出来（图层名不对、或墙不是多线）。这时不要跑 `--level strict` 判房间，只会得到空结果。
- `room: false` → 图上没有模型空间文字，房间用途无从判断。**不要自己给房间贴名字再判定**，那等于把假设当事实。
- `dimension: false` → 单位没声明。所有长度量都不可信。

doctor 的 `items` 里每条都有 `fix`，直接转述给用户就行：人改图比 agent 猜图快得多。

```bash
cadspec check <图纸.dxf> --scale 100 --level strict --format json -o report.json
```

- `--scale` 出图比例，默认 100。图面若有比例标注，会校验两者是否一致。
- `--level strict` 才会做几何识别（房间多边形、门窗洞口）。**不写就是 basic**，只查图层、字高、轴号这些表达问题。要求"看尺度"时一定加。
- `-o report.json` 建议始终带。Windows 下从管道取输出会带 BOM，`json.load` 直接报错。

退出码：`0` 跑完；`1` 有 error 且加了 `--strict`；`2` DXF 读不了。

## JSON 里有什么

**先看 `completeness`**，它告诉你这张图能不能被审：

```jsonc
"completeness": {
  "ok": 3, "applicable": 7,
  "items": [
    { "key": "room_label", "label": "房间名称", "ok": false,
      "detail": "10 个房间里 3 个有名字",
      "unlocks": "房间尺度类规则（卧室/厨房/卫生间面积、短边、门宽分档）" },
    { "key": "stairs", "label": "楼梯", "ok": false, "detail": "图层 楼梯 共 0 个实体" },
    { "key": "dimensions", "label": "尺寸标注", "ok": true, "detail": "DIMENSION 实体 6 个" }
  ]
}
```

`ok` 有三种值：`true` 齐备、`false` 缺、`null` 不适用（图上根本没有那个图层，
或者跑的是 basic 档）。`null` 不是通过。

**缺的东西多到一定程度，结论应该反过来写**：与其说「未发现问题」，
不如说「这张图现在只能审 X，另外 Y 要补」。判断依据就是 `unlocks` 字段——
它写明补齐之后能启用哪批规则。

其余字段：

```jsonc
{
  "scale": 100,
  "geometry_level": "strict",
  "geometry_note": null,            // 非 null 说明几何识别失败了，先读这句话
  "rooms": [
    {
      "id": "R3",
      "area_m2": 16.38,             // 轴线面积
      "net_area_m2": 14.80,         // 扣墙后的净面积，判面积用这个
      "short_side": 3900,           // 轴线短边，mm
      "net_short_side": 3600,       // 净短边
      "center": [9084, 5290],
      "label": "主卧",              // 图上写的原文，可能为 null
      "purpose": "卧室",            // 归一后的类别，可能为 null
      "neighbors": [
        { "room": "R2", "opening": "O5", "width": 900, "kind": "door" },
        { "room": "R4", "opening": null, "width": null, "kind": "wall" }
      ],
      "outline": [[...]]            // 多边形顶点，一般不用看
    }
  ],
  "openings": [
    {
      "id": "O5",
      "width": 900,
      "kind": "door",               // door / window / unknown / gap
      "evidence": "门扇开启弧：铰点 (7314,7990)，弧半径 900mm 与洞口宽 900mm 一致",
      "filled": true,               // false 表示墙线缺口，图上没有门窗实体
      "start": [x, y], "end": [x, y]
    }
  ],
  "findings": [
    {
      "rule_id": "R-ROOM-001",
      "severity": "error",          // error / warning / info
      "cite": "GB55038-2025/4.1.1", // 条文编号，用它去取原文
      "message": "「主卧」净使用面积 4.80m²，小于 5m²（房间中心 9084,5290）",
      "handle": null,               // 实体类问题才有句柄；房间类用 location 定位
      "location": [9084, 5290],
      "expected": "≥ 5m²", "actual": "4.80m²"
    }
  ]
}
```

## 三条硬规矩

**一、数已经算好了，不要自己再算。**

`area_m2`、`net_short_side`、`width` 都是 Python 量出来的确定值。你不需要、也不应该从 `outline` 的坐标里重新推面积或长短边——那会算错，而且不可复现。

**二、你负责的是「这是什么」，不是「够不够」。**

几何相邻关系 `neighbors` 也是算好的：R3 与 R2 之间是 900mm 的门还是实墙，数组里写着。你要判断的是**语义**：

- R9 是 5.67m²、只通 R1、门宽 700 —— 这是储藏间还是卫生间？门窄说明不是主要出入房间。
- R7 标着「厨房」、只通 R1、门宽 800 —— 厨房门，规范对厨房门没有宽度专项要求，但厨房面积有下限 3.5m²。
- R1 通 5 个房间、46m² —— 客厅，不是卧室，别套卧室的 5m²。

`purpose` 为 null 的房间，靠面积、门宽、相邻关系去猜，**猜不出来就说猜不出来**，不要编。

**三、说结论必须带条号。**

每条问题都要能落到 `cite` 上。拿不准是哪条，先去查：

```bash
cadspec kb show GB55038-2025/4.1.1    # 条文原文 + 解读 + 挂靠的附表
cadspec kb list --grep 卧室            # 按关键词找条文
cadspec kb table 表7.4.7               # 单看一张附表
```

`findings` 里已经带 `cite` 的，直接用。要补充判断依据时再查原文。

## 反过来查：缺了什么

上面的规矩是查「画出来的东西对不对」。但一张图最危险的状态不是画错，是**该有的没画**——
那种图跑完所有规则是全绿的，看起来比谁都干净。

几何算法看不见「不存在」：`polygonize` 只围得出有墙的地方，画漏的墙、没开的门它一律当没有。
所以这类检查只能反着做：**先假定该有，再看有没有**。

四件事，每张开图都要过一遍：

1. **外轮廓上有没有门洞。** 全部 11 处门都在房间之间、外墙只有窗，那这套房没有出口。
   `R-OPEN-003` 会自动报这一条，但你要判断它是「真没画」还是「画在块里/别的图层」。
2. **空图层是不是该有内容。** `R-LAYER-003` 报的空图层里，`楼梯`、`剖切符号`、`标高`
   往往不是模板残留，是这栋楼该画而没画的东西。看到一个空的「楼梯」层，先问：
   这栋楼几层？楼梯在哪？
3. **房间之间有没有超过 1500mm 的无门窗开口。** 两个空间之间没有门也没有实墙，
   多半是漏画了一段墙。这种情况工具会把两个房间**合并成一个**，`rooms` 里少一间，
   面积偏大——如果你看到某个「客厅」异常大（比如 46m² 还带一根孤零零的短墙），
   去 `outline` 里看那根墙的端点是不是悬空的。
4. **该有的房间在不在。** 住宅每套要有卧室、起居室、厨房、卫生间四件套（GB55038 2.2.5）。
   图上标了几间？`purpose` 为 null 的房间可能是客厅，也可能是漏标的卧室。

这条不能靠 `findings`：第 2、3、4 项工具只给原始事实，结论要你下。

## 一轮完整的工作流

0. **体检**：`cadspec doctor <dxf> --format json -o doctor.json`。`capabilities` 里有 `false` 的项，说明这一类检查整条跑不了，先别往下走——把 doctor 的 `fix` 转述给人，比强跑一遍再解释「为什么没报」快得多。
1. **跑检查**：`cadspec check ... --level strict --format json -o report.json`
2. **先看 `geometry_note`**。非 null 说明房间/门窗没识别出来，后面所有尺度结论都不成立。
3. **读 `findings`**。这些是机器已经判定的，直接采信，不要重新推翻。
4. **看 `rooms` 里没被判的房间**。`purpose` 为 null 的、面积接近限值的，用条文和常识补判。
5. **给结论**。每条写：哪个房间/门窗（`id`）、实测多少、要求多少、依据哪条（`cite`）、怎么改。

如果 doctor 报的是 `geometry: false`，第 1–4 步全部作废，只做第 0 步和「表达类」结论。

## 它判不了什么

约 85% 的条文它判不了，只供检索：

- 定性描述：「楼梯间应能天然采光和自然通风」——DXF 里没有"采光"这个实体
- 需要外部输入：「建筑高度大于 27m」——图里没有高度，`machine.requires` 会写出来
- 构造与系统：防火分区的划分、消防给水、电气
- 图上没画的东西：漏画的墙、该有而没有的门

遇到这类，**去查条文再自己判断**，不要假装 `findings` 里没报就是合格。

## 覆盖范围

`cadspec kb coverage` 会打印当前有多少条文能自动判。

现在收录 6 份 626 条：GB 55037 建筑防火、GB 55031 民用建筑通用、GB 55038 住宅项目、GB 55019 无障碍、GB/T 50001 制图标准、SJD-2016 深度规定。**不是全部国标**，覆盖不到的要明说。
