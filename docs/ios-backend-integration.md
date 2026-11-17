# WorkCam iOS-Backend 联调指南

## 概述

本文档为 Mac 端 iOS 开发提供与 WorkCam 后端联调的完整指南。包含字段定义、字节格式、代码示例、测试步骤和故障排查。

**目标**：确保 iOS 端生成的签名清单能够被 Python 后端正确验证。

---

## 1. 签名清单字段定义

### 1.1 必需字段

iOS 端需要生成以下字段：

| 字段名 | 类型 | 格式要求 | 示例 |
|--------|------|----------|------|
| `version` | string | `"1.0"` 或 `"1.1"` | `"1.1"` |
| `record_id` | string | UUID v4，无换行符 | `"a1b2c3d4-e5f6-7890-abcd-ef1234567890"` |
| `sequence_number` | integer | >= 0 | `0` |
| `previous_record_hash` | string/null | 64位十六进制或null | `null` 或 `"abc123..."` |
| `photo_hash` | string | 64位小写十六进制 | `"e3b0c44298fc1c14..."` |
| `capture_timestamp` | string | ISO 8601，无换行符 | `"2026-11-17T10:00:00+00:00"` |
| `device_reported_time` | string | ISO 8601，无换行符 | `"2026-11-17T10:00:00+00:00"` |
| `device_id` | string | 无换行符，<=256字符 | `"iphone-pro-001"` |
| `location` | object/null | 见下方格式 | `null` 或 `{"lat":39.9,"lon":116.4,...}` |
| `public_key` | string | Base64编码的65字节公钥 | `"BKPE8J4NAqS79PcCCWucQk+j7XAg..."` |
| `signature` | string | Base64编码的DER签名 | `"MEUCIQDx..."` |

### 1.2 Location 对象格式

```swift
struct LocationData: Codable {
    let latitude: Double      // -90.0 到 90.0
    let longitude: Double     // -180.0 到 180.0
    let horizontal_accuracy: Double  // > 0
    let altitude: Double?     // 可选，米
}
```

**v1.1 浮点数格式**：
- 必须有限（不能是 NaN 或 Infinity）
- 使用 `.10g` 精度格式化
- 确保包含小数点

---

## 2. 规范化签名数据格式

### 2.1 签名数据结构

iOS 端需要按照以下顺序构建签名字节：

```
WorkCam-Sig-v1.1\n
version=1.1\n
record_id={record_id}\n
sequence_number={sequence_number}\n
previous_record_hash={previous_record_hash or "none"}\n
photo_hash={photo_hash}\n
capture_timestamp={capture_timestamp}\ndevice_reported_time={device_reported_time}\n
device_id={device_id}\n
location={location_string}\n
```

### 2.2 Location 字符串格式

- 如果 location 为 null: `location=none`
- 如果 location 存在: `location={lat},{lon},{accuracy},{altitude}`
  - altitude 为 null 时: `location={lat},{lon},{accuracy},none`

### 2.3 Swift 实现示例

```swift
import Foundation
import CryptoKit

func buildCanonicalPayload(manifest: SignatureManifest) -> Data {
    var lines: [String] = []
    
    // 域分隔符
    lines.append("WorkCam-Sig-v1.1")
    
    // 按固定顺序添加字段
    lines.append("version=\(manifest.version)")
    lines.append("record_id=\(manifest.record_id)")
    lines.append("sequence_number=\(manifest.sequenceNumber)")
    lines.append("previous_record_hash=\(manifest.previousRecordHash ?? "none")")
    lines.append("photo_hash=\(manifest.photoHash)")
    lines.append("capture_timestamp=\(manifest.captureTimestamp)")
    lines.append("device_reported_time=\(manifest.deviceReportedTime)")
    lines.append("device_id=\(manifest.deviceId)")
    
    // Location 格式化
    if let loc = manifest.location {
        let alt = loc.altitude.map { formatFloat($0) } ?? "none"
        lines.append("location=\(formatFloat(loc.latitude)),\(formatFloat(loc.longitude)),\(formatFloat(loc.horizontalAccuracy)),\(alt)")
    } else {
        lines.append("location=none")
    }
    
    // 拼接并编码为 UTF-8
    let text = lines.joined(separator: "\n") + "\n"
    return text.data(using: .utf8)!
}

func formatFloat(_ value: Double) -> String {
    // 确保有限值
    guard value.isFinite else {
        fatalError("Float value must be finite")
    }
    
    // 使用 .10g 精度
    let formatted = String(format: "%.10g", value)
    
    // 确保包含小数点
    if !formatted.contains(".") && !formatted.lowercased().contains("e") {
        return formatted + ".0"
    }
    
    return formatted
}
```

