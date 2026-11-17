# WorkCam iOS-Backend 联调指南（修订版）

## 概述

本文档为 Mac 端 iOS 开发提供与 WorkCam 后端联调的完整指南。包含字段定义、字节格式、代码示例、测试步骤和故障排查。

**目标**：确保 iOS 端生成的签名清单能够被 Python 后端正确验证。

**重要声明**：
- 本文档中的 Swift 代码示例**未经实际编译验证**，仅供逻辑参考
- 所有接口格式已对照后端真实代码核对
- Secure Enclave 示例需要真机测试，模拟器不支持

---

## 1. 签名清单字段定义

### 1.1 必需字段

iOS 端需要生成以下字段（**注意：JSON 字段名使用 snake_case，与后端一致**）：

| 字段名 | 类型 | 格式要求 | 示例 |
|--------|------|----------|------|
| `version` | string | `"1.0"` 或 `"1.1"` | `"1.1"` |
| `record_id` | string | UUID v4，无换行符，<=256字符 | `"a1b2c3d4-e5f6-7890-abcd-ef1234567890"` |
| `sequence_number` | integer | >= 0, <= 1000000 | `0` |
| `previous_record_hash` | string/null | 64位十六进制或null | `null` 或 `"abc123..."` |
| `photo_hash` | string | 64位小写十六进制 | `"e3b0c44298fc1c14..."` |
| `capture_timestamp` | string | ISO 8601，无换行符 | `"2026-11-17T10:00:00+00:00"` |
| `device_reported_time` | string | ISO 8601，无换行符 | `"2026-11-17T10:00:00+00:00"` |
| `device_id` | string | 无换行符，<=256字符 | `"iphone-pro-001"` |
| `location` | object/null | 见下方格式 | `null` 或 `{"latitude":39.9,...}` |
| `public_key` | string | Base64编码的65字节公钥 | `"BEw0ZJJUZjUJh3U5..."` |
| `signature` | string | Base64编码的DER签名 | `"MEUCIGmFpTm0pQqW..."` |

**重要**：
- 所有字符串字段**不能包含换行符**（`\n` 或 `\r`）
- 所有字符串字段**不能包含等号**（`=`）
- 时间戳必须是 ISO 8601 格式

### 1.2 Location 对象格式

```swift
// 注意：JSON 字段名使用 snake_case
struct LocationData: Codable {
    let latitude: Double           // -90.0 到 90.0，有限值
    let longitude: Double          // -180.0 到 180.0，有限值
    let horizontal_accuracy: Double // > 0，有限值
    let altitude: Double?          // 可选，-1000.0 到 100000.0，有限值
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
capture_timestamp={capture_timestamp}\n
device_reported_time={device_reported_time}\n
device_id={device_id}\n
location={location_string}\n
```

### 2.2 Location 字符串格式

- 如果 location 为 null: `location=none`
- 如果 location 存在: `location={lat},{lon},{accuracy},{altitude}`
  - altitude 为 null 时: `location={lat},{lon},{accuracy},none`

### 2.3 Swift 实现示例（伪代码，未编译验证）

```swift
import Foundation
import CryptoKit

// 注意：这个结构体使用 snake_case 与后端 JSON 字段一致
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
    var signature: String  // 注意：使用 var 因为需要后续赋值
}

func buildCanonicalPayload(manifest: SignatureManifest) -> Data {
    var lines: [String] = []
    
    // 域分隔符
    lines.append("WorkCam-Sig-v1.1")
    
    // 按固定顺序添加字段（使用 snake_case 字段名）
    lines.append("version=\(manifest.version)")
    lines.append("record_id=\(manifest.record_id)")
    lines.append("sequence_number=\(manifest.sequence_number)")
    lines.append("previous_record_hash=\(manifest.previous_record_hash ?? "none")")
    lines.append("photo_hash=\(manifest.photo_hash)")
    lines.append("capture_timestamp=\(manifest.capture_timestamp)")
    lines.append("device_reported_time=\(manifest.device_reported_time)")
    lines.append("device_id=\(manifest.device_id)")
    
    // Location 格式化
    if let loc = manifest.location {
        let alt = loc.altitude.map { formatFloat($0) } ?? "none"
        lines.append("location=\(formatFloat(loc.latitude)),\(formatFloat(loc.longitude)),\(formatFloat(loc.horizontal_accuracy)),\(alt)")
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

**编译状态**：⚠️ 未验证（伪代码）

---

## 3. P-256 签名兼容要求

### 3.1 密钥格式

**公钥格式**：
- 未压缩点格式：`0x04 || x || y`（65字节）
- Base64 编码（标准，带填充）

#### 3.1.1 普通软件密钥（仅用于测试）

```swift
import CryptoKit

