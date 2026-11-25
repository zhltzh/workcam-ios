# WorkCam 公开证据验证 CLI（B02-W018）

无需 WorkCam 账号、无需服务器、**默认完全离线**的证据包验证工具。
照片只在本机做哈希与签名数学运算，**从不上传**。

由 tech-lab T13/T18 独立验签器（`tools/tech-lab/validation/evidence_verifier.py`
保留不动）演进而来的正式 CLI，新增：ZIP 安全解包（防 zip-slip 路径穿越、
炸弹防护、大小写冲突检测）、批量验证、六层级信任报告、严格退出码契约。

## 依赖

Python 3.12+；依赖只来自 `backend/requirements.txt`（pydantic、cryptography，
纯本地，无网络调用）：

```bash
python -m pip install -r backend/requirements.txt
```

## 用法（Windows / macOS 相同命令）

```powershell
# Windows（单个目录包）
python tools\evidence-verifier\verify_evidence.py <package_dir> --json report.json

# 一个 zip 包（Mac 导出后直接验证）
python tools\evidence-verifier\verify_evidence.py site-A.zip

# 批量：目录与 zip 混验，glob 支持（PowerShell/Cmd 下由 CLI 自己展开）
python tools\evidence-verifier\verify_evidence.py pkg1 pkg2.zip site-*.zip

# 带本地信任注册表（L3 设备身份）与时间锚点公钥（L5 可信时间）
python tools\evidence-verifier\verify_evidence.py pkg.zip `
    --registry registry.json `
    --anchor-pubkey server_key.pem --anchor-key-id test-key-001
```

```bash
# macOS / Linux
python3 tools/evidence-verifier/verify_evidence.py <package_dir> --json report.json
```

可选参数：`--report report.txt`（人类可读报告另存文件）、
`--max-uncompressed-mb N`（ZIP 解压总尺寸上限，默认 1024）、
`--keep-extracted`（保留 zip 解包临时目录，调试用）。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 所有包、所有记录的签名数学验证全部通过（L3-L6 的未知态会如实报告，但不计为失败） |
| 1 | 至少一条记录或一个包验证失败（篡改 / 缺文件 / 绑定断裂 / 锚点回执验签失败 / 清单外的孤儿文件） |
| 2 | 包格式损坏（package.json 缺失或不可读 / zip 损坏 / zip-slip 路径穿越 / 不安全 entry / 解压尺寸防护触发 / 输入路径不存在）；argparse 用法错误同为 2 |

完整状态语义与报告模板见
`docs/evidence-verifier/b02-w018-report-format.md`。

## 六层级验证（逐层分开，绝不合并）

| 层 | 检查什么 | 离线可达状态 |
|---|---|---|
| L1 文件完整性 | 文件在、原图 SHA-256 与被签名哈希一致、package.json 与 manifest 的 record_id 绑定 | pass / fail / cannot_verify |
| L2 数学签名 | 公钥格式（65 字节非压缩 P-256 点）、DER 结构、精确 canonical payload 重构、ECDSA P-256 验签 | pass / fail / cannot_verify |
| L3 设备身份 | 签名公钥是否在**本地**信任注册表文件里（绝不联网查询） | registered / unregistered / not_checked |
| L4 硬件来源 | Secure Enclave 硬件密钥证明 | **not_verifiable_offline**（离线永不可证，恒如此报告） |
| L5 可信时间 | 时间锚点回执验签（可选）对比裸设备时间 | server_anchored / device_reported / fail / cannot_verify |
| L6 定位来源 | 坐标是否被签名固定；坐标来源标签（gps/manual） | signed_in_payload / none / cannot_verify |

## 诚实契约（报告措辞红线）

- 数学签名有效**只证明字节完整性与签名数学**，**不**证明照片内容真实、
  **不**证明硬件密钥来源、**不**构成任何法律效力认证。
- `cannot_verify` 永远不会被报告为通过。
- 硬件来源证明（Secure Enclave attestation）离线**无法**验证，报告中恒为
  `not_verifiable_offline`，绝不被签名有效性"顺带证明"。
- **DEVICE_PENDING**：当前仅用公开测试向量
  （`test-vectors/fixed-vectors.json` + 测试专用密钥）验证；
  Mac 真机导出包的交叉验证尚未完成，报告逐条注明。

## Mac 端接入（如何导出可验证证据包）

1. 证据包 = tech-lab T13 v1 格式的目录：`package.json` + `photos/` +
   `manifests/`（每张照片一份已签名 SignatureManifest）+ 可选
   `derivatives/`（衍生图回链）+ 可选 `receipts/`（时间锚点回执）。
   字段合同：`test-vectors/tech-lab/record-schemas.json` 与
   `backend/techlab/evidence_package.py`（构建器 `build_package()`，幂等，
   重复打包需 `force=True`）。
2. manifest 签名协议：ECDSA P-256 + SHA-256 over canonical payload
   （`WorkCam-Sig-v1` / `WorkCam-Sig-v1.1` 域分隔），跨语言字节合同见
   `test-vectors/fixed-vectors.json`。
3. 打包成 zip（两种布局均可）：
   ```bash
   # 布局 A：zip 根即包内容（推荐）
   cd <package_dir> && zip -r ../site-A.zip .
   # 布局 B：单顶层目录包裹（macOS Finder「压缩」默认布局）
   zip -r site-A.zip <package_dir>
   ```
4. 传到任何机器上运行本 CLI 即可离线验证；无需账号、不上传照片。
5. L3 设备身份检查需要接收方自备 `--registry registry.json`
   （`{"keys": [{"public_key_b64": "...", "device_id": "..."}]}`，
   本地信任决策文件）；L5 需要接收方信任的时间锚点服务器公钥
   （`--anchor-pubkey`）。两者不提供时，CLI 如实报告 not_checked /
   cannot_verify，不会因此把记录判失败，也不会把未知报成已知。

## 测试

`backend/tests/test_techlab/test_b02_w018_verifier.py`（42 项）：
有效包（目录/zip/带包裹层/公开向量）、篡改照片、缺件、坏公钥、
manifest 字段篡改、锚点无钥匙、非法 location_meta、孤儿文件、
锚点篡改、zip-slip（`../` 穿越 / 绝对路径 / 盘符 / 反斜杠）、
坏 zip、尺寸炸弹、大小写冲突、批混退出码优先级。
