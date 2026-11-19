# 兼容性矩阵 — 现有实现盘点（Wave 0 审计）

审计对象：`main @ 17d9699`、PR #4（W004 文档）、PR #6（Mac M003 Draft）、PR #7（W005+R1）。
结论：**禁止重复开发**的清单如下。

## 已实现且可用（勿重做）

| 能力 | 位置 | 质量证据 |
|---|---|---|
| P-256 ECDSA + DER + SHA-256 验签 | `backend/app/services/signature.py` | 157 项回归测试 |
| Canonical payload v1.0/v1.1（域分离、字段序、UTF-8、ISO8601、NaN/换行/等号拒绝） | `backend/app/services/serialization.py` | 同上 |
| 公钥注册 + 设备身份绑定 + 测试环境保护（`WORKCAM_ENV=production` 禁用、防覆盖、幂等） | `backend/app/services/key_registry.py`、`api/verify.py` | `test_api.py`、`test_w005_regression.py` |
| 证据分级（fully_attested…invalid 六级 + 五维独立信号） | `backend/app/models/capture.py` | 运行时断言：未注册≤signature_valid；无 App Attest 永不 fully_attested |
| 记录存储 + 重放/冲突检测 + 拒绝不占位 | `api/verify.py` `/store-record` | 幂等重提、篡改拒绝测试 |
| 时间锚点 v2（verification_status 纳入签名、key_id 轮换、原子写+回滚、损坏文件显式报错） | `backend/app/services/time_anchor.py` | `test_time_anchor.py`、`test_wc_w003_r3.py` |
| 离线链验证（顺序保持、缺口/重复/重建检测） | `backend/app/services/chain.py`、`offline_simulator.py` | 72h 模拟、链重建不可证不可变性测试 |
| 跨语言固定向量（正+负，确定性生成） | `test-vectors/fixed-vectors.json`、`backend/generate_negative_vectors.py` | 字节级幂等（sha256 两次一致） |
| 13 项 HTTP 联调自检 | `tools/integration_check.py` | 实测 13/13 PASS |
| 启动器（任意 CWD、非零退出码） | `tools/start_backend.ps1` | 实测 exit 1/exit 3 失败路径 |
| iOS 拍照原型 + 证据签名/本地验证 + `.pending-` 原子目录 | `Core/`（main, M001/M002）；`Core/Security/Evidence.swift`（PR #6） | Mac 侧编译结果以 PR 为准 |

## 部分实现（需补齐而非重做）

| 能力 | 缺口 | 归属 |
|---|---|---|
| 定位元数据 | 仅 4 数值字段；无 source/quality/模拟标记 | T02（本轮补） |
| 时间可信 | 有锚点上界；无回拨/跳变/时区异常检测 | T03（本轮补） |
| 数据完整性 | 有原子写+回滚（锚点存储）；无系统化故障注入 | T06（Wave 2） |
| 独立验证 | 有联网联调工具；无离线独立验签 CLI | T18（Wave 2） |
| 证据绑定 | 签名绑定哈希；无独立证据包文件格式 | T13（Wave 2） |

## 未实现（NOT_STARTED）

T04、T05（本轮补模型）、T07、T08、T09、T10、T11、T12、T14、T15、T16、T17。

## 协议唯一依据（防漂移）

1. `main` 上的后端代码与 `test-vectors/fixed-vectors.json`。
2. **PR #4 的 Swift 示例不作协议依据**（含已确认错误：`import SecureEnclave`、SE 签名 DER 表述等，虽有 R2/R3 修订，审核未完）。
3. Mac 端实现以 PR #6 `Evidence.swift` 为准，经 W005-2 对照与后端兼容（X9.63/DER/payload/时间戳/genesis 全通过）。
