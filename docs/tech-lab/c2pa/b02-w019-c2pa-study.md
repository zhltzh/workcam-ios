# B02-W019 C2PA 兼容性研究报告（Content Credentials / C2PA 标准研究）

日期：2026-11-25 · 分支 `dev/windows-b02-w019-c2pa`（基线 `31917e3`）
任务：WORKCAM-WINDOWS-BATCH-02 / B02-W019（P1，02 算法与工程技术研发组）
作者：WorkCam 研发（Windows 工作流）

## 0. 定位声明（不可省略）

- 本报告是**规范符合性研究与独立 PoC 实验**。C2PA 联盟没有对本项目做过任何
  认证、审核或背书；本仓库没有任何代码或文档可以被称为"C2PA 认证"。
- 本任务**未修改任何生产证据协议代码**（backend 生产代码零改动；
  `requirements.txt` 零改动；backend `.venv` 与基线一致）。
- PoC 位于 `backend/tools/c2pa-poc/`，使用独立 venv，不接线生产代码。
- 本报告承接并深化 T14 桥接研究（`docs/tech-lab/reports/t14-c2pa-diff.md`）：
  T14 在无官方 SDK 的条件下钉死了"断言映射语义"；本任务首次引入官方 SDK
  （c2pa-python 0.39.0 / c2pa-rs 0.92.0）对 T14 十项差距做了实测回填。

## 1. 规范版本与官方信源状态（2026-11-25 访问）

| 信源 | URL | 状态 |
|---|---|---|
| C2PA 规范站 | https://spec.c2pa.org | 当前版本 **2.5**（2.2 页 canonical 已指向 2.5） |
| 规范全文（2.5） | https://spec.c2pa.org/specifications/specifications/2.5/specs/C2PA_Specification.html | 抓取成功（1,468,438 B HTML），本文引用均出自此版本 |
| Content Credentials 官网 | https://contentcredentials.org | 可访问（`/verify` 为 JS 渲染，纯文本不可提取，已如实记录） |
| 官方信任锚 | https://contentcredentials.org/trust/anchors.pem | HTTP 200，44,078 B，**27 张证书** |
| Adobe Photoshop 文档 | https://helpx.adobe.com/photoshop/using/content-credentials.html | **403 Access Denied**（Akamai 拦截），未获取正文，如实记录 |

说明：规范 2.4 的 claim 结构（v2 claim）已是 c2pa-rs 默认；2.5 在 2.4 基础上
细化信任模型（§13.4 trust lists）、证书状态（§16.7.1 certificate-status）与
时间戳断言（§16.7.2）。本报告以 2.5 为准并注明版本差异。

## 2. 九个调研点逐项结论

### 2.1 官方 SDK 与工具生态

官方组织 `contentauth`（Content Authenticity Initiative，Adobe 发起）的
完整 SDK 家族（GitHub API 实查，2026-11-25）：

| 仓库 | 语言/用途 | stars | 状态 |
|---|---|---|---|
| contentauth/c2pa-rs | Rust 核心库（其余绑定的底层） | 454 | beta（0.x，约两个月一列 breaking release train） |
| contentauth/c2patool | CLI：显示/嵌入 manifest | 174 | 活跃 |
| contentauth/c2pa-python | c2pa-rs 的 Python 绑定 | 110 | **pip 包名 `c2pa-python`（0.39.0）** |
| contentauth/c2pa-swift | iOS/macOS Swift SDK（包 c2pa-rs 的 C API XCFramework） | 28 | 早期（SwiftPM `from: "0.0.1"`） |
| contentauth/c2pa-js / c2pa-node / c2pa-cpp / c2pa-android | JS/Node/C++/Android 绑定 | 45/21/20/15 | 活跃 |
| contentauth/c2pa-attacks | 官方安全测试工具 | 15 | 活跃 |
| contentauth/verify-site | contentcredentials.org/verify 的开源代码 | 10 | 活跃 |
| contentauth/c2pa-conformance-tool(-cli) | 官方符合性测试工具 | 6+1 | 活跃 |

