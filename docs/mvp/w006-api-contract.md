# WorkCam W006 API 契约（/api/v1）

版本：v1 · 基线：`dev/windows-mvp-w006`（基于 `8a05391`）
所有响应为 JSON。错误统一结构：

```json
{ "detail": { "code": "<machine_code>", "message": "<human>", "details": {} } }
```

## 认证

全部 `/api/v1` 端点要求 `X-Device-Token` 请求头（已注册设备令牌）。
- 缺失/未注册 → `401 unauthorized`
- 资源不属于该令牌 → `404 not_found`（与不存在完全一致——不做存在性预言机）

## 1. 项目管理

### POST /api/v1/projects → 201

```json
// 请求
{ "name": "Bridge N-12", "description": "Main span", "site_address": "Nanjing" }
// 响应
{ "project_id": "<uuid4>", "name": "Bridge N-12", "description": "Main span",
  "site_address": "Nanjing", "status": "active",
  "created_at": "…", "updated_at": "…", "archived_at": null }
```

校验：`name` 必填 1–200 字符且含非空白；`description` ≤2000；`site_address` ≤500。

### GET /api/v1/projects → 200

查询参数：`limit`（默认 20，上限 100）、`offset`（默认 0）、
`status`（`active`|`archived`|`all`，默认 `active`）。

```json
{ "items": [ /* Project */ ], "total": 42, "limit": 20, "offset": 0 }
```

排序稳定：`created_at DESC, project_id`。仅返回调用者拥有的项目。

### GET /api/v1/projects/{project_id} → 200 / 404

`project_id` 非 UUIDv4 → `400 invalid_uuid`。

### PATCH /api/v1/projects/{project_id} → 200

请求体为可选字段（`name`/`description`/`site_address`），至少一项，
否则 `400 empty_patch`。已归档项目 → `409 project_archived`。

### POST /api/v1/projects/{project_id}/archive → 200（幂等）

再次归档返回相同状态与 `archived_at`（不报错）。归档后：
- 项目不可 PATCH（`409 project_archived`）
- 不可新增照片（`409 project_archived`）
- 照片与证据关联**保留**（无物理删除——证据保全原则）

## 2. 照片管理

### POST /api/v1/projects/{project_id}/photos → 201

登记**元数据**（不接收原图二进制；大文件上传协议沿用既有实验通道，不在本轮）。

```json
// 请求
{ "idempotency_key": "client-generated-unique-key",
  "original_sha256": "<64 位小写十六进制>",
  "original_filename": "IMG_0001.jpg",
  "mime_type": "image/jpeg",
  "byte_size": 1234567,
  "captured_at": "2026-11-23T08:00:00Z",
  "device_id": "iPhone15,3" }
// 响应（201）
{ "photo_id": "<uuid4>", "project_id": "…", "original_sha256": "…",
  "original_filename": "…", "mime_type": "…", "byte_size": 1234567,
  "captured_at": "…", "received_at": "<server UTC>", "device_id": "…",
  "evidence_id": null, "created_at": "…",
  "trust_notes": { "captured_at": "client-claimed, untrusted",
                   "device_id": "client-claimed, untrusted" } }
```

**信任语义**：`captured_at` 与 `device_id` 是**客户端声明数据**，永不标记为可信。
`received_at` 才是服务端时间。

**幂等**：`(project_id, idempotency_key)` 唯一。
- 相同内容重放 → 201，返回原照片（`"_idempotent_replay": true`）
- 相同 key 不同内容 → `409 idempotency_conflict`（详情含 existing_photo_id）

**不可变**：`original_sha256` 写入后无任何 API 可修改（照片无 PATCH 端点）。

校验：`original_sha256` 必须匹配 `^[0-9a-f]{64}$`；`mime_type` 必须 `image/*`；
`byte_size` 1–100MB。

### GET /api/v1/projects/{project_id}/photos → 200

`limit`/`offset` 分页；`items` + `total`。

### GET /api/v1/photos/{photo_id} → 200 / 404