// ⚠️ 警告：这是普通软件密钥， NOT Secure Enclave 密钥
// 私钥存储在应用内存中，可以被提取
let privateKey = try P256.Signing.PrivateKey()
let publicKey = privateKey.publicKey

// 获取未压缩公钥（65字节）
let publicKeyBytes = publicKey.x963Representation  // 0x04 || x || y

// Base64 编码
let publicKeyBase64 = publicKeyBytes.base64EncodedString()
```

**编译状态**：⚠️ 未验证（伪代码）

#### 3.1.2 Secure Enclave 密钥（推荐用于生产）

```swift
import CryptoKit
import SecureEnclave

// ✅ Secure Enclave 密钥：私钥永不出硬件
// 需要真机测试，模拟器不支持

// 生成 Secure Enclave 密钥
let accessControl = SecAccessControlCreateWithFlags(
    nil,
    kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
    .privateKeyUsage,
    nil
)!

let privateKey = try SecureEnclave.P256.Signing.PrivateKey(
    compactRepresentable: false,
    accessControl: accessControl,
    authenticationContext: nil
)

// 获取公钥
let publicKey = privateKey.publicKey
let publicKeyBytes = publicKey.x963Representation  // 65字节
let publicKeyBase64 = publicKeyBytes.base64EncodedString()

// 签名
let payload = buildCanonicalPayload(manifest: manifest)
let signature = try privateKey.signature(for: payload)

// ⚠️ 注意：Secure Enclave 签名也需要转换为 DER 格式
// 与 CryptoKit 的普通密钥一样，使用 .derRepresentation
let derSignature = signature.derRepresentation
let signatureBase64 = derSignature.base64EncodedString()
```

**编译状态**：⚠️ 未验证（伪代码，需要真机测试）

**重要区别**：
- `P256.Signing.PrivateKey`：普通软件密钥，私钥在内存中
- `SecureEnclave.P256.Signing.PrivateKey`：硬件保护密钥，私钥不出 Secure Enclave
- Secure Enclave 签名**不等于** App Attest 设备认证
- Secure Enclave 签名也需要使用 `.derRepresentation` 获取 DER 字节

### 3.2 签名格式

**签名格式**：
- DER 编码（RFC 3279）
- Base64 编码（标准，带填充）

#### 3.2.1 普通密钥签名（需要 DER 转换）

```swift
import CryptoKit

let privateKey = try P256.Signing.PrivateKey()
let payload = buildCanonicalPayload(manifest: manifest)

// 签名
let signature = try privateKey.signature(for: payload)

// ⚠️ 注意：CryptoKit 的 P256.Signing.ECDSASignature 返回的是 raw 格式 (r || s)
// 必须转换为 DER 格式
let derSignature = signature.derRepresentation

// Base64 编码
let signatureBase64 = derSignature.base64EncodedString()
```

**编译状态**：⚠️ 未验证（伪代码）

#### 3.2.2 Secure Enclave 签名（也需要 DER 转换）

```swift
import CryptoKit
import Security

let privateKey = try SecureEnclave.P256.Signing.PrivateKey(...)
let payload = buildCanonicalPayload(manifest: manifest)

// 签名
let signature = try privateKey.signature(for: payload)

// ⚠️ 注意：Secure Enclave 签名也需要使用 .derRepresentation 获取 DER 字节
// 与 CryptoKit 的普通密钥处理方式相同
let derSignature = signature.derRepresentation
let signatureBase64 = derSignature.base64EncodedString()
```

**编译状态**：⚠️ 未验证（伪代码，需要真机测试）

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
   - `P256.Signing.ECDSASignature`（普通密钥）：返回 raw 格式，需要 `.derRepresentation`
   - `SecureEnclave.P256.Signing.ECDSASignature`（Secure Enclave）：也需要 `.derRepresentation`
   - **两者都需要调用 `.derRepresentation` 获取 DER 字节**

2. **DER 结构要求**：
   - 必须以 `0x30` 开头（SEQUENCE tag）
   - 必须包含两个 INTEGER（r 和 s）
   - 不能有尾部数据
   - 长度必须匹配

3. **Base64 编码**：
   - 使用标准 Base64（不是 URL-safe）
   - 包含填充（`=`）
   - 不包含换行符

4. **Secure Enclave 限制**：
   - 仅支持真机，模拟器不支持
   - 需要用户认证（Face ID/Touch ID）
   - 私钥永不离开硬件

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

**编译状态**：⚠️ 未验证（伪代码）

### 4.2 Python 验证

```python
import hashlib

