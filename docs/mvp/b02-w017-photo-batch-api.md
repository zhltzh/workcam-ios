# B02-W017 工程照片批量管理 — API 行为文档

版本 v1 · 分支 `dev/windows-b02-w017-photo-batch` · 基线 `31917e3` (W013-R1)
全量 **658 passed / 3 skipped / 0 failed**（632 基线零回退 + 26 B02-W017 专项）

## 0. 设计约束（硬性）

| 约束 | 实现 |
|---|---|
| 原图与已签名证据不可变 | 批量端点只写 `mvp_photo_tags` / `mvp_photo_business` / `mvp_photos.project_id`（移动）/ `mvp_derivatives`（衍生）。`original_sha256`、`captured_at`、`device_id` 与 evidence links **零写路径**（测试钉死） |
| business metadata 与 evidence 隔离 | 业务分类/备注/标签存**独立表**；`GET /photos/{id}`（证据视图）与 `GET /photos/{id}/business`（业务视图）分离 |
| 50-op sync batch 纪律（W008/W013-R1） | **未触碰** W008 台账与协议。批量照片管理有自己的上限（500 照片/请求）与幂等键（batch_id + photo 级 ledger） |
| 幂等键 | `batch_id`（客户端生成，1..128 字符）+ per-photo request hash（sha256 of `{op, photo_id, params}`，与批内其他照片无关） |
| 事件面对齐 B02-W014 | 批量变更写入 `mvp_sync_events`（entity_type=`photo`，action=`updated`），v8 CHECK 兼容词汇，W014 事件流设备可直接拉取 |
| 迁移编号 | v10（v9 留给 B02-W014 的 event-stream rebuild，合并时零冲突；`migrate()` 按 sorted 键应用，跳号合法） |

## 1. 认证与授权

- 所有端点走 W009 身份层：`X-Device-Token` 头 → `require_caller`。
- 资源 owner-scoped，跨主体访问一律 `404 not_found`（无存在性预言机）。
- 「授权项目」＝ caller 自己的 active 项目。

## 2. 批量操作通用语义（六端点共用）

### 请求

```json
POST /api/v1/photos/batch/{tags|category|note|move|watermark|export}
{ "batch_id": "client-generated", "photo_ids": ["uuid", ...], ... }
```

- `photo_ids`：1..500 张，批内唯一（422 校验）。
- 每张照片**独立事务**：一张失败不回滚其他（W008 per-op 纪律）。

### 响应（每张照片逐项明细）

```json
{
  "batch_id": "...", "op": "tag_add",
  "total": 25, "succeeded": 23, "failed": 2, "replayed": 0,
  "results": [
    {"photo_id": "uuid", "status": "success",
     "data": {"tags": ["rebar", "column"], "added": 2}},
    {"photo_id": "uuid", "status": "failed",
     "error": {"code": "not_found", "message": "photo ... not found"}},
    {"photo_id": "uuid", "status": "duplicate", "data": {...}}
  ],
  "resume_hint": "retry failed photo_ids under the SAME batch_id ..."
}
```

`status` 三态：
- `success` — 本次执行成功，**已记台账**
- `failed` — 本次执行失败，**不记台账**（保持可重试）
- `duplicate` — 台账命中（同 batch_id + 同 request hash），**回放存储结果，不重新执行**

### 幂等与可恢复失败处理（核心规则）

| 场景 | 行为 |
|---|---|
| 同 `batch_id` + 同照片 + 同参数重放 | `duplicate`：返回第一次的存储结果，**绝不重新执行**（水印不重渲染、导出条目用快照、台账不新增行） |
| 同 `batch_id` + 同照片 + **不同参数** | 该照片 `failed`，`error.code = batch_id_conflict`（想改参数请换新 batch_id） |
| 部分失败后重试失败子集 | 客户端带**同一 batch_id** 重发（全量或失败子集皆可）：已成功照片走 duplicate 快速路径，失败照片重新执行 —— 「不重做已成功项」由台账保证 |
| 失败项为何不记台账 | 失败可能是暂态（原图未上传、对端项目暂不可见）。记台账会把失败钉死；不记则同 batch_id 重试恰好只补失败照片 |
| 并发同 batch 同照片 | SQLite `BEGIN IMMEDIATE` + 台账主键 `(batch_id, owner_token, photo_id)`：恰一个 `success`，另一个 `duplicate`（测试钉死，台账仅一行） |
| 台账持久性 | `mvp_batch_ops` 表存业务 DB，服务重启不丢 |

### 台账查询（断网后恢复用）

