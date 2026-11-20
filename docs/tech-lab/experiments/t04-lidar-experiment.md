# T04 — LiDAR 工程测量精度真机实验方案

状态：RESEARCHED（方案+分析工具就绪）→ 待 Mac 真机执行
Windows 侧交付：本方案 + `t04-checklist.csv`（唯一测试清单，132 次）+ `tools/tech-lab/benchmarks/lidar_error_analysis.py` + `tools/tech-lab/validation/t04_batch_review.py`

> **W-01 修正记录**：Wave 1 版文档声称 96 次测试，但矩阵表加总只有 68
> 次（20+16+8+8+8+8），数量不一致。现已由
> `tools/tech-lab/benchmarks/t04_checklist.py` 生成唯一权威清单
> `t04-checklist.csv`（**132 次**），本表与清单以清单为准。

## 1. 实验目标

回答一个未预设结论的问题：**iPhone LiDAR（ARKit SceneDepth/Raycasting）在 0.5–5 m 工程距离上到底能达到什么精度、什么成功率？**

不做任何"工程测量精度达标"的预设。失败场景与正常场景同权重记录。

## 2. 三种测量模式的决策（W-01）

Mac LiDAR Lab（PR #9）实现了三种模式：`raycast`、`scene_depth`、
`smoothed_depth`。是否都要测？**决策：测，但分工不同：**

| 模式 | 范围 | 理由 |
|---|---|---|
| raycast | 完整矩阵（68 次） | 现场主路径（单点、低耗电、UI 依赖的默认路径），必须获得全条件×全距离的精度与失败率 |
| scene_depth | 子集（32 次） | 与 raycast 出自同一传感器，只验证"API 路径一致性"：normal/low_light/glass/textureless @ 1/3 m |
| smoothed_depth | 子集（32 次） | 同上（smoothed 是 scene_depth 的后处理变体） |

`reflective` 不进入深度模式子集：镜面对 ToF 是物理性失效，三模式必然同败，
raycast 已覆盖其失败原因分布——重复必然失败的样本没有信息量（反凑数原则）。

## 3. 实验设备与环境

- iPhone Pro（LiDAR 硬件必需；模拟器无 SceneDepth）
- 标定 setup：激光测距仪或卷尺作为 ground truth（±2 mm 级），三脚架固定
- 硬质平整标靶（A3 白板 + 纹理贴图两版）
- 场地：室内 ≥ 6 m 直线空间；可加桌面/楼道场景

## 4. 测试矩阵（与 t04-checklist.csv 一致；重复数=4/格）

raycast（primary）：

| 条件 | 距离点 (m) | 次数 |
|---|---|---|
| normal | 0.5 / 1 / 2 / 3 / 5 | 20 |
| low_light | 1 / 2 / 3 / 5 | 16 |
| glass | 1 / 3 | 8 |
| oblique | 1 / 3 | 8 |
| textureless | 1 / 3 | 8 |
| reflective | 1 / 3 | 8 |
| **小计** | | **68** |

scene_depth / smoothed_depth（secondary）：normal、low_light、glass、
textureless @ 1 / 3 m，各 32 次。**总计 132 次。**

每条测试有唯一 `test_id`（`T04-<mode>-<condition>-<dist>m-R<n>`），执行时
填入 Mac App 的 Test ID 字段，导出 CSV 的 `test_id` 列与之对应，Windows
侧验收工具据此查重与统计完成度。

## 5. Swift 实验入口

已在 Mac PR #9（`Features/TechLab/LidarLabView.swift`）实现三种模式与
CSV 导出；本方案的清单 ID 直接填入其 Test ID 字段即可。

导出格式 = `lidar_error_analysis.py` / `t04_batch_review.py` 的输入 CSV
（20 列头见工具 docstring，与 LidarLabView.swift 的 `csv` 属性一致）。

## 6. 执行步骤（Mac 真机）

1. 按 `t04-checklist.csv` 逐条执行：填 test_id → 激光测距仪记真值 →
   LiDAR Lab 测量（无读数时记录失败原因，**失败绝不省略**）。
2. 每完成一批（batch）导出 CSV。
3. Windows 侧验收：`python tools/tech-lab/validation/t04_batch_review.py <csv>`
   自动完成格式/重复/异常值/完成度/误差统计检查，输出 JSON+Markdown 报告。
4. 全部 132 条完成后，状态由 DEVICE_PENDING 转 DEVICE_VERIFIED（或按
   实测数据 LIMITED/REJECTED）。

## 7. 验收判定（数据说话）

| 指标 | 进入产品开发的门槛 |
|---|---|
| raycast@normal：mean / median 绝对误差、RMSE、P95 | 以真机数据定标（预期 cm 级，但**以实测为准**） |
| raycast@normal 失败率 | < 5% |
| 恶劣条件（low_light/glass/reflective 等） | 允许 LIMITED 标注 + 明确限制清单 |
| 三模式一致性 | scene/smoothed 与 raycast 中位偏差需解释或修正 |
| 可重复性（同格 4 次的 stddev） | 与 mean error 同数量级或更小 |

## 8. 已知限制

- LiDAR 有效范围约 0.25–5 m，超程必失败（>5 m 不在本实验范围）。
- ARKit 深度为**估计值**，不承诺测绘级。
- 玻璃/镜面/吸光材质失败是物理限制，非实现缺陷——产品需 UI 预警而非掩盖。
- 本方案不预设达标结论；若实测不达工程级，状态记 LIMITED/REJECTED 并给
  替代方案（外接蓝牙测距仪做 ground truth + 照片记录）。
- Windows 侧所有统计/验收工具的合成自测数据均标记 SYNTHETIC，不构成
  真机精度报告。
