# WorkCam Mac 联调指南（B02-W026）

版本 v1 · 分支 `dev/windows-b02-w026-api-contract` · 基线 `31917e3`（W013-R1）
配套：`b02-w026-api-reference.md`（契约）· `b02-w026-swift-models.md`（Swift 模型）· `b02-w026-mock-data.json`（真实调用的固定响应集）· `openapi.json`（机器契约）

本文取代 `docs/mvp/workcam-mac-integration-rc1.md`（该文档基于 W009 时代基线 `26e71fc`，不含 W011-W013 接口面；保留作历史记录，不作联调依据）。

## 0. 前置事实

- Windows 后端为 **原型验证服务**（`app/main.py` 自述：NOT a production-grade authentication service）。实验面（time-anchor / upload）默认关闭；生产安全由启动守卫强制。
- 联调数据为合成桌面数据（DESKTOP_SIMULATION）；真机结论由 Mac 端产出，Windows 端不代答。
- 未实现能力一律 **Not Implemented**：本基线没有照片搜索/批量管理（W017）、报告模板 v2（W015）、事件流 annotation/issue 广播与批内 local ref（W014）——均已在对应分支交付但未合入 `31917e3`。

## 1. Windows 端启动（受控内网）

```powershell
cd D:\workcam\backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
# 现场生成一次注册码（安全渠道告知 Mac 端；不进 git）
$code = -join ((48..57)+(97..122) | Get-Random -Count 24 | % {[char]$_})
Set-Content data\business\enrollment-code.txt $code
# 联调环境变量（非 production！production 下启动守卫会拒绝危险组合）
$env:WORKCAM_ENROLLMENT_CODE = $code
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

自检：

```powershell
curl http://127.0.0.1:8000/health
# → {"status":"ok","service":"workcam-verify"}
curl http://127.0.0.1:8000/openapi.json -o openapi.json
# 与 docs/api/openapi.json 的 paths/methods 应逐项一致
#（契约测试 test_openapi_paths_match_repo_copy 同款检查）
```

危险组合守卫（设计行为）：`WORKCAM_ENV=production` + `WORKCAM_UPLOAD_EXPERIMENTAL=1` 直接拒绝启动；production 未配注册码会 WARN（存量设备不受影响，新设备无法 enroll）。

## 2. 认证（F1）

Mac 端生成 P-256 密钥对（**Secure Enclave / CryptoKit**；私钥永不出设备）：

```swift
let priv = try P256.Signing.PrivateKey()   // SE 内密钥或软件密钥
let pubB64 = priv.publicKey.x963Representation.base64EncodedString()
```

注册（带外注册码）：

```bash
curl -s -X POST http://<win-ip>:8000/api/v1/devices/enroll \
  -H "Content-Type: application/json" \
  -d '{"public_key_b64":"<pubB64>","device_id":"mac-iphone-001",
       "enrollment_code":"<注册码>"}'
# → 201 {"device_token":"<64-hex>","key_fingerprint":"<16-hex>","note":...}
```

- **`device_token` 存 Keychain**，它是全部 `/api/v1` 的 `X-Device-Token` 凭据；同公钥重复 enroll 幂等返回同一 token。
- 每请求限流 60 次/60 秒（429 `rate_limited`）——联调脚本注意节流。
- 私钥持有证明（可选自检）：`POST /api/v1/devices/challenge` 拿 nonce（60s 单次）→ SE 对 nonce 签名（`.signature(for:)` + `.derRepresentation`）→ `POST /api/v1/devices/challenge/verify`。
- 吊销验证（管理员）：`POST /api/v1/devices/{token}/revoke` + `X-Admin-Code: <注册码>`；吊销后原 token 全接口 401。
- ⚠️ 旧设备（W012 前派生 token = sha256(pubB64)）会得到 401 `legacy_token_must_re_enroll`——**按提示重新 enroll 即可**，这是 W013-R1 的安全关闭行为，不是故障。

## 3. F2 项目 / 照片 / 原图

```bash
# 建项目
curl -s -X POST .../api/v1/projects -H "$AUTH" \
  -d '{"name":"Tower B - Level 3 Retrofit","description":"...","site_address":"..."}'