```
GET /api/v1/photo-batches/{batch_id}?op_type=tag_add|tag_remove|category|note|move|watermark|export
→ { batch_id, op, count, items: [{photo_id, op_type, status:"success", result, created_at}], note }
```

只含成功/重放照片；缺的照片＝未处理或失败，均可继续重试。owner 隔离（B 看不见 A 的台账）。

## 3. 六个批量端点

### 3.1 批量标签 `POST /api/v1/photos/batch/tags`

```json
{"batch_id": "b1", "photo_ids": [...], "action": "add"|"remove",
 "tags": ["rebar", "column"]}
```
- tags：1..50 个/请求，trim 后非空，单个 ≤64 字符，请求内唯一。
- add 幂等（`INSERT OR IGNORE`）：已存在的标签 success 且 `added:0`；remove 同理 `removed:0`。
- 响应 `data.tags` 是该照片当前全量标签集合。

### 3.2 批量归类 `POST /api/v1/photos/batch/category`

```json
{"batch_id": "b2", "photo_ids": [...], "category": "structural"}
```
- `category` ≤100 字符，空字符串＝清除归类。
- 写 `mvp_photo_business.category`，revision +1。

### 3.3 批量备注 `POST /api/v1/photos/batch/note`

```json
{"batch_id": "b3", "photo_ids": [...], "note": "north facade, level 3"}
```
- ≤5000 字符。写 `mvp_photo_business.note`。

### 3.4 批量移动 `POST /api/v1/photos/batch/move`

```json
{"batch_id": "b4", "photo_ids": [...], "target_project_id": "uuid"}
```

**整批前置校验（任务硬性要求）**：先校验全部照片与目标项目，任何一张有问题 → 整批 `400 batch_precondition_failed`，`details.errors` 给出逐张明细，**零执行**（照片留在原项目，测试钉死）。

| 前置错误码 | 场景 |
|---|---|
| `not_found` | 照片不属于 caller（含不存在）；**目标项目**不属于 caller 时直接 404 |
| `project_archived` (409) | 目标项目已归档 |
| `source_project_archived` | 照片所在项目已归档（归档＝冻结工件，不允许移出） |
| `photo_linked_to_issues` | 照片被 inspection issue 关联（before/after）。移动会造成 W013 明令禁止的跨项目关联，先 unlink 再移动 |

执行规则：
- 只改 `mvp_photos.project_id`；evidence links、原图绑定、annotations 原样不动（测试验证移动后原图仍可下载、证据链完整）。
- 移到当前所在项目＝幂等 no-op（`moved:false`），断网重试安全。
- 成功发 `photo/updated` 事件，`payload_hint="batch_moved:{target}"`。

### 3.5 批量水印 `POST /api/v1/photos/batch/watermark`

```json
{"batch_id": "b5", "photo_ids": [...], "template": "construction",
 "fields": {"show_project_name": true, "project_name": "Tower B"},
 "company_logo_b64": null}
```

- **复用 W011 单张端点的同一渲染路径**（`render_watermark_for_photo`，从原端点提取共享，零语义漂移）：
  - 原图只读；衍生图写独立 content-addressed store。
  - `evidence_status` **服务端解析覆盖**（客户端伪造无效，W012 纪律）。
  - W011 幂等键 `(photo, template, params-hash, original-sha)` 双保险。
- 请求级前置校验：模板与字段组合渲染不出任何行 → 整批 `400 nothing_to_render`（不逐张失败）。
- per-photo 失败：`original_not_uploaded`（补传原图后**同 batch_id 重试**该照片即可恢复）；渲染引擎异常 → `render_error`。
- 渲染在台账事务外执行（CPU 密集）；中断重放时已渲染照片走台账 duplicate，未完成照片靠 W011 幂等探测兜底。

### 3.6 批量导出 `POST /api/v1/photos/batch/export`

```json
{"batch_id": "b6", "photo_ids": [...], "include_originals": true}
→ { ..., "export_id": "uuid", "zip_sha256": "...",
    "download_path": "/api/v1/photo-exports/{export_id}/download" }
```

- ZIP 内容：`manifest.json`（逐照片条目：元数据 + 标签/分类/备注 + 关联 issues + 服务器解析的 evidence_status + trust 注记）、`originals/{sha256}.jpg`（**内容寻址**：重复内容只存一份，与重复识别视图一致）、`verification-guide.txt`。
- manifest 含逐文件 SHA-256 清单，离线可验证完整性。
- **不导出** token / enrollment code / 密钥材料（测试扫描钉死）。
- not_found 照片逐项 failed，其余正常导出；失败子集修复后同 batch_id 重试 → 已导出照片 duplicate（快照条目复用），新照片用当前状态，生成**新的完整 export**（旧包不可变仍可下载）。
- 下载：`GET /api/v1/photo-exports/{export_id}/download`（owner 隔离，X-Export-Sha256 校验头）。