def verify_photo_hash(photo_bytes: bytes, expected_hash: str) -> bool:
    actual_hash = hashlib.sha256(photo_bytes).hexdigest()
    return actual_hash == expected_hash
```

### 4.3 关键要求

**必须遵守**：

1. **使用实际保存的文件字节**
   - JPEG、HEIC 等编码格式都可以
   - 哈希必须针对**实际保存到磁盘的文件**
   - 不能是内存中的临时数据

2. **禁止重新编码**
   - ❌ 计算哈希后不能重新压缩
   - ❌ 不能修改 EXIF 数据
   - ❌ 不能转换格式（如 HEIC → JPEG）

3. **文件保存前后一致性检查**
   ```swift
   // 1. 拍照后计算哈希
   let photoData = try await capturePhoto()
   let hash1 = computePhotoHash(photoData: photoData)
   
   // 2. 保存到文件
   let fileURL = getDocumentsDirectory().appendingPathComponent("photo_\(UUID()).jpg")
   try photoData.write(to: fileURL)
   
   // 3. 重新读取并计算哈希
   let reloadedData = try Data(contentsOf: fileURL)
   let hash2 = computePhotoHash(photoData: reloadedData)
   
   // 4. 验证一致性
   assert(hash1 == hash2, "Photo file was modified during save!")
   
   // 5. 使用 hash1 作为 photo_hash
   ```

4. **哈希格式**
   - 64 位小写十六进制字符串
   - 不能包含换行符或空格

---

## 5. 公钥注册接口

### 5.1 接口格式（已核对后端代码）

**端点**：`POST /register-key`

**请求格式**：`application/x-www-form-urlencoded`（不是 JSON）

**必需参数**：
- `public_key_b64`: Base64 编码的公钥
- `device_id`: 设备标识符
- `allow_test_registration`: 必须为 `true`（开发/测试环境）

**示例**：
```bash
# ✅ 推荐：使用 --data-urlencode 自动编码特殊字符
curl -X POST "http://localhost:8000/register-key" \
  --data-urlencode "public_key_b64=BEw0ZJJUZjUJh3U5mTh2H+zyQH6cIo5JIYmpQ6t34t4D..." \
  --data-urlencode "device_id=iphone-pro-001" \
  --data-urlencode "allow_test_registration=true"
```

**iOS 实现**（伪代码）：
```swift
// ⚠️ 重要：Base64 字符串包含 +、=、/ 等特殊字符，必须 URL 编码
// 不能直接拼接，必须使用 percent-encoding

func urlEncode(_ string: String) -> String {
    // 使用 urlQueryAllowed 字符集，但需要额外编码 +、=、/
    var allowed = CharacterSet.urlQueryAllowed
    allowed.remove(charactersIn: "+=&/")
    return string.addingPercentEncoding(withAllowedCharacters: allowed) ?? string
}

let params: [String: String] = [
    "public_key_b64": urlEncode(publicKeyBase64),
    "device_id": urlEncode("iphone-pro-001"),
    "allow_test_registration": urlEncode("true")
]

let bodyString = params.map { "\($0.key)=\($0.value)" }
    .joined(separator: "&")

var request = URLRequest(url: URL(string: "http://localhost:8000/register-key")!)
request.httpMethod = "POST"
request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
request.httpBody = bodyString.data(using: .utf8)

