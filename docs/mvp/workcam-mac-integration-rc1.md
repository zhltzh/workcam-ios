# WorkCam Mac 联调唯一入口（RC1）

**代码基线**：`release/workcam-mvp-rc1` @ **`26e71fc`**（包含 W005-W009 全部 Windows MVP：tech-lab、集成安全、项目/照片/证据、原图存储、离线同步、设备身份加固）
**测试基线**：**513 passed / 0 failed**（全部桌面验证；**不构成真机验收**）
**定位**：受控内网联调——不宣称生产就绪、不开放公网。

---

## 0. Mac 获取正确代码基线

```bash
git clone https://github.com/zhltzh/workcam-ios.git
cd workcam-ios
git fetch origin release/workcam-mvp-rc1
git checkout -b mac-joint-debug-rc1 origin/release/workcam-mvp-rc1
git log --oneline -1
# 必须显示：26e71fc feat(mvp): W009 security hardening & deployability
```

⚠️ **分支注意**：Windows 各开发分支（`dev/windows-mvp-w006`…`w009` 等）**不要合并**——它们的 PR（#12-#15）尚在审核中。候选分支 `release/workcam-mvp-rc1` 已包含全部内容，是唯一的集成基线。

## 1. Windows 受控内网启动

Windows 侧（Mac 连接前）：

```powershell
cd backend
# 受控注册码（现场生成一次，安全渠道告知 Mac）：
$env:WORKCAM_ENROLLMENT_CODE = "<secrets.token_urlsafe(32) 生成的串>"
$env:WORKCAM_UPLOAD_AUTH = "1"        # 远程客户端需要
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 0.0.0.0 仅限受控内网；启动时自动迁移（v5）+ 安全配置检查
# 危险组合（production + experimental）会拒绝启动——设计行为
```

Mac 连通性预检：

```bash
curl http://<win-ip>:8000/health
# → {"status": "ok", "service": "workcam-verify"}
```

## 2. 设备注册与认证

**Secure Enclave 私钥永不出硬件**——注册只提交公钥；签名验证走挑战-响应或独立证据文件。

### 2.1 生成密钥（真机）

Swift：`SecureEnclave.P256.Signing.PrivateKey()` 产生密钥；公钥导出 X9.63 65 字节 → base64。**私钥留在 Secure Enclave，不导出、不传输、不写进任何文件。**

### 2.2 注册（带外注册码）

```bash
curl -s -X POST http://<win-ip>:8000/api/v1/devices/enroll \
  -H "Content-Type: application/json" \
  -d '{"public_key_b64": "<X9.63 公钥 base64>",
       "device_id": "mac-iphone-001",
       "enrollment_code": "<现场注册码>"}'
# → {"device_token": "<64-hex>", ...}
# device_token 是全部 /api/v1 的 X-Device-Token 凭据
```

### 2.3 挑战-响应（证明私钥持有，不交出私钥）

```bash
# ① 拿 nonce
curl -s -X POST http://<win-ip>:8000/api/v1/devices/challenge \
  -H "X-Device-Token: $TOKEN"
# → {"nonce": "…", "expires_s": 60}
# ② 用 SE 对 nonce 做 P-256 ECDSA-SHA256 签名（Swift CryptoKit：
#    privateKey.signature(for: nonce.data(using:.ascii)!)，
#    再 .derRepresentation），base64 后提交：
curl -s -X POST http://<win-ip>:8000/api/v1/devices/challenge/verify \
  -H "Content-Type: application/json" \
  -d "{\"device_token\": \"$TOKEN\", \"nonce\": \"…\", \"signature_b64\": \"…\"}"
# → {"device_id": "mac-iphone-001", "verified": true}
```

## 3. iPhone 原图上传与下载

沿用 `tools/tech-lab/validation/ios_sync_sim.py`（照片字节替换为真机 JPEG）：

```bash
python3 tools/tech-lab/validation/ios_sync_sim.py \
    http://<win-ip>:8000 $TOKEN
```

或直接调 API：`PUT /api/v1/photos/{photo_id}/original`（原图字节为 body，先照片登记后字节）；下载 `GET .../original` 响应头含 `X-Original-Sha256` 可本地复核。

## 4. Secure Enclave 签名独立验证（receive-only）

Windows 侧工具 `tools/tech-lab/validation/se_signature_check.py`——**只收公钥+载荷+签名，永不收私钥**：

```bash
# Mac 用 iOS 导出（App 内分享/AirDrop 到 Windows）组装 evidence.json：
# { "mode": "se_direct",
#   "photo_path": "IMG_0001.jpg",
#   "photo_sha256": "<照片 sha256>",
#   "public_key_b64": "<SE 公钥 X9.63 base64>",
#   "signature_b64": "<SE 对 payload_hex 的 DER 签名>",
#   "payload_hex": "<被签名的原始字节 hex>",
#   "key_id": "com.workcam.se.v1", "device_id": "…" }
# Windows 侧：
python tools/tech-lab/validation/se_signature_check.py evidence.json
# 退出码 0 = 哈希一致 + 签名数学有效
# 输出始终声明：签名数学有效 ≠ Secure Enclave 真机验收（工具不做硬件验证）
```