## 4. 查询面

### 4.1 搜索与筛选 `GET /api/v1/photos/search`

| 参数 | 说明 |
|---|---|
| `project_id` | 单项目过滤 |
| `tag` | 含该标签 |
| `category` | 业务分类（空串＝未分类） |
| `issue_status` | `open/in_progress/resolved/closed`（照片关联的 issue 处于该状态）／`linked`（有任何关联）／`none`（无关联） |
| `captured_from` / `captured_to` | 客户端声明时间（ISO 字符串字典序比较，**untrusted，仅作排序/过滤**） |
| `q` | 子串搜索：文件名 ∨ 备注 ∨ 标签（大小写不敏感，LIKE 元字符已转义） |
| `sort` | `captured_at`（默认）／`created_at`／`filename`／`byte_size` |
| `order` | `asc`／`desc`（默认 desc） |
| `limit` | 1..200（默认 50） |
| `cursor` | 上一页的 `next_cursor` |

**稳定分页（任务硬性要求）**：keyset 分页，页键＝`(sort_value, photo_id)` 全序。
- 翻页期间插入新行**不会**造成已翻页漂移：排序值在 cursor 之前的新行不重复出现，之后的正常前进（offset 分页的跳行/重复窗口为零，测试用 3 个不同插入位置钉死）。
- `next_cursor` 编码了 sort/order；换排序方式用旧 cursor → `400 invalid_cursor`。
- 同排序值多行时 `photo_id` 作 tiebreaker，保证全序无重复（测试钉死）。
- `total` 是查询时点快照（参考值），响应注明。

### 4.2 重复识别 `GET /api/v1/photos/duplicates`

- 按 `original_sha256` 分组（SHA-256 即身份，W007 规则），caller 全部项目范围内，`count ≥ 2` 的组。
- 响应逐组给出内容哈希、数量、byte_size、逐照片明细（跨项目同内容也归同一组）。
- **只指出，绝不自动删**（响应 note 声明；原图不可变证据，删除是 API 外的人工决策）。
- 组级 keyset 分页（`next_cursor`＝末组 sha）。

### 4.3 单张业务视图 `GET /api/v1/photos/{photo_id}/business`

```
→ { photo_id, tags: [...], category, note, revision, updated_at,
    note_trust: "business metadata is HUMAN-managed content, ..." }
```

## 5. 错误码总表

| HTTP | code | 场景 |
|---|---|---|
| 401 | unauthorized | 缺 X-Device-Token / token 无效 / 已吊销 |
| 404 | not_found | 跨主体或不存在（照片/项目/导出包），无预言机 |
| 400 | batch_precondition_failed | move 整批前置校验失败（零执行），details.errors 逐张 |
| 409 | project_archived | move 目标项目已归档 |
| 409 | batch_id_conflict* | 同 batch 同照片不同参数（*per-photo failed 项，非 HTTP 409） |
| 400 | invalid_cursor | cursor 与 sort/order 不匹配或损坏 |
| 400 | unknown_template / nothing_to_render / invalid_logo | 水印请求级校验 |
| 400 | empty_body 等 | 参数校验 |
| 422 | (pydantic) | 超上限（>500 照片、>50 标签、>5000 备注等）、photo_ids 重复、空列表 |

## 6. 数据模型（迁移 v10）

```
mvp_photo_tags      (photo_id, tag) PK            标签集合，add/remove 幂等
mvp_photo_business  photo_id PK                   category/note/revision（业务元数据，与证据隔离）
mvp_batch_ops       (batch_id, owner_token, photo_id) PK   批量台账：成功项持久化
mvp_photo_exports   export_id PK                  导出包登记（zip_sha256 钉死）
```

## 7. 离线客户端（B02-W014 对齐）要点

- 批量变更广播为 `photo/updated` 事件：`GET /api/v1/sync/events?entity_type=photo` 可拉取，游标语义不变。
- 事件仅由批量**服务端**操作产生；离线设备的本地编辑仍走 W008 `sync/batch`（50-op 上限不变，两套台账互相独立）。
- Mac 端重试建议：收到批量响应后把 `failed` 项连同原 batch_id 存入重试队列；恢复网络后原样重发（参数不可变，否则收到 batch_id_conflict）。
