# B02-W018 六层级验证报告模板与退出码规范

工具：`tools/evidence-verifier/verify_evidence.py`
（`workcam-evidence-verifier` v1.0.0，任务编号 B02-W018）
日期：2026-11-25 · 状态：Windows 侧全测试通过；Mac 真机包交叉验证 **DEVICE_PENDING**

本文件是验证报告的规范文档：六层级各自验证什么、各状态的确切语义、
记录级 verdict 如何派生、退出码契约、JSON/人类报告模板。

核心红线（报告措辞必须体现，缺一即为违约）：

> 数学签名有效只证明**字节完整性**与**签名数学**。
> 它**不**证明照片内容真实、**不**证明硬件密钥来源已认证、
> **不**构成任何法律效力认证。
> `cannot_verify` 永远不会被报告为通过。

---

## 1. 验证的六个层级（必须逐层分开，不得混为一谈）

| 层 | 名称 | 验证内容 | 数据来源 |
|---|---|---|---|
| L1 | file_integrity 文件完整性 | 文件存在；原图字节 SHA-256 与被签名的 `photo_hash` 一致；`package.json` 记录列表与各 manifest 的 `record_id` 交叉绑定 | 包内文件 + `backend/techlab/evidence_package.py::verify_package` |
| L2 | signature_math 数学签名有效性 | 公钥格式检查（65 字节非压缩 P-256 点，`0x04 + x + y`）；DER 签名结构检查；**精确签名载荷重构**（canonical payload v1.0/v1.1 域分隔 + 字段定序 + 浮点格式化）；ECDSA P-256 (secp256r1) + SHA-256 验签 | `backend/app/services/signature.py` + `serialization.py`（只读复用，生产证据协议未改动） |
| L3 | device_identity 已注册设备身份 | 签名公钥是否在**本地**信任注册表文件（`--registry`）中 | `backend/app/services/key_registry.py::InMemoryKeyRegistry`（离线 JSON 文件，绝无联网查询） |
| L4 | hardware_provenance 硬件密钥来源证明 | Secure Enclave / 硬件密钥 attestation | **离线不可验**——包内不携带任何 attestation 凭据；恒报 `not_verifiable_offline` |
| L5 | trusted_time 可信时间状态 | 时间锚点回执的独立验签（`--anchor-pubkey` 提供时）对比裸设备时间戳 | `backend/techlab/anchor_verifier.py::verify_anchor_receipt_standalone`（锚点 v2 签名字节级对拍已 pin） |
| L6 | location_provenance 定位来源状态 | 坐标是否被签名固定（manifest.location ∈ 签名载荷）；T02 location_meta 的 source/grade/trust_label | `backend/techlab/location_meta.py`（manual 永不显示为已验证位置） |

### 各层状态枚举与语义

**L1 文件完整性**
- `pass`：照片与 manifest 文件齐全，字节哈希与被签名哈希一致，record_id 绑定一致
- `fail`：**文件在但被篡改**（photo_hash 不匹配）或 record_id 交叉绑定断裂
- `cannot_verify`：照片或 manifest 文件**缺失**（无从核对完整性）

**L2 数学签名**
- `pass`：公钥格式、DER、canonical payload、ECDSA 验签全部通过
- `fail`：**明确的坏输入**（坏公钥 / 坏 DER / 非法字段 / 验签失败）——
  畸形输入是失败，**不是** cannot_verify
- `cannot_verify`：manifest 缺失或不可读，签名数学无从执行

**L3 已注册设备身份**
- `registered`：签名公钥在本地注册表文件中
- `unregistered`：不在（数学过 ≠ 身份已知，附注说明）
- `not_checked`：未提供 `--registry`（离线默认态，如实报告）

**L4 硬件密钥来源证明**
- `not_verifiable_offline`：**唯一状态**。离线包不携带（也无法携带）硬件
  attestation 凭据；签名有效绝不被解释为硬件密钥来源证明。

**L5 可信时间**
- `server_anchored`：包携带锚点回执且通过可信公钥独立验签
- `device_reported`：无锚点回执，时间戳只是设备自报
- `fail`：**声称了**锚点回执但验签失败（不诚实声明，等同记录失败）
- `cannot_verify`：有回执但未提供 `--anchor-pubkey`（无从核验，不判通过）

**L6 定位来源**
- `signed_in_payload`：坐标进入签名载荷（拍照时刻被签名固定）
- `none`：无坐标
- `cannot_verify`：manifest 不可读
- 恒附注：签名只固定**坐标字节**，坐标来源（GPS/手动）与其真实性**离线不可证**。
  location_meta 若提供 source/grade，则按 T02 语义展示（manual = 用户手输，
  永不显示为已验证位置）。

### 记录级 verdict 派生规则

| 条件 | verdict |
|---|---|
| L1、L2 或 L5 任一 `fail`；或 location_meta 非法 | `fail` |
| L1、L2 或 L5 任一 `cannot_verify` | `cannot_verify` |
| 以上皆无，L3 = `unregistered` | `pass_unregistered_key` |
| 以上皆无，其余情况 | `pass` |

L6 不影响 verdict（无定位是合法状态；来源不可证不是失败）。
L4 恒为不可证，不影响 verdict。

### 包级 outcome

- `pass`：所有记录 pass / pass_unregistered_key，且无包级问题
- `fail`：存在 fail / cannot_verify 记录，或包级问题
  （package.json 未列出却存在的孤儿文件、衍生图断链/篡改等）
- `corrupt`：见退出码 2

---

## 2. 退出码规范（契约，不可变更）