**红线**：Mac 不提供 PEM 私钥——不存在这个入口（证据文件含私钥字段会被输入级拒绝 exit 2）。

## 5. 项目及证据关联

- 创建/查询项目：`POST/GET /api/v1/projects`（离线队列走 `POST /api/v1/sync/batch`，见 §6）
- 登记照片：`POST /api/v1/projects/{pid}/photos`（元数据；`captured_at`/`device_id` 是客户端声明，服务端标记为 untrusted）
- 证据关联：`POST /api/v1/photos/{photo_id}/evidence-links`（引用 `/verify` 系统的 record_id）→ 状态永远从 `unverified` 开始
- 服务端验证回写：`POST .../evidence-links/{lid}/verify`（提交完整签名清单材料；状态由服务端计算——`signature_valid`/`cryptographically_valid`/`verification_failed`）
- 完整性一视图：`GET /photos/{photo_id}/original/integrity`（字节完整性 ≠ 照片真实性——两个 claim 分开）

## 6. 离线同步及恢复

```bash
# 离线期间本地排队 {op, operation_id, payload}，恢复网络后：
curl -s -X POST http://<win-ip>:8000/api/v1/sync/batch \
  -H "X-Device-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"operations": [...]}'
# → 逐项回执 + id_map（local_id → server_id）
# 重发同一批 → 全部 duplicate（幂等，安全）
# 增量拉取：GET /api/v1/sync/events?cursor=<上次 cursor>
```

冲突语义：项目更新带 `expected_revision`；不匹配返回 `revision_conflict` + 当前版本（绝不最后写入覆盖）。

## 7. LiDAR 数据与证据关联的接口边界

LiDAR（tech-lab T04）与业务 MVP 是**两个层次**：

- **本轮 MVP API 不收 LiDAR 数据**——LiDAR 测量记录留在实验层（`docs/tech-lab/experiments/t04-*`）
- LiDAR 结果与照片的**唯一合法关联途径**：Mac 按实验方案采集（`t04-checklist.csv` 132 条唯一 test_id），导出 CSV 给 Windows 分析（`tools/tech-lab/validation/t04_batch_review.py`）；关联方式是**报告层引用**（photo_id 写入实验记录），不是业务库写入
- T08 AR 投影向量对拍：`test-vectors/tech-lab/t08-projection-vectors.json`（`arkit_conversion` 段）；坐标转换契约见 `docs/tech-lab/reports/mac-handoff-w003-r1.md`
- **接口边界如实声明**：照片证据链（哈希+签名+锚点）与 LiDAR 测量精度是独立验证域——前者不证明后者，反之亦然

## 8. 真机验收记录模板

每项真机实验完成后回填（存 `docs/tech-lab/experiments/<id>-results.md`）：

```markdown
## RC1 真机结果 — <实验 ID>（如 T01-SE / T04-LiDAR / 全链）

- 设备型号 / iOS 版本 / 日期：
- 代码基线：release/workcam-mvp-rc1 @ 26e71fc
- 执行步骤与实际输出：（附原始数据路径）
- 成功/失败/失败率：
- 与桌面预期的一致性：
- 密钥来源声明：Secure Enclave（由 <什么证据> 支持）/ 其他
- 结论：状态从 DEVICE_PENDING 改为
  DEVICE_VERIFIED / LIMITED（含原因）/ REJECTED（含原因）
```

**没有此报告，对应技术永远停在 DEVICE_PENDING。**

## 9. 常见错误排查（速查）

| 症状 | 处理 |
|---|---|
| 401 not enrolled | 用 §2.2 注册；或管理员 `GET /api/v1/devices/{token}`（X-Admin-Code）查状态 |
| 401 enrollment_closed | Windows 未设 `WORKCAM_ENROLLMENT_CODE` |
| 409 device_revoked | 吊销后重注册被拒——需管理员人工（设计如此） |
| 503 NON-LOOPBACK | 远程访问需 `WORKCAM_UPLOAD_AUTH=1` |
| 429 rate limit | 60 次/60 秒窗口，等待即可 |
| 服务拒绝启动（FATAL） | production + experimental 同开——移除其一（设计硬拒绝） |
| verify 后 photo_integrity 仍 false | 检查照片字节是否与登记哈希一致（HEIC 转换环节常见） |

## 10. 安全边界（如实）

设备 token=公钥指纹；吊销即时全员拒绝；跨设备 404 等价（无预言机）；挑战单次消费；XFF 默认不信任；审计为**应用日志**（非防篡改链）；限流 DB 持久；**全部桌面验证——真机结论由 Mac 产出，本候选不宣称生产就绪、不开放公网。**