⚠️ **PyPI 裸名 `c2pa` 包（1.4.1）不是官方 SDK**：其 PyPI 元数据声明
`License: GPLv3`，`summary: Implementation of C2PA`，与官方
`c2pa-python`（0.39.0，MIT OR Apache-2.0，repo 指向 contentauth/c2pa-python）
不同源。采购/引用时必须用 `c2pa-python`，避免 GPLv3 传染与无维护风险。

Adobe 系工具链：Photoshop/Lightroom/Firefly 的 Content Credentials 集成
（Adobe 官方文档页 403 未能核验正文，此处仅作存在性描述）；公开验证工具为
contentcredentials.org/verify（开源库 verify-site）。

**结论**：SDK 生态成熟可用，Python/Rust/iOS 三端都有官方绑定；但全部处于
0.x beta（API 不稳定承诺：中位号变更=破坏性变更），MSRV Rust 1.96.0。

### 2.2 许可证核查（引用原文出处）

| 组件 | 许可证 | 原文出处（2026-11-25 访问） |
|---|---|---|
| c2pa-rs | **MIT OR Apache-2.0**（双许可） | README §License：`The c2pa crate is distributed under the terms of both the MIT license and the Apache License (Version 2.0)`；文件 LICENSE-MIT / LICENSE-APACHE（GitHub API 报 NOASSERTION 正因双许可） |
| c2pa-python | **MIT OR Apache-2.0** | README §License：`This project is licensed under the Apache License 2.0 and the MIT License`；PyPI metadata `License: MIT OR Apache-2.0` |
| c2pa-swift | **Apache-2.0 + MIT** | README §License：`licensed under the Apache License, Version 2.0 and MIT License` |
| c2patool | **Apache-2.0** | GitHub API `license.spdx_id: Apache-2.0` |
| PyPI 裸名 `c2pa`（非官方） | **GPLv3** | PyPI JSON `license: GPLv3`（避用） |
| 示例代码 | Apache-2.0 / MIT（带版权头） | `examples/sign.py` 首部版权声明 |

**结论**：官方栈双许可（MIT/Apache-2.0），商用与闭源集成无障碍；
唯一雷区是 PyPI 裸名 `c2pa` 的 GPLv3 假包。c2pa-rs 依赖的部分 crates 有
独立条款（README §License 提示需逐项检查），工程上还需一次
`cargo deny`/license-audit，本轮未做（Not Implemented，集成期补）。

### 2.3 iOS 与 Python/Rust 后端集成方式

**iOS（c2pa-swift）**：SwiftPM 直接引入；要求 iOS 16.0+ / macOS 14.0+；
底层是预编译的 c2pa-rs C API XCFramework（`C2PAC.xcframework`）。
支持 **Secure Enclave 硬件签名**（iOS 设备/Apple Silicon）与本地测试
签名服务器（CSR→证书→签名）。WorkCam 工程集成需注意：AGENTS.md
"不擅自加依赖"——引入 c2pa-swift 属于显式授权事项；当前 Xcode/Swift
要求（README：Xcode 26.4+ / Swift 6.3+）需与 Mac 工作流核对。
备选路径：c2pa-rs 的 `c2pa_c_ffi` C API 自行编译静态库（MSVC/Xcode
C build tools）。

**Python 后端（c2pa-python 0.39.0）**：本轮 PoC 实测可行（Windows
Python 3.12.10 直接安装运行）。API 面：`Builder`/`Signer`/`Reader`/
`Context`/`Settings`（0.39 新 API；旧 `Image.sign` 已弃用）。
对 WorkCam：Python 侧适合"服务端再签名/验证服务"（与现有 FastAPI 同栈）；
若追求设备端签名，走 iOS 的 c2pa-swift 或 Rust 原生。

**Rust 原生（c2pa-rs 0.92.0）**：`Context` 线程安全配置（Arc 共享）、
progress/cancellation 回调、embeddable API（对 manifest 嵌入位点的
显式控制）。作为最高性能路径留作后端签名服务的长期选项。

**结论**：三端集成路径全部真实存在且有文档；iOS 端工程量=依赖授权+
XCFramework 体积+Secure Enclave 密钥→CSR→证书的注册流程搭建。