---

## 3. P-256 签名兼容要求

### 3.1 密钥格式

**公钥格式**：
- 未压缩点格式：`0x04 || x || y`（65字节）
- Base64 编码（标准，带填充）

**Swift 实现**：
```swift
import CryptoKit

// 生成密钥对（Secure Enclave）
let privateKey = try P256.Signing.PrivateKey()
let publicKey = privateKey.publicKey

// 获取未压缩公钥（65字节）
let publicKeyBytes = publicKey.x963Representation  // 0x04 || x || y

// Base64 编码
let publicKeyBase64 = publicKeyBytes.base64EncodedString()
```

**Python 验证**：
```python
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64

# 解码公钥
pub_bytes = base64.b64decode(public_key_base64)
assert len(pub_bytes) == 65
assert pub_bytes[0] == 0x04

# 加载公钥
public_key = ec.EllipticCurvePublicKey.from_encoded_point(
    ec.SECP256R1(), 
    pub_bytes
)
```

### 3.2 签名格式

**签名格式**：
- DER 编码（RFC 3279）
- Base64 编码（标准，带填充）

**Swift 实现**：
```swift
import CryptoKit

// 签名
let payload = buildCanonicalPayload(manifest: manifest)
let signature = try privateKey.signature(for: payload)

// CryptoKit 返回的是 raw 格式 (r || s)，需要转换为 DER
let derSignature = signature.derRepresentation

// Base64 编码
let signatureBase64 = derSignature.base64EncodedString()
```

**Python 验证**：
```python
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
import base64

# 解码签名
sig_bytes = base64.b64decode(signature_base64)

# 验证 DER 结构
assert sig_bytes[0] == 0x30  # SEQUENCE tag
# ... 更严格的 DER 验证见后端代码

# 验证签名
public_key.verify(
    sig_bytes,
    payload,
    ec.ECDSA(hashes.SHA256())
)
```

### 3.3 关键兼容性注意事项

1. **CryptoKit 签名格式**：
   - `P256.Signing.ECDSASignature` 默认返回 raw 格式 (r || s)
   - 必须使用 `.derRepresentation` 获取 DER 格式
   - 或者手动转换：`ECDSASignature(rawRepresentation:).derRepresentation`

2. **DER 结构要求**：
   - 必须以 `0x30` 开头（SEQUENCE tag）
   - 必须包含两个 INTEGER（r 和 s）
   - 不能有尾部数据
   - 长度必须匹配

3. **Base64 编码**：
   - 使用标准 Base64（不是 URL-safe）
   - 包含填充（`=`）
   - 不包含换行符

---

## 4. 照片哈希计算

### 4.1 计算方法

```swift
import CryptoKit

func computePhotoHash(photoData: Data) -> String {
    let hash = SHA256.hash(data: photoData)
    return hash.map { String(format: "%02x", $0) }.joined()
}
```

### 4.2 Python 验证

```python
import hashlib

def verify_photo_hash(photo_bytes: bytes, expected_hash: str) -> bool:
    actual_hash = hashlib.sha256(photo_bytes).hexdigest()
    return actual_hash == expected_hash
```

### 4.3 注意事项

- 必须使用原始照片字节（未压缩、未修改）
- 哈希值为 64 位小写十六进制字符串
- 不能包含换行符或空格

---

## 5. 时间锚点 v2 提交

### 5.1 提交数据结构

```swift
struct TimeAnchorRequest: Codable {
    let batch_id: String           // 批次标识符
    let chain_head_hash: String    // 链头哈希（64位十六进制）
    let chain_tail_hash: String    // 链尾哈希（64位十六进制）
    let record_count: Int          // 记录数量
    let device_reported_start_time: String  // ISO 8601
    let device_reported_end_time: String    // ISO 8601
}
```

