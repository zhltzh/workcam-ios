# WorkCam W013 照片标注与现场检查记录规范

版本 v1 · 分支 `dev/windows-inspection-w013` · 全量 **620 passed / 0 failed**
8 张真实渲染样图：`docs/mvp/w013-samples/`

## P0 安全补充验收（两个发现，均已修复）

### 发现 A：旧式 SHA-256(public_key) token 经 legacy 文件路径仍可认证

**修复**：`resolve_device()` 对 legacy 命中新增两层防线——
1. 按**公钥**查 SQLite 吊销记录（旧设备被吊销后即使走 legacy 路径也拒绝）
2. **推导式 token 一律 401 `legacy_token_must_re_enroll`**——公钥嵌在每个证据清单里，推导 token 不是秘密。旧设备重注册（同公钥幂等）即获得新随机 token，**数据零丢失**

### 发现 B：上传端点的 auth/challenge/verify 三个路径仍走旧文件注册表

**修复**：统一走 SQLite 身份库（`device_identity`），挑战发放/验签共用同一 DB 存储（旧分裂使验签 401 有效挑战）

### 附带修复（"全量才挂"类 flake 根因）

`main.py` 模块导入时跑 migrate() 捕获了错误时机的 `WORKCAM_BUSINESS_DB`（pytest hash 随机化收集顺序下后到 fixture 撞上已删 tmp DB）。`PYTHONHASHSEED=0 → 592 全绿` 确诊。**修复：迁移改 HTTP 中间件惰性执行**（uvicorn 行为不变、import 顺序险消除、全套件现在无 PYTHONHASHSEED 也稳定）。

## P1 标注数据模型

6 种类型：`arrow / rectangle / circle / freehand / text / number_marker`。

| 字段 | 约束 |
|---|---|
| norm_x1/y1 | 归一化 0..1（EXIF 转置后的显示坐标系） |
| norm_x2/y2 | 箭头/矩形/圆需要 |
| text_content | text 类型必填，≤200 字符 |
| marker_number | number_marker 必填，1..999 |
| color_hex | `#RRGGBB` 格式校验 |
| stroke_width | 1..40 |
| z_order | 0..999 图层顺序 |
| revision | 乐观并发（patch 需带 expected_revision） |
| is_active | 软删除（历史保留） |

标注数量上限：50/照片（413）。**所有响应标注 "human-added annotation content"**。

## P2 标注渲染引擎

`render_annotated_derivative()`（`app/services/annotation_render.py`）：
- 在 W011 式衍生图上渲染（**原图只读**，测试钉死字节不变）
- **支持标注 + 水印组合**在一张衍生图（`watermark_applied` 几何标记）
- EXIF 0x0112 转置**先于**归一化坐标解算（标注落在视觉正立图上）
- 文字换行（45% 图宽）、箭头方向自动计算（三角头）、编号圆底白字
- stroke 按图长边缩放（1600px 基准）——不同分辨率视觉一致
- 80MP 渲染预算守卫与水印引擎共用（测试证明早于分配即拒绝）

## P3 工程问题（Inspection Issue）

```
POST /api/v1/projects/{pid}/issues       创建（open）
GET  /api/v1/projects/{pid}/issues       列表（status/severity 过滤）
GET  /api/v1/issues/{iid}                详情（含全量 history + photos）
PATCH /api/v1/issues/{iid}               更新（revision+history；乐观并发）
```

| 字段 | 取值 |
|---|---|
| severity | low / medium / high / critical |
| status | open → in_progress → resolved → closed |

**状态机**（非法跳转 409）：open→{in_progress}；in_progress→{resolved,open}；resolved→{closed,in_progress}；closed=终态不可改。

**每笔变更写入 `mvp_issue_history`**（created/severity_changed/status_changed/photo_linked/photo_unlinked，含 from→to 值与时间戳）。resolved 自动记 `resolved_at`，重开自动清空。

**等级全部是人工记录**——响应固定注记 "severity/status are HUMAN records; no AI assessment implied"。

## P4 整改前后对比

```
POST   /api/v1/issues/{iid}/photos          关联（role: before/progress/after）
DELETE /api/v1/issues/{iid}/photos/{pid}/{role}
GET    /api/v1/issues/{iid}/photos          对比视图
POST   /api/v1/issues/{iid}/comparison-image  并排衍生图
```

- 每角色一张照片（重关联覆盖）
- **跨项目照片拒绝**（photo_project_mismatch 400）
- closed 问题不可增删照片
- 并排衍生图带 BEFORE/AFTER 标签与免责行；**原图不变**（测试钉死）
- **每个响应声明**："manual association — does not prove the photos show the same location or that work was accepted"

## P6 同步集成

标注/问题写入**自有表 + history**；离线批量创建走 W008 `sync/batch`（事件日志只记录 batch 路径创建的 project/photo——W008 既有设计，测试验证两路共存）。问题修订走乐观并发（同 W008 项目更新语义），**绝不 LWW**。

## P7 iOS 实现要点（详见 w013-mac-handoff.md）

- **SwiftUI Canvas**：`Path`/`Ellipse`/`Rectangle` 按归一化坐标 × 显示尺寸绘制
- **坐标转换**：`norm * displayWidth/Height`；EXIF 用 `CGImageSourceCopyPropertiesAtIndex` 读 0x0112 → `UIGraphicsImageRenderer` 自动处理或手动转置
- **手势**：拖动箭头/矩形用 `DragGesture`；归一化 = `point / displaySize`
- **离线**：标注先存本地 SQLite → 恢复网络后逐条 POST（服务端幂等靠 revision 冲突检测）
- **JSON Schema**：AnnotationCreate/Patch 的 Pydantic schema（/openapi.json）

## 迁移 v8

`mvp_annotations`（norm 坐标 CHECK 约束 + z_order + revision + is_active）/ `mvp_issues`（severity/status CHECK + revision + resolved_at）/ `mvp_issue_history`（append-only 审计）/ `mvp_issue_photos`（role UNIQUE）/ `mvp_issue_links`。幂等：v1→v8 全链走通、重放零操作。