### 2.4 JPEG 与 HEIC 支持（容器嵌入面）

- **JPEG**：manifest 以 JUMBF box 嵌入（ISO/IEC 19566-5），c2pa-rs
  supported-formats 官方列表 `jpg/jpeg → image/jpeg`。PoC 实测
  签名/读回/篡改检测全链路通过。
- **HEIC/HEIF**：c2pa-rs 官方支持（`heic → image/heic`、`heif →
  image/heif`，走 ISO BMFF 路径）。PoC 用 pillow-heif 1.9.0 编码
  HEIC 后签名读回 **Trusted**（实测 +3,890 B / 42.2ms 签名）。
  已知缺口：`image/heic-sequence` / `heif-sequence` 不支持
  （c2pa-rs issue #2774，open）；BMFF 哈希有过多个整数溢出安全修复
  （#2263/#2359/#2054 等，已 closed）。
- **iOS 侧现实约束**：
  1. WorkCam 当前拍摄输出为 JPEG（`Core/Camera/CameraController.swift:90`
     `AVVideoCodecType.jpeg`），不经过 HEIC 管线——C2PA JPEG 路径可
     直接落地，无需先解决 HEIC。
  2. iPhone 相机"高效"模式原生输出 HEIC 是平台事实；若未来 WorkCam
     切 HEIC，c2pa 容器层已支持（本 PoC 实证），风险不在容器而在下述
     生态（3）。
  3. **manifest 存活性**（Apple Photos 编辑、隔空投送、第三方分享管线
     是否保留第三方 app 写入的 C2PA box）**未在本轮验证**（无 Mac/iOS
     实机；列为 Not Implemented 与后续 Mac 任务项，不写推测性结论）。

**结论**：JPEG 路径 PoC 实测全通；HEIC 容器支持实证存在；WorkCam
自身用 JPEG，无阻塞性容器问题；生态存活性待 Mac 实测。

### 2.5 离线签名与后续时间戳（offline signing + 延时补戳）

规范机制（spec 2.5）：

- **签名时 TSA（同步）**：§10.3.2.5——签名时向 RFC 3161 TSA 请求
  时间戳，作为 COSE countersignature 嵌入。**"manifest 无时间戳时，
  签名证书过期或吊销后 manifest 即失效"**（§10.3.2.5.1 原文：
  "manifests without time stamps cease to be valid when the signing
  credential expires or becomes revoked"）。一个 manifest 只允许一个
  时间戳（2.x 起废除多时间戳）。
- **离线签名后的延时补戳（异步）**：§16.7.2 **Time Stamps 断言**正是
  为离线工作流设计的机制，原文：
  *"a standard or update manifest is created offline, where it is not
  possible to obtain a trusted time stamp … a trusted time stamp can be
  added at a later point in time (provided the certificate has not yet
  expired) providing for a 'proof of existence'"*。
  结构：`time-stamp-map`（manifest URN → RFC 3161 时间戳 blob）。
- **TSA 时间戳的长期有效性**：§13（validation）——即使 TSA 证书过期，
  只要被证明时间落在 TSA 证书有效期内，时间戳仍有效；验证时以
  attested time 而非当前时间判断签名证书时效。
- **内容不变时追加断言**：§11.3.3 **Update Manifest**（action 限
  `c2pa.edited.metadata` / `c2pa.opened` / `c2pa.published` /
  `c2pa.redacted`），通过 `c2pa.ingredient.v3` 的
  `activeManifest`+`claimSignature`+`parentOf` 引用原 manifest。
- **SDK 支持**：c2pa-rs/c2pa-python 的 `SignerInfo(ta_url=…)`；
  `ta_url=None` 即纯离线签名。PoC 实测：离线签名成功（56.2ms）；
  在线 TSA 请求在沙箱内失败（SDK 原生 HTTP 不走 Windows 用户代理：
  `error sending request for url (https://timestamp.digicert.com/)`，
  如实记录）。