### 5.2 提交示例

```swift
let request = TimeAnchorRequest(
    batch_id: "batch-20261117-001",
    chain_head_hash: "abc123...",  // 第一条记录的哈希
    chain_tail_hash: "def456...",  // 最后一条记录的哈希
    record_count: 10,
    device_reported_start_time: "2026-11-17T09:00:00+00:00",
    device_reported_end_time: "2026-11-17T10:00:00+00:00"
)

// POST /time-anchor/anchor
let responseData = try await apiClient.post("/time-anchor/anchor", body: request)
let response = try JSONDecoder().decode(TimeAnchorResponse.self, from: responseData)
```

### 5.3 响应结构

```swift
struct TimeAnchorResponse: Codable {
    let anchor_id: String
    let key_id: String
    let batch_id: String
    let server_received_at: String
    let challenge: String
    let server_signature: String
    let chain_head_hash: String
    let chain_tail_hash: String
    let record_count: Int
    let verification_status: String  // "digest_anchored", "chain_verified", etc.
}
```

### 5.4 回执验证

```swift
// 保存回执用于后续验证
let receipt = TimeAnchorReceipt(
    anchor_id: response.anchor_id,
    key_id: response.key_id,
    batch_id: response.batch_id,
    server_received_at: response.server_received_at,
    challenge: response.challenge,
    server_signature: response.server_signature,
    chain_head_hash: response.chain_head_hash,
    chain_tail_hash: response.chain_tail_hash,
    record_count: response.record_count,
    verification_status: response.verification_status,
    device_reported_start_time: request.device_reported_start_time,
    device_reported_end_time: request.device_reported_end_time,
    stored_at: response.server_received_at
)

// 保存到本地数据库
try saveReceipt(receipt)
```

---

## 6. 完整联调测试步骤

### 6.1 环境准备

**后端**：
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**iOS**：
- Xcode 15+
- iOS 17+
- iPhone Pro（带 Secure Enclave）

### 6.2 测试流程

#### 步骤 1：生成测试密钥

```swift
// iOS 端
let privateKey = try P256.Signing.PrivateKey()
let publicKey = privateKey.publicKey

// 注册公钥到后端
let pubKeyBase64 = publicKey.x963Representation.base64EncodedString()
try await apiClient.post("/register-key", params: [
    "public_key_b64": pubKeyBase64,
    "device_id": "iphone-pro-001",
    "allow_test_registration": "true"
])
```

#### 步骤 2：拍照并生成清单

```swift
// 拍照
let photoData = try await capturePhoto()

// 计算哈希
let photoHash = computePhotoHash(photoData: photoData)

// 生成清单
let manifest = SignatureManifest(
    version: "1.1",
    record_id: UUID().uuidString,
    sequenceNumber: 0,
    previousRecordHash: nil,
    photoHash: photoHash,
    captureTimestamp: ISO8601DateFormatter().string(from: Date()),
    deviceReportedTime: ISO8601DateFormatter().string(from: Date()),
    deviceId: "iphone-pro-001",
    location: nil,
    publicKey: publicKey.x963Representation.base64EncodedString(),
    signature: ""  // 待填充
)

// 构建签名数据
let payload = buildCanonicalPayload(manifest: manifest)

// 签名
let signature = try privateKey.signature(for: payload)
manifest.signature = signature.derRepresentation.base64EncodedString()
```

#### 步骤 3：提交到后端验证

```swift
// 提交清单和照片
let verifyRequest = VerifyRequest(
    manifest: manifest,
    photo_base64: photoData.base64EncodedString()
)

let response = try await apiClient.post("/verify", body: verifyRequest)

// 检查响应
assert(response.evidence_status == "cryptographically_valid")
assert(response.cryptographic_signature_valid == true)
assert(response.photo_hash_valid == true)
assert(response.key_registration_status == "registered")
```

#### 步骤 4：验证时间锚点

