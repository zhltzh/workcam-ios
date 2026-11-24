# WorkCam W012 安全补充说明（P0 审计与修复）

日期：2026-11-24 · 分支 `dev/windows-report-w012`

## 审计发现与修复

### 发现 1（严重）：W011 水印 evidence_status 可被客户端伪造

**问题**：`WatermarkFields.evidence_status` 是封闭枚举——能阻止自由文本，但**不能**阻止恶意客户端在照片证据实际 `unverified` 时直接提交 `"cryptographically_valid"`，使水印显示 "CRYPTOGRAPHICALLY VERIFIED"。封闭枚举 ≠ 可信状态验证。

**修复**：`POST /photos/{id}/derivatives/watermark` 现在在渲染前调用 `_resolve_actual_evidence_status(conn, photo_id, original_sha256)`——从服务端自己的 `mvp_evidence_links` 解析照片的**真实**最高验证状态（与 W006 integrity 端点同一状态机、同一 rank 表），并**直接覆盖**客户端提交值。同时强制哈希绑定：链接的 `original_sha256` 与照片登记哈希不符的链接不参与状态计算（防跨照片串用）。

**测试**（`tests/test_w012_security.py`）：恶意客户端提交 cryptographically_valid、无证据→快照 no_evidence；有 unverified 链接→快照 unverified；服务端真实验证过→验证态如实穿透；状态变化后重新渲染产生**新**衍生物（旧衍生物保留历史，不被描述为当前状态）。

### 发现 2（严重）：W009 设备 token 可从公开信息推导

**问题**：`device_token = sha256(public_key_b64)`。公钥嵌在每个签名清单（`manifest.public_key`）里——**任何拿到一份证据包或 /verify 请求的攻击者都能算出合法凭据**。token 是 `require_caller` 的唯一凭据。

**修复**：`enroll_device` 改为签发 `secrets.token_hex(32)` 随机不透明密钥——与公钥零关联。公钥仍然入库（挑战-响应用），但不再是凭据推导源。攻击模拟测试：从证据清单提取 public_key → 计算 sha256 → **401 拒绝**。新 token 是密码级秘密，须妥善保管。旧推导 token 经旧文件兼容路径继续有效（不破坏既有联调设备）。

**遗留边界（如实）**：旧推导 token 在其设备改用新 token 或被吊销前仍可作凭据——建议联调环境为已注册设备重新走一次 enroll（同公钥重注册幂等地返回现有 token，新设备自动获得随机 token）。

## 恶意输入专项测试清单

| 场景 | 测试 | 结果 |
|---|---|---|
| 伪造 cryptographically_valid（无证据） | `test_malicious_client_cannot_forge_verified_status` | 服务端覆盖为 no_evidence ✅ |
| 伪造 cryptographically_valid（有 unverified 链接） | `test_forged_status_resolved_to_actual_server_state` | 服务端覆盖为 unverified ✅ |
| 从证据清单提取公钥算 token | `test_captured_manifest_cannot_yield_credential` | 401 ✅ |
| sha256(公钥) ≠ 签发 token | `test_new_token_is_random_not_derived_from_public_key` | 随机不透明 ✅ |
| token 与公钥无关联 | `test_enrolled_token_is_opaque_secret` | 零关联 ✅ |
| 真实验证态如实穿透 | `test_verified_state_flows_through_when_real` | ✅ |
| 状态变化后旧衍生物不冒充当前态 | `test_status_change_invalidates_old_derivative_claim` | 新衍生物 ID + 历史保留 ✅ |