**与 WorkCam 模型对照**：WorkCam 时间锚点（`docs/offline-time-trust.md`）
= 离线记录摘要 → 联网后服务器签署时间回执（DIGEST_ANCHORED 等）。这与
C2PA §16.7.2 结构同构（离线产物 + 事后第三方时间证明），差异在信任源：
WorkCam 服务器是自建锚，C2PA 要求信任列表内的 TSA（§13.4.2）。
TSA 信任锚与签名者信任锚是**两套独立列表**（§13.4.2 原文 "shall be
separate from the lists for C2PA signers"）。

**结论**：离线签名原生支持（ta_url=None）；延时补戳有标准机制（§16.7.2
时间戳断言 + §11.3.3 update manifest），且补戳必须赶在签名证书过期前——
这给 WorkCam 现有"72 小时离线窗口"增加了一条新的硬时限语义。

### 2.6 证书与信任链要求（vs WorkCam 服务器锚定模型）

C2PA 信任模型（spec 2.5 §13）：

1. **签名凭证是 X.509 证书链，不是裸公钥**。COSE_Sign1 的
   `x5chain`（integer label 33，protected header）必须带叶子证书+全部
   中间 CA；根证书不带（§13.5）。
2. **证书 profile**（§13.5.1，PoC 踩坑实证）：叶证书必须 v3、
   BasicConstraints CA=False、KeyUsage digitalSignature、EKU 非空且
   不含 anyExtendedKeyUsage；CA 证书必须有 SubjectKeyIdentifier、
   非自签证书必须有 AuthorityKeyIdentifier；签 C2PA claim/TSA/OCSP
   的密钥三者不可复用；TSA 或 OCSPSigning-only 证书不能签 claim。
   认可的 claim EKU：emailProtection、documentSigning
   (1.3.6.1.5.5.7.3.36)、Microsoft C2PA signing
   (1.3.6.1.4.1.311.76.59.1.9)、c2pa-kp-claimSigning
   (1.3.6.1.4.1.62558.2.1)。
3. **信任列表按 EKU 分组**（§13.4.1）：每 EKU 一组"信任锚配置"
   （证书 + notBefore/notAfter 窗口）；官方 C2PA Trust List 是
   c2pa-kp-claimSigning 的推荐默认；验证者可配置外部列表。
   官方信任锚实测 27 张证书（contentcredentials.org/trust/anchors.pem）。
4. **私有凭证库**（§13.4.3）：用户显式信任的"通讯录"，只能直接信任
   叶证书、不能当 CA、不适用于 TSA 验证——**这是 WorkCam 服务器锚定
   模型可映射的最贴近入口**（把 WorkCam 设备公钥/证书作为用户配置的
   私有信任，而非申请进入 C2PA Trust List）。
5. **吊销**：OCSP stapling（`rVals` unprotected header / certificate-status
   断言 §16.7.1）；明确禁止 CRL。
6. PoC 实测 SDK 行为：0.92.0 在**签名时**即执行证书 profile 检查——
   自签叶证书被拒（`Signature: the certificate was self-signed`）；
   CA 缺 SKI/叶缺 AKI 被拒（`Signature: the certificate is invalid`）；
   补齐后通过。读取侧状态分级实测：无信任锚=**Valid**（密码学完整但
   签署者不受信）、装锚后=**Trusted**、篡改=**Invalid**。

**与 WorkCam 差异（核心）**：WorkCam `signature-protocol.md` 是
裸公钥（65B 未压缩点）+ 服务器 key_registry 注册表（中心化锚定）；
C2PA 是 X.509 链 + 按 EKU 分组的分布式信任列表 + OCSP。两者不兼容，
映射只能通过"私有凭证库/自建信任列表"衔接（见 §4 映射表 G4）。

**结论**：信任链要求是集成成本重心：需要建 CA/购证书、管理证书生命
周期（过期=manifest 失效）、OCSP/TSA 运营决策；WorkCam 中心化锚定
模型无法直接获得 C2PA 生态信任，但可通过私有信任库保持语义自洽。

### 2.7 原始照片与衍生照片的关系表达（ingredient / derivative）