```swift
// 提交时间锚点
let anchorRequest = TimeAnchorRequest(
    batch_id: "test-batch-\(UUID().uuidString)",
    chain_head_hash: computeRecordHash(manifest: manifest),
    chain_tail_hash: computeRecordHash(manifest: manifest),
    record_count: 1,
    device_reported_start_time: manifest.captureTimestamp,
    device_reported_end_time: manifest.captureTimestamp
)

let anchorResponse = try await apiClient.post("/time-anchor/anchor", body: anchorRequest)

// 验证回执
let verification = try await apiClient.post("/time-anchor/verify", body: anchorResponse)
assert(verification.valid == true)
assert(verification.signature_valid == true)
```

### 6.3 预期结果

**成功响应**：
```json
{
  "evidence_status": "cryptographically_valid",
  "cryptographic_signature_valid": true,
  "photo_hash_valid": true,
  "key_registration_status": "registered",
  "device_identity_consistent": true,
  "device_attestation_status": "unknown",
  "time_trust_level": "device_reported",
  "summary": "Cryptographically valid: signature and photo hash verified, key registered. BUT device attestation is unknown.",
  "details": [
    {"check": "structure", "status": "passed", "message": "Manifest structure valid"},
    {"check": "photo_hash", "status": "passed", "message": "SHA-256 match"},
    {"check": "ecdsa_signature", "status": "passed", "message": "Signature valid"}
  ],
  "warnings": [
    "Device attestation (App Attest) is not implemented. device_attestation_status=unknown.",
    "Timestamp is device-reported only. time_trust_level=device_reported."
  ]
}
```

---

## 7. 常见失败场景及排查

### 7.1 签名验证失败

**症状**：`cryptographic_signature_valid = false`

**排查步骤**：

1. **检查规范化数据**：
   ```swift
   let payload = buildCanonicalPayload(manifest: manifest)
   print("Payload hex:", payload.map { String(format: "%02x", $0) }.joined())
   ```
   
   对比 Python 端：
   ```python
   payload = canonical_payload(manifest)
   print("Payload hex:", payload.hex())
   ```
   
   **必须完全一致**。

2. **检查公钥格式**：
   - 必须是 65 字节
   - 必须以 `0x04` 开头
   - Base64 解码后检查长度

3. **检查签名格式**：
   - 必须是 DER 格式
   - 必须以 `0x30` 开头
   - 使用 `signature.derRepresentation`，不是 `signature.rawRepresentation`

4. **检查字段值**：
   - 不能包含换行符（`\n` 或 `\r`）
   - 不能包含等号（`=`）
   - 时间戳必须是 ISO 8601 格式

### 7.2 照片哈希不匹配

**症状**：`photo_hash_valid = false`

**排查步骤**：

1. **检查照片字节**：
   ```swift
   print("Photo size:", photoData.count)
   print("Photo hash:", computePhotoHash(photoData: photoData))
   ```

2. **检查 Base64 编码**：
   ```swift
   let base64 = photoData.base64EncodedString()
   print("Base64 length:", base64.count)
   ```

3. **确认使用原始照片**：
   - 不能是压缩后的 JPEG
   - 不能是修改过的数据
   - 必须是相机原始输出

### 7.3 公钥未注册

**症状**：`key_registration_status = "unregistered"`

**排查步骤**：

1. **检查注册请求**：
   ```bash
   curl -X POST "http://localhost:8000/register-key" \
     -d "public_key_b64=..." \
     -d "device_id=iphone-pro-001" \
     -d "allow_test_registration=true"
   ```

2. **检查公钥一致性**：
   - 注册时使用的公钥
   - 签名时使用的公钥
   - 必须完全相同（Base64 字符串相同）

3. **检查 device_id 一致性**：
   - 注册时的 device_id
   - 清单中的 device_id
   - 必须完全相同

### 7.4 设备身份不匹配

**症状**：`device_identity_consistent = false`

**排查步骤**：

1. **检查注册的 device_id**：
   ```bash
   curl "http://localhost:8000/check-record/..." 
   ```

2. **检查清单中的 device_id**：
   ```swift
   print("Manifest device_id:", manifest.deviceId)
   ```

3. **确保一致**：
   - 注册时的 device_id
   - 清单中的 device_id
   - 必须完全相同（区分大小写）

### 7.5 时间锚点验证失败

**症状**：`verification.valid = false`

**排查步骤**：

1. **检查 key_id**：
   - 回执中的 key_id 必须是已注册的
   - 检查服务器公钥端点：`GET /time-anchor/public-key`

