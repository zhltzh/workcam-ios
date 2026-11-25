# WorkCam API 契约与接口参考（B02-W026）

版本 v1 · 分支 `dev/windows-b02-w026-api-contract` · 基线 `31917e3`（W013-R1）
契约测试 **13 passed**（`backend/tests/test_b02_w026_contract.py`），全量 **645 passed / 3 skipped / 0 failed**（632 基线零回退 + 13 契约）

本文件汇总当前后端**已实现的全部接口面**，以代码与真实响应为唯一事实来源：

- 机器契约：`docs/api/openapi.json`（由 `backend/scripts/b02_w026_export_openapi.py` 从运行中的应用**真实导出**，再人工补齐 description/example/security 标注——补齐范围见文件内 `x-b02-w026-enrichment` 节点，逐字段列明）
- 真实示例：`docs/api/b02-w026-mock-data.json`（70 次真实 HTTP 调用的完整请求/响应记录，`backend/scripts/b02_w026_generate_fixtures.py` 产出，**零手写**）
- 漂移防线：契约测试把 openapi.json 与 mock 数据作为 oracle 重放真实调用并断言结构一致——文档说谎测试就红

未实现的能力一律标注 **Not Implemented**。已交付但**未合入基线**的分支能力（B02-W014/W015/W017）集中列在附录 A，本基线契约不含它们的端点与错误码。

---

## 0. 接口面总览

**47 paths / 58 operations**，契约测试按 surface 分组钉死（`test_endpoint_inventory_counts`）：

| 面 | paths | ops | 模块 |
|---|---|---|---|
| verify 核心（W002/W003 时代冻结面） | 5 | 5 | `app/api/verify.py` |
| time-anchor（实验） | 5 | 5 | `app/api/time_anchor.py` |
| upload/experimental（实验） | 7 | 7 | `app/api/upload.py` |
| /api/v1 业务核心（projects/photos/evidence-links/originals/derivatives/reports/annotations/issues） | 23 | 34 | mvp / originals / derivatives / reports / inspection |
| /api/v1 设备管理 | 5 | 5 | device_admin |
| /api/v1 同步 | 2 | 2 | sync |

其中 /api/v1 合计 30 paths / 41 ops。完整端点清单见 `openapi.json` `paths` 键（契约测试断言其与线上导出逐 path 逐 method 一致）。

## 1. 通用约定

### 1.1 身份认证（X-Device-Token）

- **全部 `/api/v1` 端点**要求 `X-Device-Token` 头（`app/api/identity.py::require_caller`）。
- Token 是 **服务端随机签发的 64-hex 不透明凭据**（W012 安全变更：绝不从公钥派生），`POST /api/v1/devices/enroll` 用带外注册码（`WORKCAM_ENROLLMENT_CODE`[_FILE]）+ P-256 公钥换取；同一公钥重复 enroll 幂等返回同一 token。
- 吊销即时生效：`POST /api/v1/devices/{token}/revoke`（X-Admin-Code 门控）。
- **legacy 关闭现状（W013-R1 P0）**：`devices.jsonl` 里 `token == sha256(public_key_b64)` 的派生 token 一律 401 `legacy_token_must_re_enroll`（公钥印在每个证据 manifest 里，派生凭据任何人可算）；**非派生的 legacy 随机 token 保持读兼容可用**。以上两条均有真实调用证据（mock ERRORS 场景 `legacy-derived-token-refused` / `legacy-random-token-still-valid`）。
- 限流：每 token 滑动窗口 60 次/60 秒（SQLite 持久化），超限 429 `rate_limited`。
- **ownership = token**：跨主体访问一律 404 `not_found`，与"不存在"完全一致（无存在性预言机）。客户端提交的 `device_id` 永远只是数据。
- 上传实验面的 auth 是独立开关（见 §4.3）；`/upload/experimental/auth/challenge|verify` 走同一 SQLite 身份存储。

### 1.2 错误响应：统一 typed envelope

业务面（`app/db.py::ApiError`）：

```json
{ "detail": { "code": "<machine_code>", "message": "<human>",
              "details": { "<extra>": "…" } } }
```

