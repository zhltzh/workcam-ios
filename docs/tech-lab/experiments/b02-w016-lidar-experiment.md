# B02-W016 — LiDAR 测量分析工具升级：实验协议与 Mac CSV 接入规范

状态：**DESKTOP_VERIFIED**（Windows 侧分析器与验收工具已完成并用合成数据验证）；
真机数据状态：**DEVICE_PENDING**（Mac 端尚未采集）。

Windows 侧交付：本协议 + `b02-w016-checklist.csv`（唯一实验清单，132 次）+
`tools/tech-lab/benchmarks/b02w016_lidar_analysis.py`（分析 CLI，输出
JSON/CSV/图表/Markdown）+ `backend/techlab/lidar_stats_v2.py`（统计核心）。

> 本任务编号一律写 **B02-W016**。仓库历史分支 `dev/windows-stress-w016`
> （场景压测）是旧编号体系的另一任务，与本任务无关，勿混淆。

## 1. 本任务修复的三类历史问题

历史教训：Wave 1 文档声称 96 次测试、矩阵加总只有 68 次（git `355edf9`）。
本任务在**协议与代码两侧**建立防线，Mac 端拿到本规范即可开始采集：

| # | 历史问题 | B02-W016 修复 |
|---|---|---|
| 1 | 声明次数 ≠ 实际记录次数 | 清单生成器自检（声明矩阵求和必须等于生成行数）；分析器 `matrix_check` 从清单行**推导**声明矩阵、从 CSV 行**推导**实际矩阵，逐 mode / 逐 cell 对比；不一致时报告 MISMATCH 并退出码 1 |
| 2 | 不同测量模式被混合统计 | v2 分析**不存在任何跨模式误差统计**：顶层只有计数；raycast / scene_depth / smoothed_depth 的每项指标都在各自分组内，混合输入不改变分模式结果（测试保证分组不变性） |
| 3 | 绝对误差标准差被误用作重复性 | `repeatability_stddev_m` 只在 **cell 级**（同 mode + 同 condition + 同基准距离）按**有符号误差**的样本标准差计算；所有聚合层级（by_mode / by_distance / by_material / by_lighting / by_pose / by_condition）一律为 null；绝对误差的标准差在任何层级都不作为重复性输出 |

## 2. Mac CSV 接入规范 v2

### 2.1 列定义（23 列，v1 20 列的超集）

前 20 列与 LidarLabView.swift 既有导出（PR #9）**完全一致**：

```
batch_id,test_id,device_model,ios_version,depth_received,
confidence_end,confidence_start,tracking,mode,condition,device_time,
distance_true_m,distance_measured_m,absolute_error_m,relative_error,
elapsed_s,failure,distance_m,first_device_time,first_tracking
```

v2 追加 3 个**因子列**（命名 snake_case，列序无关，解析按列名取）：

| 列 | 取值 | 说明 |
|---|---|---|
| `material` | `matte_white` / `textureless` / `glass` / `mirror` | 标靶材质，与光照无关 |
| `lighting` | `normal` / `low_light` | 现场光照，与材质无关 |
| `pose` | `perpendicular` / `oblique_30` | 手机对标靶的姿态（正对 / 30° 斜对） |

**兼容性**：不追加这 3 列也能被分析（v1 CSV 自动按 condition 映射：
`normal→(matte_white,normal,perpendicular)`、`low_light→(matte_white,low_light,perpendicular)`、
`glass→(glass,normal,perpendicular)`、`oblique→(matte_white,normal,oblique_30)`、
`textureless→(textureless,normal,perpendicular)`、`reflective→(mirror,normal,perpendicular)`）。
但**强烈建议 Mac 端升级导出 3 列**——材质与光照独立分组是本任务的核心统计维度。

### 2.2 mode 列

固定三个小写值：`raycast` / `scene_depth` / `smoothed_depth`。
大小写与常见变体（`Raycast`、`SCENEDEPTH`、`Smoothed Depth` 等）会被
自动规范化并记入 warnings，但**不要依赖**规范化——导出时请用规范值。

### 2.3 行规则（诚实性，最高优先）

1. **每次尝试一行**。清单 132 次 = CSV 132 行；不许只记成功。
2. **失败行绝不省略**：`distance_measured_m` 留空，`failure` 填原因
   （建议词表：`depth_missing` / `no_return` / `low_confidence` /
   `tracking_lost` / `out_of_range`；自定义词也可，会原样保留）。
3. **无读数且无原因**的行按 `measurement_missing` 计为未解释失败，
   出现在报告的失败清单里——不许为了"成功率"抹掉它。
4. `distance_true_m` 必填（激光测距仪 / 卷尺真值）；缺失的行会被
   归入 `invalid_rows` 并计入完成度缺口，**不会被静默删除**。
5. 异常值（|误差|>1 m、测量值 ≤0.05 m 或 ≥10 m）会被**报告但保留**
   在统计中；不要手工剔除。
