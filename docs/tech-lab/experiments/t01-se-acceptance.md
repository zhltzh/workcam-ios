# T01 — Secure Enclave 真机验收脚本（Mac 执行）

状态：协议层 G1–G4 已由 `backend/tests/test_techlab/test_key_lifecycle.py` 验证。
本脚本为 Mac 真机判定标准；未执行前状态保持 DEVICE_PENDING。

## 前置

- iPhone Pro 真机（模拟器无 Secure Enclave，不构成验收）
- Xcode 连接真机，WorkCam iOS 工程（PR #6 `dev/mac-evidence-m003` 分支）
- Windows 后端已启动（`tools\start_backend.ps1`）且 `integration_check.py` 13/13

## 验收步骤（每步必须记录实际输出）

| # | 操作 | 通过标准 | 对应保证 |
|---|---|---|---|
| 1 | 拍照 → 证据目录生成 `signature.der` + `record.json` | 文件存在且 `record.status` 为 Signed | — |
| 2 | 提交后端 `/verify`（带 photo） | `evidence_status=cryptographically_valid` | 协议兼容 |
| 3 | 记录当前公钥 `publicKeyID`，卸载 App → 重装 → 拍照 | 新 `publicKeyID` ≠ 旧值（密钥确实重生成） | G1 前提 |
| 4 | 用旧证据的 `record.json` 调后端 `/verify` | 仍 `cryptographically_valid`（旧公钥存档生效） | **G1** |
| 5 | 重装后用新 device_id 注册新公钥，提交旧证据不变 | 新旧两条证据各自通过，互不干扰 | **G4** |
| 6 | 同一 device_id 在后端用另一把公钥注册 | `DEVICE_ID_CONFLICT`（显式拒绝，不覆盖） | **G2** |
| 7 | 服务器 key_id 轮换后（Windows 侧 `TimeAnchorService` 新 key_id + trusted_keys 归档）重验旧锚点回执 | `signature_valid=true` | **G3** |
| 8 | Keychain 中删除 `WorkCam.M003/signing-key` 条目（模拟清理）→ 拍照 | 生成新密钥；步骤 4–6 重跑仍通过 | G1+G4 |

## 结果回填

数据存 `tools/tech-lab/datasets/t01-se-<device>.md`，完成后将 acceptance-matrix 中 T01 从 DEVICE_PENDING 改为 DEVICE_VERIFIED（或 LIMITED + 原因）。

## 明确边界（不得宣称）

- Secure Enclave 签名 ≠ App Attest 设备认证（`device_attestation_status` 仍为 `unknown`）。
- 本脚本验证密钥生命周期协议，不验证 SE 硬件抗物理攻击。