- 机制：manifest 的 **ingredient 断言**（`c2pa.ingredient` / v2 / v3）
  记录被引用素材，`relationship` 取 `parentOf`（源）、`componentOf`
  （合成）等；衍生资产通过 ingredient 链把父 manifest 递归携带。
  Update Manifest 额外要求 `activeManifest`+`claimSignature` URI
  （§11.3.3）。
- SDK：`Builder.add_ingredient()` + `set_intent(EDIT/CREATE/UPDATE)`
  （intent 保证 spec 合规的动作语义链）。
- **PoC 实测**：把已签名 JPEG 作为 `parentOf` ingredient 签给旋转
  90° 的编辑副本 → 读回 `validation_state=Trusted`，`parentOf` 链深 1。
  （即 T14 差距表 #6 的实测回填：ingredient 链由官方 SDK 直接可用。）

**与 WorkCam 对照**：WorkCam T05 衍生引擎的"衍生图回链"与 C2PA
ingredient 链语义同构；WorkCam 衍生水印/标注层可作为 c2pa.edited
+ ingredient(parentOf) 表达。注意 C2PA 不复制父照片字节，只带摘要+
缩略图（thumbnail），WorkCam 的"write-once 原图 + 衍生文件分离"
（T05/T06）需要保留自己的原件存储，C2PA 链不能替代。

**结论**：机制完整、SDK 可用、PoC 实证通过；映射语义清晰。

### 2.8 WorkCam 证据模型 → C2PA 映射分析

对象：`backend/app/models/capture.py::SignatureManifest`（字段与
`docs/signature-protocol.md` 一致）+ W023 发现 F1
（`origin/dev/mac-evidence-m003:Core/Security/Evidence.swift` 中
`payload()` 硬编码 `guard version == "1.1", location == nil` 与
`location=none`）。

映射表见 §4；关键差异：
1. **哈希链无对应**：C2PA 是每资产独立 manifest，无跨记录序列链
   （sequence_number/previous_record_hash 无处安放）；WorkCam 的
   "拍摄顺序防重排"证据在 C2PA 语义里必须退化为每照片独立凭证。
2. **F1 的位置映射影响**：C2PA claim 层没有 location 字段，GPS 走
   EXIF/自定义断言；而 Mac 端签名协议当前硬编码 location=none 且仅
   v1.1——任何"把 location 映入 C2PA 断言"的方案都会先撞上 W023
   F1（跨端不一致），必须先修 Mac 端 payload 才能保证 C2PA 断言与
   WorkCam 签名载荷一致（W023 的篡改向量证明单字段差异即废签名）。
3. **photo_hash 语义不同**：WorkCam 是整文件 SHA-256；C2PA hard binding
   是按容器 box 的哈希（c2pa.hash.data / BMFF.v3），嵌入 manifest 后
   文件必然变化，所以不能把"WorkCam photo_hash == C2PA hash"——
   C2PA 哈希排除 manifest box 本身；映射时应把 WorkCam photo_hash
   原样放入自定义断言（PoC 的 `org.workcam.poc.evidence` 实证），
   哈希一致性测试要按"同一原始字节"锚定（T14 已钉的不变量继续有效）。
4. **evidence_status 与 validation_state 不是一回事**：WorkCam 的
   CRYPTOGRAPHICALLY_VALID 隐含"密钥已注册"（服务器锚）；C2PA 的
   Valid 恰恰不包含签署者可信（Trusted 才含）。两者只能做语义对照
   表，不能做状态机互相翻译（见 §4 G8）。

### 2.9 文件体积与性能成本（PoC 实测）

| 场景 | 原始 | 签名后 | 增量 | 签名耗时 | 读验耗时 |
|---|---|---|---|---|---|
| 5,355 B 测试 JPEG | 5,355 B | 40,684 B | +35,329 B（+660%） | 56.2 ms | 3.6 ms |
| 4000×3000 JPEG（12MP 级） | 322,116 B | 358,447 B | **+36,331 B（+11.3%）** | 242.8 ms | 7.5 ms |
| HEIC（同内容） | 19,593 B | 23,483 B | +3,890 B（+19.9%） | 42.2 ms | 11.4 ms |
| 衍生图（含父 ingredient） | — | — | 含父链 manifest | — | — |