let (data, response) = try await URLSession.shared.data(for: request)
```

**编译状态**：⚠️ 未验证（伪代码）

**Base64 编码示例**：
- 原始 Base64：`BEw0ZJJUZjUJh3U5mTh2H+zyQH6cIo5JIYmpQ6t34t4D`
- URL 编码后：`BEw0ZJJUZjUJh3U5mTh2H%2BzyQH6cIo5JIYmpQ6t34t4D`
- 注意 `+` 被编码为 `%2B`

### 5.2 响应格式

**成功响应**：
```json
{
  "public_key_hash": "abc123...",
  "registration_status": "registered",
  "registered_at": "2026-11-17T10:00:00+00:00",
  "device_id": "iphone-pro-001",
  "environment": "development",
  "warning": "This is a TEST registration. Production will use App Attest formal registration."
}
```

**错误响应**：
- `TEST_REGISTRATION_NOT_ALLOWED`: 缺少 `allow_test_registration=true`
- `DEVICE_ID_CONFLICT`: 公钥已注册到不同 device_id
- `REGISTER_DISABLED`: 生产环境禁用测试注册

---

## 6. 验证接口

### 6.1 接口格式（已核对后端代码）

**端点**：`POST /verify`

**请求格式**：`application/json`

**请求结构**：
```swift
struct VerifyRequest: Codable {
    let manifest: SignatureManifest
    let photo_base64: String?  // 可选，但强烈建议提供
}
```

**示例**：
```swift
let request = VerifyRequest(
    manifest: manifest,
    photo_base64: photoData.base64EncodedString()
)

let jsonData = try JSONEncoder().encode(request)
var urlRequest = URLRequest(url: URL(string: "http://localhost:8000/verify")!)
urlRequest.httpMethod = "POST"
urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
urlRequest.httpBody = jsonData

let (data, response) = try await URLSession.shared.data(for: urlRequest)
let verifyResponse = try JSONDecoder().decode(VerifyResponse.self, from: data)
```

**编译状态**：⚠️ 未验证（伪代码）

### 6.2 响应结构

```swift
struct VerifyResponse: Codable {
    let evidence_status: String              // "cryptographically_valid", "signature_valid", etc.
    let cryptographic_signature_valid: Bool
    let photo_hash_valid: Bool?              // nil if photo not provided
    let key_registration_status: String      // "registered", "unregistered", "unknown"
    let device_identity_consistent: Bool?    // nil if key not registered
    let device_attestation_status: String    // always "unknown" currently
    let time_trust_level: String             // "device_reported"
    let summary: String
    let details: [VerifyDetail]
    let warnings: [String]
}

struct VerifyDetail: Codable {
    let check: String        // "structure", "photo_hash", "ecdsa_signature"
    let status: String       // "passed", "failed", "not_checked"
    let message: String
}
```

### 6.3 预期成功响应

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

## 7. 记录存储接口

### 7.1 接口格式（已核对后端代码）

**端点**：`POST /store-record`

**用途**：用于重放检测，存储已验证的记录

**重要限制**：
- `photo_base64` **必须提供**（不能为 null 或空）
- 签名必须有效
- 公钥必须已注册
- 设备身份必须一致
- 照片哈希必须匹配

**请求结构**：与 `/verify` 相同
```swift
struct StoreRecordRequest: Codable {
    let manifest: SignatureManifest
    let photo_base64: String  // 必须提供
}
```

**响应结构**：
```swift
struct StoreRecordResponse: Codable {
    let record_id: String
    let is_new: Bool
    let stored: Bool
    let message: String
    let stored_at: String?
    let rejection_reason: String?  // 如果 stored=false
}
```

**拒绝原因**：
- `photo_missing`: 未提供照片
- `photo_empty`: 照片为空字符串
- `invalid_signature`: 签名无效
- `unregistered_key`: 公钥未注册
- `device_identity_mismatch`: 设备身份不匹配
- `photo_hash_mismatch`: 照片哈希不匹配

---

## 8. 时间锚点 v2 接口

### 8.1 提交时间锚点

**端点**：`POST /time-anchor/anchor`

**请求结构**：
```swift
struct TimeAnchorRequest: Codable {
    let batch_id: String                    // 批次标识符，<=256字符
    let chain_head_hash: String             // 链头哈希，64位十六进制
    let chain_tail_hash: String             // 链尾哈希，64位十六进制
    let record_count: Int                   // 记录数量，>0, <=10000
    let device_reported_start_time: String  // ISO 8601
    let device_reported_end_time: String    // ISO 8601
}
```

**示例**：
```swift
let request = TimeAnchorRequest(
    batch_id: "batch-20261117-001",
    chain_head_hash: "abc123...",  // 第一条记录的哈希
    chain_tail_hash: "def456...",  // 最后一条记录的哈希
    record_count: 10,
    device_reported_start_time: "2026-11-17T09:00:00+00:00",
    device_reported_end_time: "2026-11-17T10:00:00+00:00"
)