# → 201 project JSON（project_id = uuid4）

# 登记照片元数据（先元数据后字节；byte_size 1..100MB）
curl -s -X POST .../api/v1/projects/$PID/photos -H "$AUTH" \
  -d '{"idempotency_key":"<每照片唯一>","original_sha256":"<64-hex>",
       "original_filename":"BEFORE_N3_01.jpg","mime_type":"image/jpeg",
       "byte_size":<n>,"captured_at":"2026-11-25T09:15:00Z","device_id":"mac-iphone-001"}'
# → 201；响应 trust_notes 标注 captured_at/device_id = client-claimed, untrusted

# 传原图字节（raw body，不是 base64）
curl -s -X PUT .../api/v1/photos/$PHOTO/original -H "$AUTH" \
     --data-binary @BEFORE_N3_01.jpg
# → 200 {"photo_id","sha256","byte_size","stored","idempotent_replay","note"}

# 校验视图（storage 与 evidence 分离）
curl -s .../api/v1/photos/$PHOTO/original/integrity -H "$AUTH"
# claims.photo_authenticity_proven 恒为 false —— UI 不得越权宣称
```

要点：
- **幂等**：同 `(project, idempotency_key)` + 同内容（sha/文件名/字节数参与比对）→ 201 + `_idempotent_replay:true`；同 key 不同内容 → 409 `idempotency_conflict`。断网重试安全。
- **哈希绑定**：上传字节必须等于登记时的 `original_sha256`，否则 400 `hash_mismatch`；已绑定照片换内容 409 `binding_conflict`（证据不可变）。
- 下载校验头：`X-Original-Sha256`。

## 4. F3 证据链（verify 核心 + 链接回写）

```bash
# ① 测试面注册公钥（仅开发联调；参数在 QUERY 里，不在 body！）
curl -s -X POST ".../register-key?public_key_b64=<URL编码的pubB64>&device_id=mac-iphone-001&allow_test_registration=true"

# ② 本地签名 manifest（version 1.0|1.1；photo_hash=照片 sha256）
#    用 app/services/serialization.py 的 canonical_payload 规范化后
#    ECDSA-P256-SHA256 签名，DER → base64 填 signature 字段

# ③ 独立验证
curl -s -X POST .../verify -d '{"manifest":{...},"photo_base64":"..."}'
# → cryptographically_valid（key 已注册 + photo_hash 通过 + attestation unknown）

# ④ 防重放登记（photo_base64 必填）
curl -s -X POST .../store-record -d '{"manifest":{...},"photo_base64":"..."}'
# → {"record_id","is_new","stored",...}

# ⑤ 业务面挂证据链接 + 服务端验证回写
curl -s -X POST .../api/v1/photos/$PHOTO/evidence-links -H "$AUTH" \
  -d '{"evidence_reference":"<record_id>","evidence_type":"signature_manifest"}'
# → 201 verification_status = unverified（客户端永远不能直接提交状态）
curl -s -X POST .../api/v1/photos/$PHOTO/evidence-links/$LID/verify -H "$AUTH" \
  -d '{"manifest":{...},"photo_base64":"..."}'
# → cryptographically_valid（服务端跑真实验证后回写）
```

要点：
- 绑定守卫：`manifest.record_id` ≠ 登记引用 → 400 `reference_mismatch`；`manifest.photo_hash` ≠ 照片登记哈希 → 400 `hash_binding_mismatch`（防跨照片误绑）。
- **材料 schema 校验失败也是 200**（状态写为 `verification_failed` + failure_reason）——客户端要读状态而不是只看 HTTP 码。
- 时间锚路径（可选）：`POST /time-anchor/anchor` → **客户端自行组装完整 receipt**（补 `device_reported_start_time/end_time` + `stored_at`，anchor 响应本身不是 receipt，直接回发是 422）→ `POST /time-anchor/verify?check_storage=true` → evidence_type=time_anchor 的链接 verify 传 `{"receipt": {...}}` → `server_anchored`。

## 5. F4 水印衍生

```bash
curl -s -X POST .../api/v1/photos/$PHOTO/derivatives/watermark -H "$AUTH" \
  -d '{"template":"construction",
       "fields":{"show_project_name":true,"project_name":"Tower B",
                 "show_captured_time":true,"captured_time":"...",
                 "show_evidence_status":true,"show_photo_id":true}}'