- `details` 常见键：`current_revision`（乐观并发）、`existing_photo_id`（幂等冲突）、`allowed`（非法状态机跳转）、`recovery`（恢复建议）。
- 请求 schema 校验失败是 FastAPI/pydantic 的 **422**（`detail` 为数组，不是本 envelope）——两类错误并存，客户端都要处理。
- 实验面（time-anchor / upload）的历史代码用**裸 HTTPException**（`detail` 为纯字符串），未纳入 typed envelope——这是实现事实，已如此记录。

### 1.3 全量错误码总表（真实代码对齐，按模块分组）

**认证/身份**：`unauthorized` 401（缺头/未知/已吊销）· `legacy_token_must_re_enroll` 401 · `rate_limited` 429 · `enrollment_closed` 403 · `bad_enrollment_code` 403 · `invalid_public_key` 400 · `device_revoked` 409（已吊销公钥重新 enroll）· `forbidden` 403（challenge 属他人）

**通用**：`invalid_uuid` 400 · `not_found` 404 · `empty_patch` 400 · `service_unavailable` 503（业务库迁移失败时拒绝服务）

**项目**：`project_archived` 409 · `revision_conflict` 409（`details.current_revision` + `recovery`）

**照片**：`idempotency_conflict` 409 · `hash_mismatch` 400（上传字节与登记哈希不符）· `binding_conflict` 409 · `empty_body` 400 · `original_not_uploaded` 404 · `photo_project_mismatch` 400 · `duplicate_photo_id` 400 · `duplicate_issue_id` 400 · `issue_project_mismatch` 400

**证据链接**：`missing_material` 400 · `reference_mismatch` 400 · `hash_binding_mismatch` 400

**标注**：`too_many_annotations` 413（上限 50/照片）· `annotation_deleted` 409 · `annotation_validation_failed` 400（sync 通道）

**问题**：`issue_closed` 409 · `invalid_status_transition` 409（`details.allowed`）

**衍生/报告**：`unknown_template` 400 · `invalid_logo` 400 · `object_missing` 404 · `report_generation_failed` 500 · `package_too_large` 413

**sync 批内回执**（HTTP 200，回执级 `error.code`）：`op_id_conflict`（同 id 不同内容）· `unknown_op`（未识别 op 名）；executor 的任何 ApiError 也会以各自 code 进入 rejected 回执（`revision_conflict` / `project_archived` / `annotation_deleted` / `issue_closed` / `invalid_status_transition` / `photo_project_mismatch` / `empty_patch` / `annotation_validation_failed` / `not_found` 等）。

**未合入分支的错误码**（本基线**不存在**，勿按它们写客户端）：`unresolved_local_ref`（B02-W014）、`batch_precondition_failed` / `batch_id_conflict` / `invalid_cursor` / `source_project_archived` / `photo_linked_to_issues` / `nothing_to_render` / `render_error` / `lidar_ref_issue_mismatch`（B02-W017）、`invalid_request` / `invalid_payload`（W017 批量文档通用名——基线没有这两个字符串，真实代码用上面那张表）。

### 1.4 分页规范（本基线：offset；W017 keyset 未合入）

基线**只有 offset 分页**，两处使用：

- `GET /api/v1/projects?limit&offset&status`、`GET /api/v1/projects/{pid}/photos?limit&offset`
- 信封：`{ "items": [...], "total": N, "limit": L, "offset": O }`
- `limit` 默认 20、**clamp 到 [1,100]**（传 99999 得 200 + limit=100，不是 422——真实行为，见 mock `validation-422-oversize-page`）；`offset` 负数 422
- 排序稳定（`created_at DESC, <id>` tiebreaker）
- 其余列表端点（annotations/issues/reports/evidence-links/derivatives/issue-photos）不分页，返回全量 + `total`

**W017 的 keyset cursor**（`GET /api/v1/photos/search` 的 `next_cursor` 页键 `(sort_value, photo_id)` 全序）已在分支 `dev/windows-b02-w017-photo-batch` 交付并通过 658 测试，但**未合入基线 31917e3**——本基线上它是 **Not Implemented**。合入后两者并存：业务列表沿用 offset（小集合全量页），照片搜索用 keyset（大集合稳定翻页）。