| 码 | 名称 | 触发条件 |
|---|---|---|
| 0 | OK | 全部包、全部记录的 L1/L2 验证通过（L3-L6 的 not_checked / not_verifiable_offline / device_reported 如实报告，**不**计为失败） |
| 1 | VERIFY_FAILED | 至少一个记录 fail / cannot_verify，或包级问题（篡改、缺文件、绑定断裂、锚点验签失败、孤儿文件、衍生图问题） |
| 2 | CORRUPT_INPUT | 包格式损坏：输入路径不存在、package.json 缺失/不可读、zip 损坏（BadZipFile）、zip-slip 路径穿越 / 绝对路径 / 盘符 / 反斜杠 entry / 大小写冲突 / 解压总尺寸或压缩比炸弹防护触发。**argparse 用法错误同为 2**（输入不可用类） |

优先级：任一包 corrupt → 2；否则任一 fail → 1；否则 0。

CI/脚本用法：`0` 才可宣称「签名数学验证通过」，且措辞必须同时引用
六层报告中 L3-L6 的实际状态；`1` 是可定位的验证失败（逐记录看 problems）；
`2` 是连验证都无法开始的坏输入。

---

## 3. 人类可读报告模板

```
workcam-evidence-verifier 1.0.0 (task B02-W018) - offline, no account, no network
input photos are hashed locally and never uploaded
  ! This report is OFFLINE VERIFICATION MATH ONLY.
  ! A valid signature proves byte integrity and signature math.
  ! It is NOT proof that the photo content is genuine.
  ! It is NOT proof of hardware key provenance (Secure Enclave).
  ! It is NOT a legal certification of anything.
  ! DEVICE_PENDING: validated against public test vectors only; no real
  ! Mac device evidence package has been cross-validated yet

== Package 1/2: site-A.zip ==
  kind: zip
  package version: 1 | device_id: lab-device
  [record a1b2...] verdict: PASS_UNREGISTERED_KEY
    L1 file integrity      : PASS
    L2 signature math      : PASS
    L3 device identity     : UNREGISTERED
    L4 hardware provenance : NOT_VERIFIABLE_OFFLINE
    L5 trusted time        : DEVICE_REPORTED
    L6 location provenance : NONE
    problem: ...
    note: ...
    NOTE: a valid signature proves byte integrity only; it is NOT proof
          that the photo content was genuine, NOT proof of hardware
          provenance, and NOT a legal certification
  [PACKAGE-PROBLEM] ...
  package outcome: FAIL

Summary: 2 packages | 1 passed | 1 failed | 0 corrupt
exit code: 1
```

---

## 4. JSON 报告模板

```json
{
  "tool": "workcam-evidence-verifier",
  "task": "B02-W018",
  "version": "1.0.0",
  "offline": true,
  "disclaimer": [ "…5 条免责声明，逐字见 §1 红线…" ],
  "device_pending": true,
  "packages": [
    {
      "input": "site-A.zip",
      "kind": "zip",
      "outcome": "pass | fail | corrupt",
      "package_version": "1",
      "device_id": "…",
      "records": [
        {
          "record_id": "…",
          "verdict": "pass | pass_unregistered_key | cannot_verify | fail",
          "layers": {
            "L1_file_integrity": "pass | fail | cannot_verify",
            "L2_signature_math": "pass | fail | cannot_verify",
            "L3_device_identity": "registered | unregistered | not_checked",
            "L4_hardware_provenance": "not_verifiable_offline",
            "L5_trusted_time": "server_anchored | device_reported | fail | cannot_verify",
            "L6_location_provenance": "signed_in_payload | none | cannot_verify"
          },
          "problems": [],
          "notes": [],
          "raw": { "…verify_package 原始字段（photo_found、signature_valid 等）…": "…" }
        }
      ],
      "package_problems": [],
      "derivative_problems": [],
      "anchor_receipts": [ { "record_id": "…", "valid": true, "reason": "…" } ]
    }
  ],
  "summary": { "packages": 1, "passed": 1, "failed": 0, "corrupt": 0 },
  "exit_code": 0
}
```

样例（公开测试向量真实运行产物）：`docs/evidence-verifier/samples/`。

---

## 5. ZIP 安全解包规范（防 zip-slip 路径穿越）

解包前对每个 entry 做静态检查，任何一条命中即整包拒收（退出码 2），
**不**解出任何文件：

1. 绝对路径（`/…`）与盘符（`C:…`）
2. `..` 路径组件（逐段检查，不靠字符串包含）
3. 反斜杠分隔符（注：CPython 3.12 zipfile 读取端会把 `\` 规范化为 `/`，
   敌意反斜杠名通常以 `..` 穿越 guard 被拒；显式检查保留作为纵深防御）
4. 大小写不敏感冲突（`photos/a.jpg` vs `photos/A.JPG`，Windows 落盘覆盖）
5. 单文件尺寸与解压总尺寸上限（默认 1024 MiB，`--max-uncompressed-mb` 可调）
6. 极端压缩比（>200:1 且 >1 MiB，zip 炸弹特征）
7. zip 损坏（`BadZipFile`）

解包目标为一次性临时目录，验证结束即删除（`--keep-extracted` 可留验）。
包根解析：`package.json` 必须在 zip 根，或在**唯一**顶层目录内
（macOS Finder「压缩」默认布局）；其余一律 corrupt。

---

## 6. Mac 端接入（如何导出可验证证据包）

见 `tools/evidence-verifier/README.md`「Mac 端接入」一节：
包格式（T13 v1）、签名协议（canonical payload 跨语言字节合同
`test-vectors/fixed-vectors.json`）、两种 zip 布局、
L3/L5 所需的接收方本地信任文件格式。

真实 Mac 设备导出包的交叉验证状态：**DEVICE_PENDING**（未完成前，
所有验证结论仅对公开测试向量成立，报告逐条注明）。