6. `test_id` 与清单 1:1，格式 `B02-W016-<mode>-<condition>-<dist>m-R<n>`；
   重复 id 会被标记 DUPLICATE 并触发矩阵 MISMATCH。
7. `batch_id` 建议 `B02W016-<日期>-<批次号>`（如 `B02W016-20261201-B1`）。
   **合成数据批次名必须含 `DESKTOP_SIMULATION` 或 `SYNTHETIC` 字样**，
   分析器据此把报告标注为合成、禁止冒充真机结果。

### 2.3.1 Windows 侧验收命令（Mac 交完一批即可跑）

```powershell
backend\.venv\Scripts\python.exe tools\tech-lab\benchmarks\b02w016_lidar_analysis.py <mac-export.csv> `
    --checklist docs\tech-lab\experiments\b02-w016-checklist.csv --out-dir <输出目录>
```

输出四类产物：`<stem>-stats.json`、`<stem>-groups.csv`、
`<stem>-report.md`、`<stem>-fig1..4.png`。退出码：0 = 矩阵一致；
1 = 格式错误或矩阵 MISMATCH（部分完成时正常，按报告补测即可）。

兼容旧数据：`t04_batch_review.py` 与 `lidar_error_analysis.py` 原样可用
（本任务未改动它们）。

## 3. 实验清单（132 次，物理矩阵与 T04 同构）

清单唯一来源：`tools/tech-lab/benchmarks/b02w016_checklist.py` 生成的
`docs/tech-lab/experiments/b02-w016-checklist.csv`（生成时自检声明矩阵 =
68+32+32=132 行，二者不一致则拒绝生成）。

| 模式 | 条件 | 距离点 (m) | 次数 |
|---|---|---|---|
| raycast（主路径） | normal | 0.5 / 1 / 2 / 3 / 5 | 20 |
| | low_light | 1 / 2 / 3 / 5 | 16 |
| | glass | 1 / 3 | 8 |
| | oblique | 1 / 3 | 8 |
| | textureless | 1 / 3 | 8 |
| | reflective | 1 / 3 | 8 |
| | **小计** | | **68** |
| scene_depth | normal / low_light / glass / textureless | 1 / 3 | 32 |
| smoothed_depth | 同上 | 1 / 3 | 32 |
| | | **总计** | **132** |

每格重复 4 次；reflective 不进 scene/smoothed 子集（镜面对 ToF 物理性
失效，三模式必然同败，raycast 已覆盖其失败分布——反凑数原则）。

执行步骤（Mac 真机）：
1. 按 `b02-w016-checklist.csv` 逐条执行：填 test_id → 激光测距仪记真值 →
   LiDAR Lab 测量（三种模式在 App 内切换）；无读数时记失败原因。
2. 每完成一批导出 CSV（23 列 v2 格式），发 Windows 侧验收。
3. 全部 132 条且 `matrix_check.match = true` 后，状态由 DEVICE_PENDING 转
   DEVICE_VERIFIED（或按实测 LIMITED/REJECTED）。

## 4. 统计指标（每模式/每分组输出）

每个分组输出：**有效样本数量（valid_samples）、总尝试次数（attempts）、
测量成功率、有符号平均误差（mean_signed_error_m）、MAE（mean_abs_error_m）、
RMSE、中位绝对误差、P95 绝对误差、最大绝对误差、重复测量标准差
（repeatability_stddev_m，仅 cell 级）**。

分组维度：模式、模式×条件、模式×材质、模式×光照、模式×基准距离、
模式×姿态、cell（模式×条件×距离）。

诚实性规则：
- 有效样本 < 3 的分组：误差统计全部为 null（计数保留）——不给假数字。
- 失败行、未解释失败、异常值、无效行全部保留并可计数，永不静默删除。
- 严格 JSON（allow_nan=False）：NaN/Infinity 不可能出现在任何输出。

## 5. 验收判定（数据说话，不预设结论）

| 检查 | 门槛 |
|---|---|
| 矩阵核验 | `matrix_check.match = true`（声明 132 = 实际 132，无重复 id） |
| raycast@normal 精度 | mean / median 绝对误差、RMSE、P95 以真机数据定标 |
| raycast@normal 失败率 | < 5% |
| 恶劣条件 | 允许 LIMITED 标注 + 明确限制清单 |
| 三模式一致性 | scene/smoothed 与 raycast 的中位偏差需解释或修正 |
| 重复性 | cell 级重复性应与 cell MAE 同数量级或更小 |

## 6. 已知限制

- LiDAR 有效范围约 0.25–5 m；超程必失败（>5 m 不在本实验范围）。
- ARKit 深度为估计值，不承诺测绘级。
- 玻璃/镜面/吸光材质失败是物理限制——产品需 UI 预警而非掩盖。
- 本协议不预设达标结论；实测不达工程级则记 LIMITED/REJECTED 并给替代方案
  （外接蓝牙测距仪做 ground truth + 照片记录）。
- Windows 侧所有合成自测数据（`gen_b02w016_samples.py` 产物）标注
  **DESKTOP_SIMULATION**，仅验证分析器，不构成真机精度报告。