照片归属链经所属项目判定——必须同时拥有该照片所在项目。

## 3. 证据关联

### POST /api/v1/photos/{photo_id}/evidence-links → 201

```json
// 请求（仅两个字段——客户端不能提交验证状态）
{ "evidence_reference": "<既有验证系统中的标识>",
  "evidence_type": "signature_manifest" | "time_anchor" }
// 响应
{ "evidence_link_id": "<uuid4>", "photo_id": "…", "evidence_reference": "…",
  "evidence_type": "signature_manifest",
  "original_sha256": "<关联时刻照片哈希，防跨照片误关联>",
  "verification_status": "unverified",
  "failure_reason": "awaiting server-side /verify result for record …",
  "verified_at": null, "created_at": "…" }
```

**状态语义（完全对齐既有状态机）**：
- `unverified`：链接已登记，服务端尚未运行验证
- `signature_valid`：签名数学有效但公钥未注册（设备身份未确立）
- `cryptographically_valid`：签名有效 + 公钥已注册（服务端验证写入）
- `server_anchored`：锚点回经服务端验证写入
- `verification_failed`：保留 `failure_reason` 审计记录

**新链接一律以 `unverified` 创建**——请求体里带任何 `verification_status` 字段都会被忽略。状态转换只发生在服务端验证之后（见下方 verify 端点）。`original_sha256` 在链接时刻绑定，防止照片 A 的证据被关联到照片 B。

### POST /api/v1/photos/{photo_id}/evidence-links/{evidence_link_id}/verify → 200

**服务端验证回写**：客户端提交验证材料，服务端运行**既有**验证路径（W002 签名服务 / W003 锚点服务，未修改）并把结果回写。这是唯一的非 unverified 状态写入者。

```json
// signature_manifest 类型的请求（photo_base64 可选，用于字节级复核）
{ "manifest": { …完整 SignatureManifest… }, "photo_base64": "…" }
// time_anchor 类型的请求
{ "receipt": { …完整 TimeAnchorReceipt… } }
```

**追溯与绑定防护（400 快速拒绝，不写状态）**：
- `manifest.record_id` 必须等于链接登记的 `evidence_reference`（`reference_mismatch`）
- `manifest.photo_hash` 必须等于照片登记的 `original_sha256`（`hash_binding_mismatch`——防跨照片误关联）
- `receipt.batch_id` 必须等于 `evidence_reference`（`reference_mismatch`）

**状态映射（既有语义，不夸大）**：
- 签名有效 + 公钥已注册 → `cryptographically_valid`
- 签名有效 + 公钥未注册 → `signature_valid`（failure_reason 注明设备身份未确立）
- 结构/签名/字节哈希任一失败 → `verification_failed` + 原因
- 锚点回执验证通过 → `server_anchored`

**可重跑**：verify 是可重复执行的服务端动作——失败后用正确材料重验可恢复为有效状态（每次验证覆盖 verified_at / failure_reason）。

**材料中仍无状态字段**：请求体注入的 `verification_status` 会被忽略，数据库里的状态永远是服务端计算值。

### GET /api/v1/photos/{photo_id}/evidence-links → 200

`items` + `total`，按 `created_at DESC`。

## 错误码总表

| code | HTTP | 含义 |
|---|---|---|
| `unauthorized` | 401 | 缺令牌/未注册 |
| `invalid_uuid` | 400 | 路径参数非 UUIDv4 |
| `not_found` | 404 | 不存在**或**不归你（不区分） |
| `empty_patch` | 400 | PATCH 无字段 |
| `project_archived` | 409 | 归档项目不可改/不可加照片 |
| `idempotency_conflict` | 409 | 同 key 不同内容 |
| `missing_material` | 400 | verify 缺 manifest/receipt |
| `reference_mismatch` | 400 | 材料 record_id/batch_id 与登记引用不符 |
| `hash_binding_mismatch` | 400 | 材料 photo_hash 与照片登记哈希不符（防跨照片误关联） |
