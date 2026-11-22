# ADR-0001：Secure Enclave 密钥生命周期按"失效即安全边界"设计

状态：已接受 · 日期：2026-11-19 · 关联：T01

## 背景

Secure Enclave 密钥不可导出（私钥永不出硬件）。用户会卸载重装 App、换机、恢复备份、清 Keychain。必须先定义生命周期协议，Mac 真机实测才有判定标准。

## 研究（资料来源）

- Apple CryptoKit/SecureEnclave 文档：`SecureEnclave.P256.Signing.PrivateKey`，`dataRepresentation` 为设备绑定引用，**不可跨设备恢复**；卸载 App 后 Keychain 条目删除（PR #6 使用 `kSecClassGenericPassword` + `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` 存储）。
- App Attest（未来接入）：keyId 与设备绑定，同样不迁移。

## 决策

1. **密钥 = 设备身份，不做跨设备迁移**。换机/重装 = 新设备注册新公钥。
2. **旧证据的可信性不依赖设备持有私钥**：验证用注册表中的公钥存档 + 时间锚点，任何第三方可离线复验。这使密钥失效**不损失**历史证据。
3. 卸载导致的密钥失效是**安全特性**（防提取），不是故障。
4. 协议层必须保证（已用 `backend/techlab/key_lifecycle.py` 建模测试）：
   - 密钥失效后旧签名仍可验证（公钥存档路径）；
   - 同一 device_id 绑新公钥时必须显式冲突（`DEVICE_ID_CONFLICT`，W002-R3 已实现）；
   - 新密钥注册后，旧密钥签的证据仍走旧公钥验证（key_id 轮换，W003-R3 已实现）。

## 后果

- 产品上"更换设备"是显式流程，不承诺无缝迁移。
- 真机验收脚本（`experiments/t01-se-acceptance.md` 见 T01 方案）按此四条判定。