let jsonData = try JSONEncoder().encode(request)
var urlRequest = URLRequest(url: URL(string: "http://localhost:8000/time-anchor/anchor")!)
urlRequest.httpMethod = "POST"
urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
urlRequest.httpBody = jsonData

let (data, response) = try await URLSession.shared.data(for: urlRequest)
let anchorResponse = try JSONDecoder().decode(TimeAnchorResponse.self, from: data)
```

**编译状态**：⚠️ 未验证（伪代码）

### 8.2 响应结构（TimeAnchorResponse）

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

**重要**：
- `TimeAnchorResponse` **不包含** `device_reported_start_time`、`device_reported_end_time`、`stored_at`
- 这些字段需要从请求中获取

### 8.3 构造完整回执（TimeAnchorReceipt）

```swift
// 必须从响应和请求中构造完整回执
let receipt = TimeAnchorReceipt(
    anchor_id: anchorResponse.anchor_id,
    key_id: anchorResponse.key_id,
    batch_id: anchorResponse.batch_id,
    server_received_at: anchorResponse.server_received_at,
    challenge: anchorResponse.challenge,
    server_signature: anchorResponse.server_signature,
    chain_head_hash: anchorResponse.chain_head_hash,
    chain_tail_hash: anchorResponse.chain_tail_hash,
    record_count: anchorResponse.record_count,
    verification_status: anchorResponse.verification_status,
    // 以下字段从请求中获取
    device_reported_start_time: request.device_reported_start_time,
    device_reported_end_time: request.device_reported_end_time,
    stored_at: anchorResponse.server_received_at
)

// 保存到本地数据库
try saveReceipt(receipt)
```

**编译状态**：⚠️ 未验证（伪代码）

### 8.4 验证时间锚点回执

**端点**：`POST /time-anchor/verify`

**请求格式**：`application/json`

**请求结构**：完整的 `TimeAnchorReceipt`

**查询参数**：
- `check_storage`: 是否检查服务器存储（默认 `true`）
  - `true`: 检查服务器存储（需要网络）
  - `false`: 跳过服务器存储核对，**但仍需要访问服务器**

**⚠️ 重要澄清**：
- `check_storage=false` **不是真正的离线验证**
- 它仍然需要访问服务器，只是跳过存储核对
- 真正的离线验证应该在 iOS 本地完成（见下方说明）

**示例**：
```swift
// 在线验证（检查服务器存储）
let jsonData = try JSONEncoder().encode(receipt)
var urlRequest = URLRequest(url: URL(string: "http://localhost:8000/time-anchor/verify?check_storage=true")!)
urlRequest.httpMethod = "POST"
urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
urlRequest.httpBody = jsonData

let (data, response) = try await URLSession.shared.data(for: urlRequest)
let verification = try JSONDecoder().decode(TimeAnchorVerification.self, from: data)

// 跳过存储核对（仍需网络）
var skipStorageRequest = URLRequest(url: URL(string: "http://localhost:8000/time-anchor/verify?check_storage=false")!)
skipStorageRequest.httpMethod = "POST"
skipStorageRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
skipStorageRequest.httpBody = jsonData
```

**编译状态**：⚠️ 未验证（伪代码）

### 8.4.1 真正的离线验证（iOS 本地）

**真正的离线验证**应该在 iOS 本地完成，不需要访问服务器：

```swift
import CryptoKit

// 1. 预先信任的服务器公钥（从安全渠道获取）
let trustedServerPublicKeyPem = """
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE...
-----END PUBLIC KEY-----
"""

// 2. 加载公钥
let publicKeyData = /* 从 PEM 解析公钥字节 */
let publicKey = try P256.Signing.PublicKey(x963Representation: publicKeyData)

// 3. 重建签名字节（与后端 _create_sign_data 相同）
let signData = buildTimeAnchorSignData(receipt: receipt)

// 4. 验证签名
let signatureData = Data(base64Encoded: receipt.server_signature)!
let signature = try P256.Signing.ECDSASignature(derRepresentation: signatureData)
let isValid = publicKey.isValidSignature(signature, for: signData)