### 1.5 幂等规范（三层台账）

| 层 | 键 | 语义 | 位置 |
|---|---|---|---|
| 照片登记 | `(project_id, idempotency_key)` | 同 key + **同内容**（比对 `original_sha256`+`original_filename`+`byte_size`，不含 mime_type/captured_at）→ 201 重放原照片 + `_idempotent_replay:true`；同 key 不同内容 → 409 `idempotency_conflict` | `mvp_photos` UNIQUE |
| sync 批 | `operation_id` + owner + **request_hash**（`sha256(canonical json of {op, payload})`） | 同 hash → `duplicate` 回放存储回执，零重执行；不同 hash → `rejected op_id_conflict`；**确定性失败也记台账**（重试同 id 得同一 rejection，想重试必须换新 operation_id） | `mvp_sync_ops`（业务库，重启不丢） |
| W017 批量 | `batch_id` + photo + request hash | 见附录 A（未合入） | `mvp_batch_ops`（未合入） |

另有两个非台账幂等：项目 archive 幂等（重复归档 200 返回同状态）；原图上传幂等（同字节重传 200 `idempotent_replay`）。

### 1.6 信任语义（贯穿全 API 的字面文案）

- `captured_at` / `device_id`：**client-claimed, untrusted**（每条 photo 响应带 `trust_notes` 字面量，契约测试断言逐字一致）
- 标注：`human-added annotation content`；问题严重度：`severity/status are HUMAN records; no AI assessment implied`
- 原图存储：`file integrity verified; photo authenticity is NOT claimed by storage`；integrity 视图的 `photo_authenticity_proven` **恒为 false**
- 衍生图/水印 `evidence_status`：**服务端从已验证 evidence links 解析并覆盖客户端声明**（`derivatives.py::_resolve_actual_evidence_status`）
- 报告：`SNAPSHOT at generation time`；交付包含 `verification-guide.txt`（不做法律认证声明）

## 2. 接口版本兼容说明

| 维度 | 基线语义 |
|---|---|
| URL 前缀 | `/api/v1` 是**唯一**业务版本面；没有版本协商/协商头。v1 的兼容承诺 = **schema additive-only**（新字段可加、不删不改既有字段名与类型），由契约测试的结构断言兜底 |
| manifest 协议版本 | `SignatureManifest.version` ∈ {`1.0`, `1.1`}（`SUPPORTED_VERSIONS`），不支持的版本 → `/verify` 立即 INVALID |
| 模板版本 | 水印模板 `template_version` 记录在 derivative 行（当前 `1`）；报告 `template_version` 记录在 `mvp_reports`（当前 `1`；W015 分支为 `2`，未合入）。**模板不升级内容**：已生成的报告行永不改写，重生成 = 新行 |
| sync 协议 | op 名单冻结于 W008/W013-R1 的 11 种（`SyncOp.op` Literal）；事件流 action 词表 = `created/updated/archived/bound/status_changed/soft_deleted/photo_linked/photo_unlinked`（基线实际只产生 `created/updated`，见 §4.1）。B02-W014 的 local-ref 扩展（`"local:<id>"` + `unresolved_local_ref`）**未合入**——基线上 local ref 不是协议的一部分 |
| OpenAPI 版本 | `info.version = "0.1.0"`（`app/main.py`）；实验面各自在响应里自声明 `experimental: true` |

## 3. verify 核心与实验面（冻结 + 实验）

### 3.1 verify 核心（无认证，冻结面）

| 端点 | 说明 | 示例 |
|---|---|---|
| `GET /health` | `{"status":"ok","service":"workcam-verify"}` | — |
| `POST /verify` | manifest（+可选 photo_base64）→ `VerifyResponse`（evidence_status 六级：fully_attested / cryptographically_valid / signature_valid / partial / unverified / invalid） | mock F3 `verify-manifest` |
| `POST /register-key` | **TEST only，参数走 QUERY**（`public_key_b64`、`device_id`、`allow_test_registration=true`）；拒绝覆盖；生产（`WORKCAM_ENV=production`）返回 REGISTER_DISABLED | mock F3 `register-key-test` |
| `POST /store-record` | 重放检测存储：photo_base64 必填 + 签名/注册/哈希全验过才占位 | mock F3 `store-record` |
| `GET /check-record/{record_id}` | 存在性查询 | — |