# → 201 derivative JSON（derivative_sha256、geometry、note）
curl -s .../api/v1/derivatives/$DID/download -H "$AUTH" -o wm.jpg
```

- **必须先传原图**，否则 404 `original_not_uploaded`。
- `evidence_status` 由服务端解析**覆盖**客户端声明（W012 纪律）。
- 幂等键 `(photo, template, params-hash, original-sha)`：同参重发 `idempotent_replay:true`。
- 衍生图不是可信证据——UI 文案沿用响应 note。

## 6. F5 标注 / 问题 / 前后对比

```bash
# 标注（归一化坐标 0..1，EXIF 转置后的显示帧；≤50/照片）
curl -s -X POST .../api/v1/photos/$PHOTO/annotations -H "$AUTH" \
  -d '{"annotation_type":"rectangle","norm_x1":0.2,"norm_y1":0.2,
       "norm_x2":0.7,"norm_y2":0.6,"color_hex":"#FF3B30","stroke_width":4}'
# 更新：PATCH /api/v1/annotations/$AID + expected_revision
# （revision 不符 → 409 revision_conflict，details.current_revision 给当前值）
# 删除：DELETE = 软删；GET ...?include_inactive=true 仍可审计

# 问题
curl -s -X POST .../api/v1/projects/$PID/issues -H "$AUTH" \
  -d '{"title":"Honeycomb on column C-12","severity":"high",...}'
# 状态机：open→in_progress→resolved→closed；非法跳转 409
# invalid_status_transition（details.allowed 列合法目标）
# closed 终态不可再改（409 issue_closed）；每笔变更入 history

# 关联前后照片（一 role 一照片）
curl -s -X POST .../api/v1/issues/$IID/photos -H "$AUTH" \
  -d '{"photo_id":"$BEFORE","role":"before"}'
# 并排对比图（读两张原图，输出 JPEG 衍生）
curl -s -X POST .../api/v1/issues/$IID/comparison-image -H "$AUTH" \
  -d '{"before_photo_id":"$P1","after_photo_id":"$P2"}' -o comparison.jpg
```

⚠️ comparison 的响应头里有 `X-Comparison-Note`：**不证明同位置、不构成验收**——UI 不得弱化该表述。

## 7. F6 报告 / 交付包 / 离线同步

```bash
# 报告（快照不可变：重生成=新行）
curl -s -X POST .../api/v1/projects/$PID/reports -H "$AUTH" \
  -d '{"report_type":"inspection","page_size":"a4","per_page":2,
       "photo_ids":["$P1","$P2"],"issue_ids":["$IID"],
       "operator":"inspector-li","company_name":"..."}'
# → 201 {report_id, report_number:"WC-YYYYMMDD-XXXXXXXX", pdf_sha256, page_count,...}
curl -s .../api/v1/reports/$RID/download -H "$AUTH" -o report.pdf   # X-Report-Sha256 头核对
curl -s -X POST .../api/v1/reports/$RID/delivery-package -H "$AUTH" \
  -d '{"include_originals":true,"include_derivatives":true,"include_evidence":true}' \
  -o package.zip    # X-Package-Sha256 头核对；manifest.json 含逐文件 SHA-256

# 离线批同步（≤50 op/批，按序，逐项回执）
curl -s -X POST .../api/v1/sync/batch -H "$AUTH" \
  -d '{"operations":[
    {"op":"project_create","operation_id":"op-1",
     "payload":{"operation_id":"op-1","local_id":"lp-1","name":"Offline project"}}]}'
# → {"batch_received":1,"receipts":[...],"id_map":{"lp-1":"<server-uuid>"}}
# 下一批引用上一批回执里的 server id；同 op 重发 → duplicate（台账回放）

