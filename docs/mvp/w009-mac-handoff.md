# WorkCam W009 Mac 交接文档

基线：`dev/windows-mvp-w009`（PR #14 HEAD `6a204a4` 之上）。全量 **510 passed / 0 failed**（492 基线 + 18 安全）。受控内网联调专用——**不宣称生产就绪、不开放公网**。

## 0. 内网联调启动（Windows 侧）

```powershell
# 管理员先注册一台设备（带外注册码）：
$env:WORKCAM_ENROLLMENT_CODE = "现场随机生成的注册码"
$env:WORKCAM_UPLOAD_AUTH = "1"        # 远程客户端需要时开
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 启动时自动执行迁移（v5）+ 配置安全检查（危险组合拒绝启动）
```

## 1. Mac 设备注册步骤

```bash
# Mac 生成设备密钥对（真机版：Secure Enclave 产生、Secure Enclave 内保存）
# 以下命令在 Mac 上跑；把公钥发给 Windows 管理员，或直接调注册 API：

curl -s -X POST http://<win-ip>:8000/api/v1/devices/enroll \
  -H "Content-Type: application/json" \
  -d '{
    "public_key_b64": "<base64(X9.63 65 字节公钥)>",
    "device_id": "mac-iphone-001",
    "enrollment_code": "<现场注册码>"
  }'
# → {"device_token": "<64-hex>", "key_fingerprint": "…"}
# 设备端安全保存 device_token —— 它是所有 /api/v1 的 X-Device-Token 凭据
```

## 2. 认证挑战与令牌验证（证明私钥持有）

```bash
# 挑战
curl -s -X POST http://…/api/v1/devices/challenge -H "X-Device-Token: $TOKEN"
# → {"nonce": "…", "expires_s": 60}

# 用设备私钥对 nonce 做 P-256 ECDSA-SHA256 签名（真机：Secure Enclave 签），
# 然后：
curl -s -X POST http://…/api/v1/devices/challenge/verify \
  -H "Content-Type: application/json" \
  -d "{\"device_token\": \"$TOKEN\", \"nonce\": \"…\", \"signature_b64\": \"…\"}"
# → {"device_id": "mac-iphone-001", "verified": true}
```

## 3. 原图上传与同步示例

沿用 W008 的 `ios_sync_sim.py`（设备 token 换成第 1 步拿到的）：

```bash
python3 tools/tech-lab/validation/ios_sync_sim.py \
    http://<win-ip>:8000 <device_token>
```

`data` 字节替换成真实 iPhone JPEG 即为真机联调（其余步骤不变）。

## 4. 常见错误排查

| 症状 | 原因 | 处理 |
|---|---|---|
| 401 "not enrolled" | token 未注册或已吊销 | 管理员查状态：`GET /api/v1/devices/{token}`（需 X-Admin-Code） |
| 401 "enrollment_closed" | 服务端未设置注册码 | Windows 设置 `WORKCAM_ENROLLMENT_CODE` |
| 403 "bad_enrollment_code" | 注册码错 | 核对现场注册码 |
| 409 "device_revoked" | 曾注册后被吊销又试图重注册 | 需管理员人工处理（设计如此——防自愈） |
| 429 "rate limit" | 超 60 次/60 秒 | 等窗口重置（1 分钟内）；持续超限查客户端循环 |
| 503 "NON-LOOPBACK" | 远程访问但未开 `WORKCAM_UPLOAD_AUTH` | Windows 侧设置 `WORKCAM_UPLOAD_AUTH=1` |
| 服务拒绝启动（FATAL） | production + experimental 同开 | 移除其中一个——这是设计中的硬拒绝 |

## 5. 完整安全边界声明（如实）

- **身份**：设备 token = 公钥 sha256；吊销即时生效、全员覆盖
- **注册**：带外注册码强制；无码默认关闭；吊销后重注册需人工
- **隔离**：项目/照片/证据/同步事件全部 owner 隔离；跨主体 404 与不存在不可区分（无存在性预言机）
- **防重放**：nonce DB 落库、单次消费、60s 过期、设备绑定
- **限流**：每设备独立、DB 持久化（重启不重置、多进程一致）
- **网络门禁**：非回环 + 无鉴权 = 503（代码强制）；XFF 默认不信任
- **审计**：结构化 + 指纹脱敏 + 保留期清理——**应用日志，非防篡改审计链**
- **配置**：production + experimental 组合在启动时 FATAL
- **未做（生产差距）**：TLS（反代承担）、操作员 RBAC（注册码兼管理员）、审计防篡改（需外部锚定）、公网部署（禁止）
- **全部验证为桌面测试**——真机结论由 Mac 产出，本系统不声称生产就绪