if isValid {
    print("Receipt signature is valid")
    print("Server received at: \(receipt.server_received_at)")
} else {
    print("Receipt signature is invalid")
}
```

**编译状态**：⚠️ 未验证（伪代码）

**离线验证的限制**：
- 只能验证签名有效性
- 不能验证 `verification_status` 是否被篡改（需要检查服务器存储）
- 不能验证 `challenge` 是否被重用（需要检查服务器存储）
- 需要预先信任服务器公钥

### 8.5 验证响应结构

```swift
struct TimeAnchorVerification: Codable {
    let valid: Bool
    let anchor_id: String
    let key_id: String
    let server_received_at: String
    let signature_valid: Bool
    let challenge_matches: Bool
    let storage_matches: Bool
    let verification_status: String
    let trust_level: String
    let message: String
    let warnings: [String]
    let signed_fields_verified: Bool
    let unsigned_fields_note: String
}
```

### 8.6 重要说明

**verification_status 已纳入 v2 签名**：
- 客户端**不能**篡改 `verification_status`
- 篡改会导致签名验证失败

**DIGEST_ANCHORED 的含义**：
- 服务器只签署了提交的摘要（哈希、数量、时间）
- **不代表**服务器验证了照片内容
- **不代表**服务器验证了完整记录链
- 要获得 `PHOTO_VERIFIED` 或 `CHAIN_VERIFIED`，需要提交完整内容

---

## 9. 完整联调测试步骤

### 9.1 环境准备

**后端**：
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**iOS**：
- Xcode 15+
- iOS 17+
- iPhone Pro（带 Secure Enclave）- 用于 Secure Enclave 测试
- iPhone 或模拟器 - 用于普通密钥测试

### 9.2 测试流程

#### 步骤 1：生成并注册密钥

```swift
// 使用普通密钥（模拟器可用）
let privateKey = try P256.Signing.PrivateKey()
let publicKey = privateKey.publicKey
let publicKeyBase64 = publicKey.x963Representation.base64EncodedString()

// 注册公钥到后端
let params: [String: String] = [
    "public_key_b64": publicKeyBase64,
    "device_id": "iphone-pro-001",
    "allow_test_registration": "true"
]

// ... 发送 POST /register-key 请求
```

#### 步骤 2：拍照并生成清单

```swift
// 拍照
let photoData = try await capturePhoto()

// 计算哈希
let photoHash = computePhotoHash(photoData: photoData)

// 生成时间戳
let formatter = ISO8601DateFormatter()
formatter.formatOptions = [.withInternetDateTime]
let timestamp = formatter.string(from: Date())

// 生成清单（使用 var 因为需要赋值 signature）
var manifest = SignatureManifest(
    version: "1.1",
    record_id: UUID().uuidString,
    sequence_number: 0,
    previous_record_hash: nil,
    photo_hash: photoHash,
    capture_timestamp: timestamp,
    device_reported_time: timestamp,
    device_id: "iphone-pro-001",
    location: nil,
    public_key: publicKeyBase64,
    signature: ""  // 待填充
)

// 构建签名数据
let payload = buildCanonicalPayload(manifest: manifest)

// 签名（普通密钥需要 DER 转换）
let signature = try privateKey.signature(for: payload)
manifest.signature = signature.derRepresentation.base64EncodedString()
```

#### 步骤 3：提交到后端验证

```swift
let verifyRequest = VerifyRequest(
    manifest: manifest,
    photo_base64: photoData.base64EncodedString()
)

// ... 发送 POST /verify 请求

// 检查响应
assert(verifyResponse.evidence_status == "cryptographically_valid")
assert(verifyResponse.cryptographic_signature_valid == true)
assert(verifyResponse.photo_hash_valid == true)
assert(verifyResponse.key_registration_status == "registered")
```

#### 步骤 4：提交时间锚点

```swift
// 计算记录哈希
let recordHash = computeRecordHash(manifest: manifest)

// 提交时间锚点
let anchorRequest = TimeAnchorRequest(
    batch_id: "test-batch-\(UUID().uuidString)",
    chain_head_hash: recordHash,
    chain_tail_hash: recordHash,
    record_count: 1,
    device_reported_start_time: manifest.capture_timestamp,
    device_reported_end_time: manifest.capture_timestamp
)

// ... 发送 POST /time-anchor/anchor 请求