# 增量拉取（游标重启稳定）
curl -s ".../api/v1/sync/events?cursor=<last-seq>&limit=100" -H "$AUTH"
# → {items, cursor, has_more, limit}；has_more 时循环
```

**同步纪律**（对应 W014 §7 的基线版）：
- 队列先落本地库再上传；杀进程零丢失。
- **payload 必须重复 operation_id**（顶层 + payload 各一份，同值）——缺省是 500（已知缺口，见不一致清单）。
- 网络错误 → 同 operation_id 指数退避重发；`rejected revision_conflict` → 拉新 revision、合并、**换新 operation_id** 重排；`duplicate` 回执 UI 不得显示为"新成功"。
- **基线事件流只有 project/photo 事件**（annotation/issue 的广播是未合入的 W014 能力）；拉到 `photo/created`/`project/created` 刷新对应视图。
- 原图字节 PUT 与批同步互相独立：字节失败不影响元数据回执，同 SHA 重传幂等。

## 8. Mock 数据与离线回放

`docs/api/b02-w026-mock-data.json` 是 **70 次真实调用的完整记录**（生成器 `backend/scripts/b02_w026_generate_fixtures.py`，输入即本文 F1-F6 序列）。Mac 端离线（无 Windows 主机）时：

- **解码回放**：按 `scenarios[].steps[]` 的真实响应体喂给 `b02-w026-swift-models.md` 的 Codable 模型做单元测试（每条响应都是合法 JSON，字段形态即服务端真实形态）。
- **断言校准**：`response.status` 与 `response.body` 可直接作为联调期望值（值随机、结构冻结——契约测试在 Windows 侧每跑一次就重新证明一次）。
- 二进制端点记录为 `__binary__`（sha256 + 字节数 + media_type），不内嵌字节。

mock 文件里的 `device_token_used_in_fixtures` 是生成时的随机 token，**对现网无效**——真实联调一律走 §2 的 enroll。

## 9. 契约自检（Windows 侧）

```powershell
cd backend
.venv\Scripts\python -m pytest tests\test_b02_w026_contract.py -q   # 13 passed
.venv\Scripts\python -m pytest -q                                   # 645 passed / 3 skipped
# 任何接口/字段变更必须同步更新：
#   scripts/b02_w026_export_openapi.py → docs/api/openapi.json
#   scripts/b02_w026_generate_fixtures.py → docs/api/b02-w026-mock-data.json
# 契约测试会拦住忘记更新的文档漂移。
```

## 10. 已知坑速查（联调实测得出）

| # | 现象 | 原因/对策 |
|---|---|---|
| 1 | sync op 提交返回 500 | payload 里没带 `operation_id`（§7 纪律） |
| 2 | `/register-key` 传 JSON body 没反应 | 参数在 QUERY 里（§4 ①） |
| 3 | anchor verify 422 | 直接把 anchor 响应当 receipt 回发；需补 unsigned 字段（§4） |
| 4 | `limit=99999` 得 200 不是 422 | 分页 clamp 到 100（API reference §1.4） |
| 5 | 突然全部 401 且 code=rate_limited | 60 次/60 秒限流；等待或换 token |
| 6 | 旧设备 401 legacy_token_must_re_enroll | 派生 token 已被 W013-R1 关闭；重新 enroll |
| 7 | evidence verify 返回 200 但状态 verification_failed | 材料校验失败也是"验证结果"（§4 要点） |
| 8 | project/photos 接口 404 明明存在 | 跨主体访问与不存在不可区分（无预言机）；检查 token |
| 9 | 事件流拉不到 annotation/issue | 基线事实（§7），W014 合入前属预期 |
| 10 | time-anchor batch_id 带空格没被拒 | 守卫只拒控制字符与 `=`（不一致清单 #6） |

## 11. 诚实性验收（Mac 端产出，Windows 不代答）

- [ ] `captured_at` / `device_id` UI 显示 client-claimed, untrusted 标注
- [ ] signature valid ≠ 场景真实（verify 的 summary 原样展示）
- [ ] attestation 显示 unknown (App Attest not implemented)
- [ ] 报告页保留 "reference document, not a legal certification"
- [ ] comparison 图不宣称同位置/验收
- [ ] duplicate 回执不显示为新成功
- [ ] `photo_authenticity_proven` 恒 false 的表述不被弱化