### 3.2 time-anchor（实验，无认证）

| 端点 | 说明 | 示例 |
|---|---|---|
| `POST /time-anchor/anchor` | 提交 batch digest → 签名回执。**字段守卫只拒控制字符与 `=`**（空格/标点合法，实测） | mock F3 `anchor-batch` |
| `POST /time-anchor/verify?check_storage=` | 验证 receipt。⚠️ **anchor 响应 ≠ receipt**：客户端必须补回 unsigned 字段（`device_reported_start_time/end_time`、`stored_at`）再提交，否则 422（mock ERRORS `anchor-response-is-not-a-receipt`） | mock F3 `anchor-verify` |
| `GET /time-anchor/public-key` | PEM 公钥 + key_id（测试密钥） | — |
| `GET /time-anchor/receipt/{anchor_id}` | 取存储回执 | — |
| `GET /time-anchor/health` | 实验自声明 | — |

### 3.3 upload/experimental（实验，三重开关）

默认**全部 503**：需 `WORKCAM_UPLOAD_EXPERIMENTAL=1` 显式开启，且 `WORKCAM_ENV=production` 之下硬性拒绝启动（启动守卫 G1 直接终止进程）。

| 端点 | 说明 |
|---|---|
| `POST /upload/experimental/auth/challenge` | 单次 nonce（60s）；需 `WORKCAM_UPLOAD_AUTH=1` |
| `POST /upload/experimental/auth/verify` | `{device_token, nonce, signature_b64}` 验签 |
| `POST /upload/experimental/batches` | 注册分块计划（batch_id=hex8，chunk ≤4MiB、≤512 块、batch ≤2GiB、活跃 ≤64、TTL 72h） |
| `PUT .../batches/{bid}/chunks/{index}` | 原始字节 body；Content-Length 必须等于计划值；同块同字节重传 → `duplicate_acked`，不同字节 409 |
| `GET .../batches/{bid}` | missing/received/complete 状态 |
| `POST .../batches/{bid}/complete` | 重组 + 全量哈希复核；整批 SHA 不符 → 409 且保留分块可重传 |
| `GET .../batches/{bid}/content` | COMPLETE 后流式回传 assembled.bin |

## 4. /api/v1 业务面

### 4.1 同步协议汇总（W008 → W013-R1 → B02-W014）

**事件流 `GET /api/v1/sync/events?cursor&limit&entity_type`**

- 游标 = 客户端已见最大 `seq`（SQLite AUTOINCREMENT，**重启稳定**）
- 高水位窗口：先取 `MAX(seq)` 再查 `cursor < seq <= min(high_water, cursor+limit)`——mid-pull 插入落到下一页，不漏不重；响应 `{items, cursor, has_more, limit}`
- `entity_type` 过滤词表：`project|photo|evidence_link|original`
- **基线事实（契约测试钉死）**：只有 `project_create/project_update/photo_register` 发事件 → 实际只会拉到 `project/created`、`project/updated`、`photo/created`；`evidence_link/original` 在过滤词表里但**基线无任何代码路径产生它们**；标注/问题的生命周期**不进事件流**（B02-W014 的 v9 重建已交付，未合入）
- owner 隔离：设备 B 拉不到设备 A 的任何事件

**批同步 `POST /api/v1/sync/batch`**