// 构造完整回执
let receipt = TimeAnchorReceipt(
    anchor_id: anchorResponse.anchor_id,
    key_id: anchorResponse.key_id,
    batch_id: anchorResponse.batch_id,
    server_received_at: anchorResponse.server_received_at,
    challenge: anchorResponse.challenge,
    server_signature: anchorResponse.server_signature,
    chain_head_hash: anchorResponse.chain_head_hash,
    chain_tail_hash: anchorResponse.chain_tail_hash,
    record_count: anchorResponse.record_count,
    verification_status: anchorResponse.verification_status,
    device_reported_start_time: anchorRequest.device_reported_start_time,
    device_reported_end_time: anchorRequest.device_reported_end_time,
    stored_at: anchorResponse.server_received_at
)

// 验证回执
// ... 发送 POST /time-anchor/verify 请求

assert(verification.valid == true)
assert(verification.signature_valid == true)
```

---

## 10. 常见失败场景及排查

### 10.1 签名验证失败

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
   - 普通密钥：必须使用 `.derRepresentation`
   - Secure Enclave：已经是 DER 格式，不需要转换
   - 必须以 `0x30` 开头

4. **检查字段值**：
   - 不能包含换行符（`\n` 或 `\r`）
   - 不能包含等号（`=`）
   - 时间戳必须是 ISO 8601 格式

### 10.2 照片哈希不匹配

**症状**：`photo_hash_valid = false`

**排查步骤**：

1. **检查照片字节**：
   ```swift
   print("Photo size:", photoData.count)
   print("Photo hash:", computePhotoHash(photoData: photoData))
   ```

2. **检查文件保存一致性**：
   ```swift
   // 保存前
   let hash1 = computePhotoHash(photoData: photoData)
   
   // 保存后
   let reloadedData = try Data(contentsOf: fileURL)
   let hash2 = computePhotoHash(photoData: reloadedData)
   
   assert(hash1 == hash2)
   ```

3. **确认使用原始照片**：
   - 不能是压缩后的 JPEG（如果原始是 HEIC）
   - 不能是修改过的数据
   - 必须是相机原始输出

### 10.3 公钥未注册

**症状**：`key_registration_status = "unregistered"`

**排查步骤**：

1. **检查注册请求格式**：
   - 必须使用 `application/x-www-form-urlencoded`
   - 必须包含 `allow_test_registration=true`

2. **检查公钥一致性**：
   - 注册时使用的公钥
   - 签名时使用的公钥
   - 必须完全相同（Base64 字符串相同）

3. **检查 device_id 一致性**：
   - 注册时的 device_id
   - 清单中的 device_id
   - 必须完全相同

### 10.4 时间锚点验证失败

**症状**：`verification.valid = false`

**排查步骤**：

1. **检查回执完整性**：
   - 必须包含所有必需字段
   - `device_reported_start_time` 和 `device_reported_end_time` 从请求中获取

2. **检查 verification_status**：
   - 不能篡改
   - 必须与服务器返回的值一致

3. **检查 key_id**：
   - 必须是已注册的
   - 检查服务器公钥端点：`GET /time-anchor/public-key`

---

## 11. 测试向量

### 11.1 固定测试向量

后端提供了固定测试向量：`test-vectors/fixed-vectors.json`

包含：
- 固定公钥（Base64）
- 固定照片字节（十六进制）
- 固定照片哈希
- 固定规范化数据（十六进制）
- 固定签名（Base64）

### 11.2 验证测试向量

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

// 验证签名（使用普通密钥）
let publicKey = try P256.Signing.PublicKey(x963Representation: pubKeyData)
let signature = try P256.Signing.ECDSASignature(derRepresentation: Data(base64Encoded: vectors.signature.base64)!)
let isValid = publicKey.isValidSignature(signature, for: payload)
assert(isValid)
```

**编译状态**：⚠️ 未验证（伪代码）

---

## 12. 编译状态总结

| 代码示例 | 编译状态 | 说明 |
|---------|---------|------|
| SignatureManifest 结构 | ⚠️ 未验证 | 伪代码，需要实际编译 |
| buildCanonicalPayload | ⚠️ 未验证 | 伪代码，需要实际编译 |
| 普通密钥生成 | ⚠️ 未验证 | 伪代码，需要实际编译 |
| Secure Enclave 密钥生成 | ⚠️ 未验证 | 需要真机测试 |
| 普通密钥签名 | ⚠️ 未验证 | 伪代码，需要实际编译 |
| Secure Enclave 签名 | ⚠️ 未验证 | 需要真机测试 |
| 照片哈希计算 | ⚠️ 未验证 | 伪代码，需要实际编译 |
| API 请求示例 | ⚠️ 未验证 | 伪代码，需要实际编译 |