2. **检查签名字段**：
   - 不能修改任何已签名字段
   - verification_status 包含在签名中，不能篡改

3. **检查服务器存储**：
   - 回执必须在服务器存储中存在
   - 使用 `check_storage=false` 进行离线验证

### 7.6 协议版本不匹配

**症状**：`evidence_status = "unverified"`

**排查步骤**：

1. **检查 version 字段**：
   ```swift
   print("Manifest version:", manifest.version)
   ```

2. **确认支持的版本**：
   - 当前支持：`"1.0"` 和 `"1.1"`
   - 推荐使用：`"1.1"`（改进的浮点数格式）

3. **检查域分隔符**：
   - v1.0: `WorkCam-Sig-v1`
   - v1.1: `WorkCam-Sig-v1.1`

---

## 8. 调试工具

### 8.1 后端日志

```bash
# 启动时启用详细日志
uvicorn app.main:app --reload --log-level debug
```

### 8.2 请求/响应记录

```swift
// iOS 端记录请求
print("Request:", String(data: try JSONEncoder().encode(manifest), encoding: .utf8)!)

// 记录响应
print("Response:", String(data: responseData, encoding: .utf8)!)
```

### 8.3 十六进制对比

```swift
// Swift
let payloadHex = payload.map { String(format: "%02x", $0) }.joined()
print("Swift payload:", payloadHex)
```

```python
# Python
print("Python payload:", payload.hex())
```

**对比两个十六进制字符串，必须完全一致**。

---

## 9. 测试向量

### 9.1 固定测试向量

后端提供了固定测试向量：`test-vectors/fixed-vectors.json`

包含：
- 固定公钥（Base64）
- 固定照片字节（十六进制）
- 固定照片哈希
- 固定规范化数据（十六进制）
- 固定签名（Base64）

iOS 端可以使用这些向量验证实现的正确性。

### 9.2 验证测试向量

```swift
// 加载测试向量
let vectors = try JSONDecoder().decode(TestVectors.self, from: testData)

// 验证公钥
let pubKeyData = Data(base64Encoded: vectors.public_key.base64)!
assert(pubKeyData.count == 65)
assert(pubKeyData[0] == 0x04)

// 验证照片哈希
let photoData = Data(hexString: vectors.photo.bytes_hex)!
let photoHash = computePhotoHash(photoData: photoData)
assert(photoHash == vectors.photo.sha256_hex)

// 验证规范化数据
let payload = buildCanonicalPayload(manifest: testManifest)
let payloadHex = payload.map { String(format: "%02x", $0) }.joined()
assert(payloadHex == vectors.canonical_payload.hex)

// 验证签名
let publicKey = try P256.Signing.PublicKey(x963Representation: pubKeyData)
let signature = try P256.Signing.ECDSASignature(derRepresentation: Data(base64Encoded: vectors.signature.base64)!)
let isValid = publicKey.isValidSignature(signature, for: payload)
assert(isValid)
```

---

## 10. 下一步

完成联调后，下一步是：

1. **实现 Secure Enclave 签名**
   - 使用 `SecKey` API 生成 Secure Enclave 密钥
   - 确保证书链正确

2. **实现离线哈希链**
   - 本地存储记录
   - 计算链哈希
   - 支持离线拍摄

3. **实现时间锚点客户端**
   - 批量提交离线记录
   - 保存服务器回执
   - 离线验证回执

4. **实现 App Attest**（可选）
   - 提升设备信任等级
   - 达到 `FULLY_ATTESTED` 状态

---

## 附录 A：完整代码示例

### A.1 SignatureManifest 结构

```swift
struct SignatureManifest: Codable {
    let version: String
    let record_id: String
    let sequence_number: Int
    let previous_record_hash: String?
    let photo_hash: String
    let capture_timestamp: String
    let device_reported_time: String
    let device_id: String
    let location: LocationData?
    let public_key: String
    let signature: String
}

struct LocationData: Codable {
    let latitude: Double
    let longitude: Double
    let horizontal_accuracy: Double
    let altitude: Double?
}
```

### A.2 完整签名流程

