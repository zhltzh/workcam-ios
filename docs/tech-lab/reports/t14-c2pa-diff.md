# T14 C2PA 桥接 vs 正式 C2PA SDK — 差异核对表（P1 审核交付）

基线：`backend/techlab/c2pa_bridge.py`（桥接 PoC）vs C2PA 规范
（Content Credentials，规范 2.x）与官方 SDK（c2pa-rs / c2pa-swift）。

## 定位声明（不可省略）

本仓库实现的是**断言映射的一致性协议**：把 WorkCam 清单字段映射为
C2PA 标准断言名并钉死跨系统哈希不变量。它**不是** C2PA 标准合规实现，
产出的 JSON 也不能被任何 C2PA 验证器读取——那些能力需要下表未实现
的部分。任何文档不得把桥接 PoC 描述为"C2PA 兼容"。

## 差异核对表

| # | 正式 C2PA 组成 | 桥接 PoC 状态 | 差距说明 |
|---|---|---|---|
| 1 | Manifest Store：JUMBF 容器嵌入 JPEG/PNG | ❌ 未实现 | 桥接输出独立 JSON，不写入媒体字节。嵌入需要 JUMBF box 序列化（c2pa-rs 内部格式） |
| 2 | 断言编码：CBOR（含标签/去重/URI 引用） | ❌ 未实现（JSON 代替） | JSON 仅为 PoC 合同；正式编码是 CBOR，字段名大小写和排序规则不同 |
| 3 | 签名：COSE（CBOR Object Signing）+ X.509 证书链 | ❌ 未实现 | 本仓库签名是 P-256 ECDSA + PEM 公钥（自有协议）。COSE 需要 c2pa-rs；证书链需要内容凭证颁发方账户（任务红线：外部账号依赖，未做） |
| 4 | `stds.exif.dateTimeOriginal` 等断言名 | ✅ 已按规范名映射 | 桥接映射表 `C2PA_ASSERTION_ACTIONS` 使用的动作名与规范一致（见下"映射逐条核对"） |
| 5 | `digitalSourceType` / `stds.hash.data` 哈希断言 | ⚠️ 部分 | 桥接用自定义动作键 `c2pa.hash.data` 承载 WorkCam photo_hash 并钉死一致性；正式规范的哈希出现在 ingredient/`hash` 结构里，字段路径不同，集成时需改名挂接 |
| 6 | Ingredient（被引用素材链） | ❌ 未实现 | 桥接只映射单记录；正式 C2PA 通过 ingredient 递归引用衍生链（对应我们 T05 衍生图回链，映射方案已想好但未实现） |
| 7 | claim_generator 字符串格式 | ⚠️ 近似 | 规范要求 `org/tool version` 形态；桥接用自由文本 `WorkCam tech-lab bridge v1`，集成时按 `zhltzh/WorkCam <ver>` 改写 |
| 8 | 时间戳断言 trust chain | ❌ 未实现 | 我们的 capture_timestamp 是设备报告时间（T03 已声明不可信）；正式 C2PA 也不背书时间真实性，但签署时间需在 COSE 签名层出现 |
| 9 | 验证器（Firefox/Verify.app）可读 | ❌ 不可读 | 桥接 JSON 无 JUMBF/CBOR/COSE，第三方验证器无法识别——**这是定位声明的核心依据** |
| 10 | 撤销与更新（timestamp 重签） | ❌ 未实现 | 超出 PoC 范围 |

## 映射逐条核对（桥接 → C2PA 断言名）

| WorkCam 字段 | 桥接动作名 | 正式规范对应 | 一致性判定 |
|---|---|---|---|
| photo_hash | c2pa.hash.data | manifest 内 `hash`（CBOR） | 名称自拟，语义对应；集成时改名挂入 hash 结构 |
| capture_timestamp | stds.exif.dateTimeOriginal | `stds.exif.dateTimeOriginal` ✓ | 名称一致 |
| device_reported_time | stds.exif.dateTimeDigitized | `stds.exif.dateTimeDigitized` ✓ | 名称一致 |
| device_id | stds.device.deviceID | 规范动作（新增中）名称待核 | c2pa-rs 最新动作字典里 deviceID 名称与规范文本需在集成时按 SDK 版本核对 |
| location (T02) | stds.exif.gps | `stds.exif.gps` 断言集 ✓ | 名称一致；**注意**：桥接只映射坐标值，不映射 T02 的 source/grade 诚实语义——集成时必须把 manual 标记带入断言，否则 C2PA 会把人工坐标当 GPS 呈现 |

## 集成路径（Mac 侧，待任务授权）

1. c2pa-swift（c2pa-rs 绑定）进 iOS 工程依赖评估（AGENTS.md 规则：不
   擅自加依赖，需显式授权）
2. 证书链：内容凭证颁发方注册（外部账号，产品阶段决策）
3. 断言层：**复用** 桥接的映射表与一致性测试（`test_c2pa_bridge` 的
   哈希不变量测试直接改造成 SDK 输出的回归测试）
4. T02 诚实语义必须进断言：manual 位置在 C2PA 断言里同样不得呈现为
   验证坐标

## 结论

桥接的价值是**断言语义先于容器**：在无法注册证书链之前，先把"哪些
字段以什么名字进 C2PA"钉死并被自动化测试锁定。正式集成时，容器/编码/
签名三层由 c2pa-swift 提供，断言层按本核对表移植，映射测试继续有效。