- ≤50 op/批，**按序执行**，逐项回执 `{batch_received, receipts[{operation_id, status: applied|duplicate|rejected, result, error}], id_map}`
- 依赖规则：批内"建项目 → 登记照片"靠前序回执的 server id（`result.server_project_id`）衔接；`id_map` 汇总 `local_id → server_id`
- **payload 必须自带 `operation_id`**（executor 的 pydantic 模型必填；顶层 `operation_id` 只用于台账）。缺省 → 当前实现是 **500**（未捕获 pydantic 异常，已知缺口，见不一致清单 #3；契约测试钉住该行为）
- op 名单（11 种）：`project_create / project_update / photo_register / annotation_create / annotation_update / annotation_delete / issue_create / issue_update / issue_transition / issue_photo_link / issue_photo_unlink`
- 信任边界：payload 夹带 `verification_status` 被模型忽略——sync 永远写不了可信状态

### 4.2 业务核心（projects / photos / evidence）

**项目**：`POST /api/v1/projects`（201）· `GET /projects?limit&offset&status` · `GET/PATCH /projects/{pid}` · `POST /projects/{pid}/archive`（幂等；归档后禁写、禁加照片，证据保全）

**照片**：`POST /projects/{pid}/photos`（元数据登记；幂等见 §1.5；`original_sha256` 登记后**无任何 API 可改**）· `GET /projects/{pid}/photos` · `GET /photos/{photo_id}`（归属链经项目）

**原图（W007）**：`PUT /photos/{pid}/original`（raw bytes；字节哈希必须等于登记哈希，否则 400 `hash_mismatch`；不同字节对已绑定照片 409 `binding_conflict`）· `GET .../original`（`X-Original-Sha256` 头）· `GET .../original/integrity`（storage 与 evidence 分离视图）

**证据链接**：`POST /photos/{pid}/evidence-links`（请求体**没有状态字段**——链接一律 `unverified` 起步；`original_sha256` 链接时绑定）· `GET ...` · `POST .../{lid}/verify`（提交材料，服务端跑既有验证路径并回写；绑定守卫 `reference_mismatch`/`hash_binding_mismatch`；**材料 schema 校验失败也是 200 + verification_failed**——验证结果即 API 结果，这是设计而非错误）

状态机：`unverified → signature_valid | cryptographically_valid | server_anchored | verification_failed`，转换只由服务端验证写入。

### 4.3 设备管理（W009）

`POST /api/v1/devices/enroll`（注册码）· `POST /devices/{token}/revoke`（X-Admin-Code）· `GET /devices/{token}`（X-Admin-Code；只回 fingerprint 不回公钥）· `POST /devices/challenge`（nonce）· `POST /devices/challenge/verify`（P-256 验签）

MVP 已知差距（`w009-security.md` 记录）：注册码兼任 admin code，没有真正的操作员 RBAC。

### 4.4 衍生图（W011）

`POST /photos/{pid}/derivatives/watermark`（模板：minimal/construction/inspection/corporate/evidence；`evidence_status` 服务端解析覆盖；幂等键 `(photo, template, params-hash, original-sha)`）· `GET /photos/{pid}/derivatives` · `GET /derivatives/{did}/download`（`X-Derivative-Sha256`/`X-Original-Sha256` 头）

### 4.5 报告与交付包（W012）

`POST /projects/{pid}/reports`（report_type=site_photo|inspection|evidence；page_size=a4|letter；per_page∈{1,2,4}；photo_ids ≤500；issue_ids ≤500；operator/company_name/company_logo_b64/include_evidence_appendix）→ 201 `{report_id, report_number: "WC-YYYYMMDD-XXXXXXXX", template_version, pdf_sha256, page_count, …}`

`GET /projects/{pid}/reports` · `GET /reports/{rid}`（detail 含 `photo_ids`、`snapshot`——**键为 photo UUID**、`issue_snapshot`）· `GET /reports/{rid}/download`（PDF，`X-Report-Sha256`）· `POST /reports/{rid}/delivery-package`（ZIP：report.pdf + originals/ + derivatives/ + evidence/ + manifest.json + verification-guide.txt；文件数 ≤2000；绝不导出 token/注册码/密钥）

快照不可变：重生成 = 新行；嵌入图按最长边 1800px 降采样（原图不动）。

### 4.6 标注与问题（W013）