```swift
import Foundation
import CryptoKit

class WorkCamSigner {
    private let privateKey: P256.Signing.PrivateKey
    private let deviceId: String
    
    init(deviceId: String) throws {
        self.deviceId = deviceId
        self.privateKey = try P256.Signing.PrivateKey()
    }
    
    func signPhoto(photoData: Data, sequenceNumber: Int, previousHash: String?) throws -> SignatureManifest {
        // 1. 计算照片哈希
        let photoHash = computePhotoHash(photoData: photoData)
        
        // 2. 生成时间戳
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let timestamp = formatter.string(from: Date())
        
        // 3. 构建清单
        var manifest = SignatureManifest(
            version: "1.1",
            record_id: UUID().uuidString,
            sequence_number: sequenceNumber,
            previous_record_hash: previousHash,
            photo_hash: photoHash,
            capture_timestamp: timestamp,
            device_reported_time: timestamp,
            device_id: deviceId,
            location: nil,
            public_key: privateKey.publicKey.x963Representation.base64EncodedString(),
            signature: ""
        )
        
        // 4. 构建签名数据
        let payload = buildCanonicalPayload(manifest: manifest)
        
        // 5. 签名
        let signature = try privateKey.signature(for: payload)
        manifest.signature = signature.derRepresentation.base64EncodedString()
        
        return manifest
    }
    
    private func computePhotoHash(photoData: Data) -> String {
        let hash = SHA256.hash(data: photoData)
        return hash.map { String(format: "%02x", $0) }.joined()
    }
    
    private func buildCanonicalPayload(manifest: SignatureManifest) -> Data {
        var lines: [String] = []
        
        lines.append("WorkCam-Sig-v1.1")
        lines.append("version=\(manifest.version)")
        lines.append("record_id=\(manifest.record_id)")
        lines.append("sequence_number=\(manifest.sequence_number)")
        lines.append("previous_record_hash=\(manifest.previous_record_hash ?? "none")")
        lines.append("photo_hash=\(manifest.photo_hash)")
        lines.append("capture_timestamp=\(manifest.capture_timestamp)")
        lines.append("device_reported_time=\(manifest.device_reported_time)")
        lines.append("device_id=\(manifest.device_id)")
        
        if let loc = manifest.location {
            let alt = loc.altitude.map { formatFloat($0) } ?? "none"
            lines.append("location=\(formatFloat(loc.latitude)),\(formatFloat(loc.longitude)),\(formatFloat(loc.horizontal_accuracy)),\(alt)")
        } else {
            lines.append("location=none")
        }
        
        let text = lines.joined(separator: "\n") + "\n"
        return text.data(using: .utf8)!
    }
    
    private func formatFloat(_ value: Double) -> String {
        guard value.isFinite else {
            fatalError("Float value must be finite")
        }
        let formatted = String(format: "%.10g", value)
        if !formatted.contains(".") && !formatted.lowercased().contains("e") {
            return formatted + ".0"
        }
        return formatted
    }
}
```

---

## 附录 B：API 端点参考

### B.1 验证端点

```
POST /verify
Content-Type: application/json

{
  "manifest": { ... },
  "photo_base64": "..."
}
```

### B.2 公钥注册端点

```
POST /register-key
Content-Type: application/x-www-form-urlencoded

public_key_b64=...&device_id=...&allow_test_registration=true
```

### B.3 时间锚点端点

```
POST /time-anchor/anchor
Content-Type: application/json

{
  "batch_id": "...",
  "chain_head_hash": "...",
  "chain_tail_hash": "...",
  "record_count": 10,
  "device_reported_start_time": "...",
  "device_reported_end_time": "..."
}
```

### B.4 时间锚点验证端点

```
POST /time-anchor/verify
Content-Type: application/json

{ ... receipt ... }
```

---

## 附录 C：错误代码

| HTTP 状态码 | 含义 | 排查方向 |
|------------|------|----------|
| 400 | 请求格式错误 | 检查 JSON 结构、字段类型 |
| 404 | 资源未找到 | 检查 anchor_id、record_id |
| 409 | 冲突 | 检查 batch_id 是否重复 |
| 500 | 服务器错误 | 检查后端日志 |

---

**文档版本**：1.0  
**最后更新**：2026-11-17  
**适用协议版本**：签名 v1.1，时间锚点 v2