结论：
- **增量≈常数（35–36 KB）**：manifest 体积由证书链主导（本 PoC 仅
  2 张小测试证书；生产链更长、含 OCSP/TSA blob 时 40–60 KB 量级）。
  对 WorkCam 照片（现场 JPEG 通常 2–8 MB）是 <2% 的可接受开销；
  对小缩略图是数量级膨胀。
- **签名耗时≈载荷哈希时间**（5KB→56ms 首次调用含初始化；322KB→243ms），
  与签名算法（ES256）关系小；12MP 实拍图预估 250–400ms/张（本机
  单线程实测外推），iOS 端需真机复测（Not Implemented：无 Mac）。
- 读验（含密码学校验+信任链）为个位数到几十毫秒，验证服务侧无压力。
- TSA/OCSP 是**网络 RTT 主导**项（本沙箱直连失败已如实记录），
  现场弱网是运营风险点。

## 3. PoC 代码与实测记录

- 代码：`backend/tools/c2pa-poc/poc_c2pa.py`（10 步实验）
- 运行环境：Windows / Python 3.12.10 / c2pa-python 0.39.0
  （c2pa-rs 0.92.0）/ Pillow 13.0.0 / pillow-heif 1.9.0
- 完整真实输出：`backend/tools/c2pa-poc/README.md` 与
  `backend/tools/c2pa-poc/output/poc_results.json`
- 失败与修复轨迹（保留证据价值）：
  1. 自签叶证书 → SDK 拒绝（self-signed）
  2. CA+叶链但缺 SKI/AKI → SDK 拒绝（certificate is invalid）
  3. 补齐 SKI/AKI → 通过（与 spec §13.5.1 逐条吻合）
  4. 在线 TSA → 网络失败（SDK 原生 HTTP 不走系统代理），如实记录
  5. c2pa.actions 的 `changes` 字段误用 string → SDK 解码拒绝
     （期望 RegionOfInterest 结构），改空 actions 通过

## 4. WorkCam → C2PA 映射表

| # | WorkCam（现状） | C2PA 承接（spec 2.5 / SDK 0.92） | 判定 |
|---|---|---|---|
| M1 | ECDSA P-256 + SHA-256（DER+Base64，signature-protocol v1.0/1.1） | `C2paSigningAlg.ES256`（COSE_Sign1，CBOR 编码） | 算法一致、编码不同；两者并存互译需桥 |
| M2 | photo_hash（整文件 SHA-256） | 自定义断言 `org.workcam.evidence`（PoC 实证 `org.workcam.poc.evidence`）；C2PA hard binding 用按 box 的 c2pa.hash.data/BMFF.v3 | 语义可映射；哈希口径必须写死为"原始字节" |
| M3 | record_id / sequence_number / previous_record_hash（链） | instance_id（xmp.iid:UUID）；**无跨记录链** | record_id 可映射；链证据留 WorkCam 侧（G2） |
| M4 | device_id | 无标准 claim 字段；CAWG identity 断言（可选）或自定义断言 | 自定义断言（PoC 实证） |
| M5 | capture_timestamp / device_reported_time（ISO 8601） | actions.when（generator-lower-bound 语义）；`stds.exif.dateTimeOriginal` | 可映射；语义须声明为设备自报（T03 诚实语义） |
| M6 | location（lat/lon/accuracy/altitude 或 none） | 无 claim 字段；EXIF/GPS 断言或自定义断言 | **阻塞于 W023 F1**（Mac 硬编码 location=none/仅 v1.1），先修跨端再进断言 |
| M7 | signature + public_key（裸公钥，服务器注册） | COSE_Sign1 + x5chain（X.509 链）+ 信任列表（§13.4）/ 私有凭证库（§13.4.3） | 模型差异：中心化注册 vs 分布式信任（G4） |
| M8 | key_registry（服务器锚定） | 私有凭证库/自建 trust anchors（用户侧配置） | 最近入口；不可自动进官方 Trust List |
| M9 | 时间锚点（服务器签名回执，DIGEST_ANCHORED…） | §16.7.2 时间戳断言（事后 proof of existence）/ 签名时 TSA countersignature | 结构同构；信任源不同（自建锚 vs TSA 列表 §13.4.2） |
| M10 | evidence_status 六级 | validation_state（Trusted/Valid/Invalid…）+ validation_results 明细码 | 语义对照表可用；不可直接互译（G8） |
| M11 | 哈希链防重排/缺失检测（order/gaps） | 无对应 | 保留 WorkCam 自有机制 |
| M12 | 衍生图回链（T05） | ingredient 断言 parentOf + set_intent(EDIT)（PoC 实证） | 映射清晰 |
| M13 | claim_generator 无（自有协议） | claim_generator_info（`name`+`version`，需 org 命名规范） | 新增字段，命名待产品定 |
| M14 | JPEG 输出（CameraController） | JUMBF 嵌入 image/jpeg（PoC 实证）+ HEIC（ISO BMFF，PoC 实证） | 无阻塞 |

