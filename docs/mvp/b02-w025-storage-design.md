# B02-W025 工程照片存储与空间优化 — 存储机制设计

日期 2026-11-25 · 分支 `dev/windows-b02-w025-storage` · 基线 `31917e3` (W013-R1)
状态：已实现并全量回归通过（见 docs/progress/b02-w025-storage-2026-11-25.md）

本任务**建立在 W007 不可变对象存储之上**，不推翻、不重写其字节布局；
本文档描述的是在其上新增的存储运营层（storage-ops layer）。

## 0. 硬红线（全部有测试钉死）

| 红线 | 机制 | 钉死测试 |
|---|---|---|
| 原图字节不可变：任何存储操作（缩略图/衍生图/审计/prune/备份恢复）不改变任何原图字节 | 存储层对原图只有只读 open；缓存清理代码**在结构上无法遍历 originals/**（prune 的 walk 根只有 thumbnails/ 与 derivatives/ 两个缓存目录） | `test_prune_all_never_changes_any_original_byte`、`test_scale_1k_...`（1k 张全量 hash 前后比对） |
| 禁止为省空间重压缩/覆盖/自动删除可信原图 | `store_streaming` 对已存在对象只做校验（匹配→幂等重放；损坏→409 拒收，**绝不静默修复**） | `test_existing_object_corrupted_refused_never_overwritten` |
| 任何原图删除策略 = 产品决策 | 删除动作一律 **Not Implemented**；本任务只实现检测/报告/建议 | `test_quota_exceeded_is_report_only_not_enforcement` 等 |
| 孤立文件/对账只报告不处置 | audit 输出问题清单 + verdict；唯一例外 `fix_temps=True` 只清崩溃残留 `.part`（未提交对象，非证据） | `test_audit_temp_residue_fix_only_removes_temps` |

## 1. 内容寻址布局（三层统一）

三个产物类共用同一寻址规则：**SHA-256 即身份**（W007 规则），两级散列目录防单目录膨胀：

```
data/
├── workcam.db                          # SQLite 业务库（WAL）
├── originals/ab/<sha256>.jpg           # W007 不可变原图（本任务零改动）
├── thumbnails/ab/<sha256>_<edge>.jpg   # 缩略图缓存（B02-W025 新增）
└── derivatives/ab/<derivative_sha>.jpg # W011 衍生图（布局既有，纳入管理）
```

- 原图：身份 = 内容哈希；同一内容物理上只有一份字节，多照片通过
  `mvp_photo_originals(photo_id, sha256)` 绑定引用 —— **物理去重是 W007
  的固有性质，本任务把它显式化为引用计数与统计口径，不重复造轮子**。
- 缩略图：身份 = `(原图 sha256, edge)`。同原图的多张照片**共享同一缩略图
  文件**（免费去重）；原图没了它就是死数据（审计报告）。
- 衍生图：身份 = 渲染输出的内容哈希（W011 既有），登记行在
  `mvp_derivatives`，字节可再生（见 §4）。

服务器生成路径，无任何客户端字节参与路径选择（W007 反路径穿越纪律，
`object_path()` 先验 sha 格式再拼路径；新代码同一纪律）。

## 2. 去重存储与引用计数（与 W017 的对齐）

W017（`GET /api/v1/photos/duplicates`，分支未合入）是**照片视角**的
重复识别：按 `original_sha256` 分组列出哪些照片内容相同，只指出、绝不删。

本任务是**对象视角**（`GET /api/v1/storage/dedup`）：哪个物理对象被
多少照片绑定（引用计数）、共享已经省了多少字节：

```
refcount(sha) = COUNT(mvp_photo_originals WHERE sha256 = sha)   # 动态计算
saved_bytes   = SUM((refs - 1) * byte_size)  over refs >= 2
```

两个视角互补、同一身份键（sha256），**合入 W017 后零冲突**；存储层不
重新实现照片分组逻辑。引用计数不落库、每次动态计算——避免与绑定表的
一致性维护（引用数永远和绑定行数一致，不存在漂移 bug 的可能）。

跨主体/跨项目共享：物理字节只有一份是内容寻址存储的基本性质；主体
隔离不受影响（照片/项目级访问控制照常 404，共享本身不构成信息面——
拿到 sha 等于拿到内容，而 sha 本来就是内容的哈希）。

## 3. 大文件：流式摄取（RISK-W021-R1 的修复方案）

既有路径 `PUT /photos/{id}/original` 用 `await request.body()`：**整个
文件先进内存**，2.7MB 测试照片尚可，500MB 现场视频级文件不可行。

新端点 `PUT /photos/{id}/original-stream`（`app/services/storage_ops.py`
的 `_StreamingObjectWriter`）：

```
读 256KB chunk → 写 .part 临时文件 + 哈希同块更新 → … → 收流
→ actual == claimed ? → os.replace 原子提交 + 账本登记
                   否 → 删临时件，400，零持久化
```

- **峰值内存 = 一个 chunk（256KB）**，与文件大小无关
- 语义与 W007 逐字节一致：同一错误码（`hash_mismatch` / `empty_body` /
  `object_corrupted`）、同一原子提交、同一幂等重放、同一永不覆盖
- tracemalloc 实测（同一条 chunk 流喂两条路径，2.35MB 载荷）：
  streaming 峰值 534 KB vs `request.body()` 式整体缓冲 4,697 KB
  —— **8.8×**，且前者不随文件增长（见规模报告）
- 既有非流式端点保留（零回退），客户端可渐进切换

## 4. 缓存策略

**不可变层**（originals/）：永不清理、永不重压缩；备份必含。

**可再生层**（thumbnails/、derivatives/）：

| 产物 | 丢失后果 | 再生方式 |
|---|---|---|
| 缩略图 | 缩略请求变慢一次 | `ensure_thumbnail` 即时重生成（无 DB 行，纯磁盘命名自描述） |
| W011 衍生图字节 | 下载临时 404 | **W011 幂等探测要求文件存在才回放**（`derivatives.py` 的 `exists()` 检查）：字节被清后，同参数请求自动重新渲染，恢复相同 sha 的字节；`mvp_derivatives` 登记行保留（溯源不丢） |

因此 `POST /storage/cache/prune` 删衍生图字节是**安全**的——这是把
W011 的既有幂等设计当再生机制用，不是新造的机制。

清理边界：`scope ∈ {thumbnails, derivatives, all}`；`scope=originals`
直接 400（词表拒绝）；实现上 prune 只 walk 两个缓存根目录，
**不是靠策略开关而是靠代码结构**保证碰不到原图。

## 5. 空间统计（双口径）

```
GET /api/v1/storage/stats                     # 全局：物理口径
GET /api/v1/storage/stats?project_id=<uuid>   # 单项目：逻辑口径
```

- **全局物理口径**：originals/thumbnails/derivatives/DB 各自真实磁盘字节
  （walk 实测，排除 .part）、去重节省（dedup_saved_bytes）、账本/引用/
  无引用对象计数。
- **项目逻辑口径**：项目照片引用的每个对象都计入该项目——共享对象在
  每个引用它的项目都算全量（这是配额的口径）。物理字节只在全局有意义，
  两个口径在响应中明确标注，绝不混用。

## 6. 对账算法（FS ⇄ DB 双向 + 孤立检测）

`GET /api/v1/storage/audit` → `audit_storage()`：

1. **原图方向**：复用 W007 `fsck()`（不重复实现）——DB→FS 逐对象
   hash 校验（corrupt/missing）、FS→DB 无账本文件（orphan_files）、
   无引用账本行（orphan_suspected_db）、.part 残留、绑定但缺文件
   （bound_missing）。
2. **衍生图方向**（新增）：
   - DB→FS：登记行字节缺失 → `derivatives_missing_bytes`
     （**可再生 gap**，不计入 verdict 的"数据丢失"判定，单列
     `regenerable_gaps` 计数）
   - FS→DB：磁盘字节无登记行 → `derivative_orphan_files`（计入 verdict）
3. **缩略图方向**（新增）：FS→原图对象——原图磁盘消失而缩略图残留 →
   `thumbnail_orphan_files`（死缓存，计入 verdict）；原图在但无照片引用
   → `thumbnail_dangling_originals`（报告级）。

verdict：`consistent` / `inconsistencies_found`；审计**永不删除/修复**
已提交对象（唯一动作是 `fix_temps=True` 清崩溃残留）。路径统一
`as_posix()`（跨平台响应稳定）。

## 7. 配额模型（Not Implemented 边界）

迁移 v11（v9/v10 为 W014/W017 预留，跳号合法）：

```
mvp_storage_quota(project_id PK, quota_bytes >= 0,
                  policy IN ('report_only','enforce_reject'),
                  updated_at, updated_by)
```

- `PUT/GET /api/v1/storage/quota`（owner 隔离 404 同既有纪律）
- `check_quota()`：used_bytes = 项目逻辑口径原图 + 缩略图 + 衍生图字节；
  exceeded=true 时 action 按策略给出**建议**（report / reject_future_uploads
  (would be)）
- **强制执行 Not Implemented**：没有任何生产路由调用配额检查去拒绝请求
  （测试钉死：超限状态下继续上传照片依然成功）；`enforce_reject` 只是
  词表占位，接线与否、怎么接线是产品决策
- quota=0 的语义 = "不允许任何存储"（模型层面如此解释，未接线无实际
  效果）

## 8. 备份恢复（去重一致性专项）

沿用既有基线（W016 验收文档第 7 节五项 + `test_rc3_backup_restore_full_stack`），
本任务新增两个专项：

- `test_backup_restore_dedup_shared_object`：一个物理对象被 3 张照片
  引用 → 备份 manifest 只含 1 份 → 灾难删除后恢复 1 份 → 3 张照片全部
  下载到逐字节一致的内容；重复恢复 skipped_intact（不损数据）。
- `test_backup_restore_after_cache_prune_full_consistency`：缓存清理后
  备份 → 原图+缓存全丢 → 恢复 DB+原图 → 审计 verdict=consistent
  （衍生图缺字节是可再生 gap）；缩略图按需重生成。

**缓存不进备份**（可再生的东西不占备份窗口）——恢复演练证明该取舍
安全。

## 9. 删除策略：**产品决策待定（Not Implemented）**

本任务明确交付的只有**检测/报告/建议**：

| 想删什么 | 现状 |
|---|---|
| 无引用原图对象（orphan_suspected_db） | audit 报告；不删 |
| 重复内容的多余照片 | W017 duplicates + 本任务 dedup 报告；不删 |
| 孤立磁盘文件（orphan_files） | audit 报告；不删 |
| 超配额项目的数据 | check_quota 报告 exceeded；不强制、不删 |

任何删除动作需要产品侧确认策略（例如：孤立对象保留期、审计日志要求、
产品交互文案），届时另立任务实现。

## 10. iOS / Mac 端影响分析

**应无影响**，原因：

- 客户端 API 契约零变更：既有端点（上传/下载/水印/报告/同步）路径、
  请求响应体全部不动；新能力全部是**新增端点**（/storage/*、
  /thumbnail、/original-stream），旧客户端不感知。
- 磁盘布局变化仅为**新增目录**（thumbnails/）；originals/ 与
  derivatives/ 布局未动，本地开发者直接读这两个目录的脚本不受影响。
- `/photos/{id}/original-stream` 是给大文件场景的**可选**新路径；既有
  `/original` 继续可用。Mac 端后续接入时建议（非必须）：上传大文件走
  streaming 端点（`Content-Type: application/octet-stream`，分块发送
  即可），缩略图直接请求 `/photos/{id}/thumbnail` 而非自行解码原图。

## 11. 规模测试结论（真实数字，DESKTOP_SIMULATION）

命令与完整输出见进度文档。摘要（320×240 JPEG ~58.7KB/张，30% 重复）：

| n | 摄取(纯存储) | 吞吐 | 物理对象 | 审计(全量hash) | 统计 | 备份 | 磁盘 |
|---|---|---|---|---|---|---|---|
| 1,000 | 20.1s | 49.7/s | 730 | 4.3s | 0.28s | 1.5s | 44.8MB |
| 5,000 | 101.1s | 49.4/s | 3,530 | 16.8s | 0.45s | 5.4s | 212.3MB |
| 10,000 | 192.2s | 52.0/s | 7,030 | 59.9s | 1.05s | 15.3s | 421.5MB |

- 摄取吞吐稳定 ~50/s 不随规模衰减：瓶颈是每对象一事务的落盘纪律
  （W008 durability 取舍，与 W016-K4 观测一致），**线性可扩展**
- 审计线性（~8.5ms/对象全量 hash 重验），10k 时约 1 分钟——运维可接受
- 缩略图风暴 10% 采样：0.39s/73张 → 4.98s/703张，线性 ~7ms/张
- 内存探针：streaming 峰值 534KB（≈2×chunk，不随文件增长）vs
  整体缓冲 4.7MB（=文件大小），2.35MB 载荷下 **8.8×**