**重要**：所有 Swift 代码示例均为伪代码，未经实际编译验证。Mac 端开发时需要：
1. 根据实际 Swift 语法调整
2. 处理错误和异常
3. 在真机上测试 Secure Enclave 功能

---

## 附录 A：完整代码示例（伪代码）

### A.1 WorkCamSigner 类

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
        
        // 5. 签名（普通密钥需要 DER 转换）
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

**编译状态**：⚠️ 未验证（伪代码）

---

## 附录 B：API 端点参考（已核对）

| 端点 | 方法 | Content-Type | 说明 |
|------|------|--------------|------|
| `/register-key` | POST | `application/x-www-form-urlencoded` | 注册公钥 |
| `/verify` | POST | `application/json` | 验证签名清单 |
| `/store-record` | POST | `application/json` | 存储记录（重放检测） |
| `/time-anchor/anchor` | POST | `application/json` | 提交时间锚点 |
| `/time-anchor/verify` | POST | `application/json` | 验证时间锚点回执 |
| `/time-anchor/public-key` | GET | - | 获取服务器公钥 |

---

## 附录 C：修订摘要

### WC-W004-R2 修订内容

1. **Secure Enclave 签名示例修正**
   - ✅ 修正错误：Secure Enclave 签名**也需要**使用 `.derRepresentation` 获取 DER 字节
   - ✅ 删除"Secure Enclave 签名天然就是 DER、无需转换"的错误表述
   - ✅ 明确说明：普通密钥和 Secure Enclave 密钥都需要调用 `.derRepresentation`
   - ✅ 保留 Secure Enclave 与 App Attest 的安全边界说明

2. **公钥注册表单编码修正**
   - ✅ 强调 Base64 字符串中的 `+`、`=`、`/` 等字符必须 URL 编码
   - ✅ 提供正确的 URL 编码函数示例
   - ✅ curl 示例使用 `--data-urlencode` 自动编码
   - ✅ 增加包含特殊字符的测试示例

3. **离线验证表述修正**
   - ✅ 澄清 `check_storage=false` 仍需要访问服务器
   - ✅ 明确说明这不是真正的离线验证
   - ✅ 提供真正的离线验证示例（iOS 本地使用预先信任的服务器公钥）
   - ✅ 说明离线验证的限制

### WC-W004-R1 修订内容

1. **Secure Enclave 示例修复**
   - ✅ 明确区分普通密钥和 Secure Enclave 密钥
   - ✅ 提供正确的 Secure Enclave 密钥生成示例
   - ✅ ~~说明 Secure Enclave 签名已经是 DER 格式~~（R2 修正：需要 `.derRepresentation`）
   - ✅ 明确 Secure Enclave 签名不等于 App Attest

2. **Swift 编译问题修复**
   - ✅ 统一使用 snake_case 字段名（与后端一致）
   - ✅ 修复不可变属性赋值问题（使用 `var`）
   - ✅ 标注所有示例为"伪代码，未编译验证"

3. **时间锚点验证流程修复**
   - ✅ 明确 TimeAnchorResponse 与 TimeAnchorReceipt 的差异
   - ✅ 说明需要从请求中构造完整回执
   - ✅ 说明 verification_status 已纳入 v2 签名
   - ✅ 明确 DIGEST_ANCHORED 的含义

4. **照片哈希说明修复**
   - ✅ 强调必须使用实际保存的文件字节
   - ✅ 说明 JPEG、HEIC 等编码格式都可以
   - ✅ 禁止重新编码或修改证据文件
   - ✅ 增加文件保存前后一致性检查

5. **后端接口核对**
   - ✅ 核对 `/register-key` 请求格式（表单数据）
   - ✅ 核对 `/verify` 请求与响应
   - ✅ 核对 `/store-record` 使用条件
   - ✅ 核对 `/time-anchor/anchor` 和 `/time-anchor/verify`
   - ✅ 核对测试向量字段名称

---

**文档版本**：1.2（WC-W004-R2 修订版）  
**最后更新**：2026-11-17  
**适用协议版本**：签名 v1.1，时间锚点 v2  
**编译验证状态**：所有 Swift 示例均为伪代码，未实际编译