## 5. 差距清单（gap analysis，按优先级）

- **G1 信任模型（高，产品级）**：裸公钥+中心注册 vs X.509 链+分布式
  信任列表。需建/购 WorkCam CA 或申请内容凭证签发账户（外部依赖、
  费用、周期），T14 已标"产品决策"。
- **G2 哈希链语义缺位（高）**：C2PA 无跨资产序列链；WorkCam 的
  防重排/防缺失证据必须保留自有协议，C2PA 只做单资产凭证（映射表 M3/M11）。
- **G3 F1 位置映射阻塞（高，前置修复）**：Mac 端 payload 硬编码
  location=none + 仅 v1.1（W023 发现，本任务核实 Evidence.swift 原文）；
  不先修跨端一致，任何带位置断言的 C2PA 输出都会与 WorkCam 签名载荷
  脱钩（W023 篡改向量：单字节差异即 Invalid）。
- **G4 信任衔接（中）**：进不了官方 Trust List 的现实下，验证语义退化为
  "Valid+自建信任"；WorkCam 服务器可充当私有信任库分发点（需新增
  trust 配置服务，未实现 Not Implemented）。
- **G5 证书生命周期运营（中）**：证书过期/吊销即 manifest 失效（无 TSA
  补戳时）；OCSP stapling 与 TSA 的网络依赖在弱网现场是风险（PoC 直连
  TSA 失败实证）；WorkCam 现有锚点协议不要求证书运营，两者成本不同级。
- **G6 SDK 成熟度（中）**：全栈 0.x beta（breaking train 约两个月）、
  c2pa-swift 处于早期（from 0.0.1）、Rust MSRV 1.96.0；版本锁定与
  升级策略必须写进集成方案。
- **G7 增量与体验（低）**：+35–40KB/张对现场照片可接受；小缩略图
  膨胀数量级；12MP 签名约 0.25s（需 iPhone 真机复测，Not Implemented）。
- **G8 状态语义翻译（低）**：evidence_status 与 validation_state 只做
  并列对照，不做单向翻译；"Valid"在 C2PA 语境不等于 WorkCam 的
  CRYPTOGRAPHICALLY_VALID（信任锚语义不同，见 M10）。
- **G9 生态存活性未验证（低→待测）**：Apple 管线（Photos 编辑/分享）
  是否保留第三方 C2PA manifest 需 Mac/iOS 实测（Not Implemented）。

## 6. 对 B02-W022 集成与本轮 RC2 的建议

**C2PA 是否进入本轮 RC2：明确建议——不进入。**

理由：
1. G1（信任链建设）是外部账号+证书运营的产品级决策，不是本批次的
   工程范围；
2. G3（W023 F1）要求先改 Mac 端签名协议载荷，改载荷即触碰生产证据
   协议——与本任务红线冲突，需独立授权任务；
3. 全栈 SDK beta + c2pa-swift 早期版本，直接进 RC2 会引入升级风险；
4. PoC 已证明可行性与成本结构，材料完整，无需抢跑。

