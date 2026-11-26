# TECH-MARATHON-02 · 第二批进度（2026-11-26 晚）

分支 `dev/windows-tm-geometry` · HEAD `4f2d7af` + Swift 同步层
Python techlab 回归：**695 passed, 3 skipped / 0 failed**（2:08）

## 本批交付

| 任务 | Commit | 内容 | 状态 |
|---|---|---|---|
| TM21 点云处理 | `d3e2339` | 体素质心降采样/统计+半径离群/置信度门（无信号=保留）/中值平滑（位移上报）/边缘保护启发式（L 角不动，诚实声明图像域部分属 Mac）/协方差法向（退化=None）/连通分割/PLY；**每个过滤器报告 kept/removed/reasons** | DESKTOP_VERIFIED，17 tests |
| TM23 开放测量格式 | `41b7b71` | 全上下文记录模型 + JSON/CSV/GeoJSON/PLY/SVG + **manual/device 结构性分离（构造器拒绝混搭）** + 未校准标注随值同行 + 无锚点跳过计数；**DXF R12 最小子集可行性确认（实验性 PoC）**，完整 DXF 建议 ezdxf；格式与生产证据协议隔离 | DESKTOP_VERIFIED，17 tests |
| TM63 标注几何 | `4f2d7af` | 归一化 9 类模型（数量约束+边界强制）、EXIF/裁剪变换（裁剪外=拒绝）、像素容差命中（线段/椭圆/多边形填充+边缘）、命令式 undo/redo + **软删除（与服务端纪律一致）**；Swift 移植 | DESKTOP_VERIFIED（Python），Swift CROSS_PLATFORM_PENDING |
| TM64 水印布局引擎 | `4f2d7af` | W011 语义的**纯布局数学**（字体测量结果进、矩形出）、四角、logo 上移不变式、文本预算收窄、具名拒绝（先换行/高度预算/全开关关）、亮度面板选择；**W011 十组向量文件已核对存在**（TM24 映射：W011 管渲染+geometry 合同，TM64 管无渲染布局） | DESKTOP_VERIFIED（Python），Swift CROSS_PLATFORM_PENDING |
| TM70 证据恢复 Swift | 本批 | TM32 状态机逐函数移植（事实派生永不存状态、violation 永不修补）+ **CaptureTransaction 客户端暂存事务**（Night 04 修复参考：marker 最后写，无 marker 永不成证据） | CROSS_PLATFORM_PENDING |
| TM67 Outbox Swift | 本批 | 重启恢复（sending→pending 依赖服务端台账幂等）、依赖闭包排序、**TM34 冲突类型化不静默重试**（revision_conflict 等入 conflict 态，人工解决后同 id 重入队）、指数退避（确定性供测试）、applied/duplicate 均确认 | CROSS_PLATFORM_PENDING |

## TM62 映射（已完成）

TM62（测量质量与误差 Swift 组件）= 批一 `WorkCamGeometry/Quality.swift`
（TrackingState/CalibrationProfile.uncalibrated/质量报告/距离/面积/体积/坡度
误差传播/repeatability）+ Python `quality.py`。无新增工作。

## 环境事件

- 全量回归首跑出现 1 failed + 2 errors（6:04，I/O 争用），重跑两次均
  **664/695 passed 全绿**——判环境瞬时抖动，如实记录。
- 推送再次遇代理断连：`4f2d7af` 起 3 个提交做 bundle 备份
  （`backups/tm-geometry-4f2d7af.bundle`，verify okay），待网络恢复重推。

## 下批（第二队列继续）

- TM66 统一 API Client（Swift，async/await + Typed Error + 可替换传输）
- TM68 冲突引擎细节（TM34 词汇已在 TM67 落地，TM68 = 冲突详情模型供 UI）
- TM69 断点续传客户端 Swift（对接 TM35 五端点，流式分块不整载内存）
- TM71-73 数据模型/照片索引/Repository
- TM89/91/93/94/95 真实联调、弱网、迁移、门禁、跨平台环境