**标注**：`POST /photos/{pid}/annotations`（type: arrow|rectangle|circle|freehand|text|number_marker；归一化坐标 0..1；text 需 text_content、number_marker 需 marker_number、arrow/rectangle/circle 需 x2/y2；≤50 active/照片 413）· `GET ...?include_inactive=`（软删历史）· `PATCH /annotations/{aid}`（`expected_revision` 乐观并发；409 `annotation_deleted`）· `DELETE /annotations/{aid}`（软删，历史保留）

**问题**：`POST /projects/{pid}/issues`（severity: low|medium|high|critical；status 起 open）· `GET /projects/{pid}/issues?status&severity` · `GET /issues/{iid}`（detail + 全量 history + photos）· `PATCH /issues/{iid}`（乐观 revision；severity/status 变更记 history；closed 不可改）· `POST /issues/{iid}/photos`（role: before|progress|after，一 role 一照片）· `DELETE /issues/{iid}/photos/{pid}/{role}` · `GET /issues/{iid}/photos` · `POST /issues/{iid}/comparison-image`（并排衍生 JPEG，`X-Comparison-Sha256` 等头）

状态机（非法跳 409）：`open→{in_progress}`；`in_progress→{resolved, open}`；`resolved→{closed, in_progress}`；`closed` 终态。`resolved_at` 随状态自动记/清。

## 5. 示例索引（全部来自真实调用）

`docs/api/b02-w026-mock-data.json` 按 F1-F6 流程 + ERRORS 目录组织，70 次调用逐条含 request/response/status/headers。速查：

| 场景 | 覆盖步骤 |
|---|---|
| F1 设备注册认证 | enroll · challenge · challenge/verify · status(admin/非 admin) |
| F2 项目/照片/原图 | 建项目 · 列表 · 登记(含幂等重放/冲突) · 原图上传(含 hash_mismatch) · integrity |
| F3 证据链 | register-key · /verify · store-record · evidence-link CRUD · link verify · anchor 全链 |
| F4 水印 | render · replay · list · download |
| F5 标注/问题 | annotation CRUD(含 revision_conflict/软删) · issue CRUD(含非法跳转) · role 链接 · comparison |
| F6 报告/同步 | report 生成/详情/下载/交付包 · sync batch(applied/duplicate) · events 拉取 |
| ERRORS | 401 三种形态 · 400/404/409 · 422 · 429 · 503 · legacy token 两面 |

每步请求头中的 `X-Device-Token` 已脱去（mock 文件记录的是结构，不回放凭据）。

## 附录 A：已交付但未合入基线的分支面（Not in baseline 31917e3）

| 分支 | 增量 | 对 Mac 端的影响 |
|---|---|---|
| `dev/windows-b02-w014-offline-sync` | 迁移 v9：事件流 rebuild（entity_type 加 annotation/issue；action 加 soft_deleted/photo_linked/photo_unlinked，**seq 原值迁移**）· 批内 local ref（`"local:<id>"`，409 `unresolved_local_ref`）· 软删广播 · Swift Codable 离线队列规范 §6/§7 | 事件流拉取面兼容（游标继续有效）；local ref 在基线上**不可用**（基线把 `local:` 前缀当普通 id 查 404→rejected） |
| `dev/windows-b02-w015-report-enhance` | 报告模板 v2：ISS-xxx 登记、severity/status 汇总、前后对比图、PH-xxx 编号、inspector/project_metadata、remediation_note（迁移 v9）、LiDAR 记录引用 | 报告请求体**附加字段**；旧字段不变。基线报告请求体附加这些字段会被 pydantic 忽略（模型默认裁剪）——实际验证归 Mac 联调 |
| `dev/windows-b02-w017-photo-batch` | 六个批量端点（tags/category/note/move/watermark/export，batch_id+photo 级台账，`mvp_batch_ops`）· `GET /photos/search` **keyset cursor** · `GET /photos/duplicates` · `GET /photos/{id}/business` | 全部端点在基线上 404。分页并存约定见 §1.4 |

**合并顺序提醒**：v9 编号冲突——W015 的 issue 表迁移与 W014 的事件流 rebuild 都叫 v9；W017 用 v10 规避。合并时必须先重排迁移号再落库（W017 文档已声明此纪律）。