**对 B02-W022 的建议**：
1. 若 B02-W022 为证据链集成类任务，建议其范围限定为**只读支线**：
   用 c2pa-python（版本钉死 0.39.0，走离线 wheelhouse）在验证服务侧
   做"C2PA manifest 读取+验证"演示层，不写不签不改生产协议；
2. 把本报告映射表 M1–M14 与 T14 十项差距表合并为集成 spec 的附录；
3. 位置断言（M6/G3）列为 B02-W022 的显式前置依赖（修复 F1 的任务
   必须先行）；
4. 产品决策清单提交：CA 方案（自建 CA vs 内容凭证账户）、TSA 选型、
   证书轮换策略（借鉴 ADR-0001 key-lifecycle 与 offline-time-trust
   的 key_id 轮换经验）。

**后续任务建议（不在本批次）**：
- Mac/iOS 侧：c2pa-swift 依赖授权评估 + Secure Enclave→CSR→证书
  流程实测 + Apple 管线 manifest 存活性实测（G9）；
- Windows 侧：backend 验证服务可选挂 c2pa-python 只读验证（G4 的
  trust 配置服务设计）。

## 7. 信源清单（全部 2026-11-25 访问）

| 信源 | URL | 结果 |
|---|---|---|
| C2PA spec 2.5 全文 | https://spec.c2pa.org/specifications/specifications/2.5/specs/C2PA_Specification.html | 200，1,468,438 B |
| C2PA spec 站首页（版本指向） | https://spec.c2pa.org/specifications/specifications/2.2/index.html | 200（canonical → 2.5） |
| c2pa-rs README / supported-formats / context-settings | https://github.com/contentauth/c2pa-rs（raw.githubusercontent.com main 分支） | 200 |
| c2pa-python README / examples | https://github.com/contentauth/c2pa-python | 200 |
| c2pa-swift README | https://github.com/contentauth/c2pa-swift | 200 |
| c2patool 仓库元数据 | https://api.github.com/repos/contentauth/c2patool | 200（Apache-2.0） |
| PyPI c2pa-python | https://pypi.org/pypi/c2pa-python/json | 200（0.39.0） |
| PyPI 裸名 c2pa（非官方） | https://pypi.org/pypi/c2pa/json | 200（1.4.1，GPLv3） |
| 官方信任锚 | https://contentcredentials.org/trust/anchors.pem | 200（27 证书） |
| contentcredentials.org/verify | https://contentcredentials.org/verify | 200（JS 渲染，正文未提取） |
| Adobe Photoshop 文档 | https://helpx.adobe.com/photoshop/using/content-credentials.html | **403 Access Denied** |
| c2pa-rs issues（HEIC/BMFF） | https://api.github.com/search/issues?q=repo:contentauth/c2pa-rs+heic | 200（#2774 open 等） |
| W023 交付 | origin/dev/windows-b02-w023-crosslang-sig:test-vectors/crosslang/MacIntegrationGuide.md（git show） | 本地读取 |
| WorkCam signature-protocol | docs/signature-protocol.md | 本地 |
| WorkCam 时间锚点 | docs/offline-time-trust.md | 本地 |
| WorkCam 数据模型 | backend/app/models/capture.py | 本地 |
| Mac 端 F1 证据 | origin/dev/mac-evidence-m003:Core/Security/Evidence.swift（git show） | 本地 |
| T14 差异表 | docs/tech-lab/reports/t14-c2pa-diff.md | 本地 |

## 8. 未实现项（Not Implemented，如实声明）

- 未在 Mac/iOS 实机运行任何 C2PA SDK（c2pa-swift 仅文档级评估）。
- 未接入官方 C2PA Trust List 证书做真实生态验证（无生态签发方账户）。
- 未实测 Apple 照片/分享管线对 C2PA manifest 的存活性（G9）。
- 未做 c2pa-rs 依赖 crates 的全量 license 审计（cargo deny）。
- 未实现任何生产代码接线（按红线：backend 生产代码零改动）。
- 在线 TSA 补戳实测因沙箱网络策略失败，机制结论基于规范文本。
