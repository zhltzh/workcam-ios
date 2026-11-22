# WC-W005 MVP 后端集成与安全验收报告

日期：2026-11-22 · 分支：`dev/windows-integration-w005`（基于 `origin/dev/windows-tech-lab-wave2` @ `dd0dd88` + merge `origin/dev/windows-w005` @ `b2ea22d`）
全量测试：**389 passed / 0 failed**（376 组合基线 + 11 鉴权 + 2 端到端）

---

## P0：集成准备与冲突/回归报告

### 三 PR 依赖关系（复验）

| PR | 分支 | HEAD | 状态 |
|---|---|---|---|
| #7 | `dev/windows-w005` | `b2ea22d` | open，base=main，clean |
| #8 | `dev/windows-tech-lab` | `5a57c6a` | open，base=main，clean，**⊂ #10** |
| #10 | `dev/windows-tech-lab-wave2` | `dd0dd88` | open，base=`dev/windows-tech-lab`，clean |

- #8 ⊂ #10（`merge-base --is-ancestor` 复验）；#7 与 #10 **零文件交集**
- 集成分支 = `dd0dd88` + merge `b2ea22d` → **零冲突**（git 自动合并，无手工干预）

### 组合后完整测试集

- 冲突：**无**（自动合并干净）
- 回归：**无**（358 techlab + 18 W005 + 新增 13 = 389 全绿）

### API/配置/存储兼容性

| 项 | 结果 |
|---|---|
| API 面 | 纯增量（`/upload/experimental/*` + `/auth/*`）；现有 `/verify`、`/store-record`、`/time-anchor/*`、`/register-key` **零改动** |
| requirements.txt | 零变化（P2 鉴权用已有的 cryptography） |
| 数据库迁移 | 无（全文件存储）；上传 `data/uploads-experimental/` 与锚点 `data/time-anchors/` 路径互不冲突 |
| 配置 | 新增三个**默认关闭**开关：`WORKCAM_UPLOAD_EXPERIMENTAL`（端点可达）、`WORKCAM_UPLOAD_AUTH`（鉴权层）、既有 `WORKCAM_ENV=production`（硬否决，优先级最高） |

### 集成顺序与回滚方案（供审核人执行，Windows 不合并）

```text
顺序（每步可独立回滚）：
  1. PR #8  → main        （回滚：git revert 合并提交）
  2. PR #10 → main        （回滚：revert；diff 纯增量，无 rebase）
  3. PR #7  → main        （与 #10 零交集，任意时机；回滚：revert）
  4. 本 PR（#11，integration 分支）→ main
     （回滚：revert；本分支仅新增 upload_auth.py / 鉴权包装 /
       chain_joint_debug.py / 新测试，不触碰任何冻结语义）

等效合并：也可直接合本集成分支（已包含 #7+#8+#10 全部 + P1-P3），
回滚单点。两种方案均已验证测试等价。
```

## P1：可信证据链联调

### 联调脚本 `tools/tech-lab/validation/chain_joint_debug.py`（Mac 可直接执行）

```bash
# Mac（后端在 Windows 上运行；需先注册或让 Mac 自行注册测试公钥）
python3 tools/tech-lab/validation/chain_joint_debug.py \
    <真实照片.jpg> <私钥.pem> http://<win-ip>:8000 --device-id mac-iphone-001
# 真机密钥请加 --claim-se（仅记录来源声明，脚本不做硬件验证）
```

**实测输出**（桌面照片 + TEST 密钥，对运行中的后端）：

```
10/10 PASS：hash → register-key → /verify(cryptographically_valid)
→ 篡改照片拒绝 → 换公钥拒绝 → 证据包库验证 → 离线 CLI → store-record
幂等重复 → 时间锚点签发 → 回执验证(server_anchored)
```

### 红线遵守

- 输出显式打印 `密钥来源: unknown origin - TEST/joint-debug key`
- 结尾固定声明：**本脚本不构成 Secure Enclave 真机验收**
- `--claim-se` 只记录调用方声明，不做硬件验证——不存在合成密钥冒充 SE 的路径

## P2：T12 同步安全（协议语义冻结，默认关闭）

### 三层独立开关（defense in depth）

| 开关 | 默认 | 作用 |
|---|---|---|
| `WORKCAM_UPLOAD_EXPERIMENTAL=1` | 关 | 端点可达（W003-R1 既有） |
| `WORKCAM_UPLOAD_AUTH=1` | **关** | 设备鉴权+隔离+限额+审计 |
| `WORKCAM_ENV=production` | 未设 | 硬否决，**优先级高于一切** |

**任何忘记配置的组合都拒绝**（默认拒绝语义）。

### 已实现（全部有测试，11/11）

| 能力 | 实现 | 测试 |
|---|---|---|
| 设备身份 | 预注册信任表（设备公钥指纹=token）；nonce 挑战 + **P-256 签名证明**（私钥永不上行） | 挑战流/重放拒绝/错钥拒绝 |
| 设备隔离 | batch 归属创建者；越权一律 **404（不泄露归属）** | A/B 隔离测试 |
| 请求限额 | 每设备令牌桶（60 突发/30 每分钟），互不影响 | A 429 时 B 正常 |
| 审计日志 | append-only JSONL（每操作含 ts/device_id） | 内容断言 |
| 兼容性 | auth 关闭时 18 项旧端点/链路测试**原样全绿**（语义零变化）；auth 开启时全链含丢块重启恢复仍通过 | 两套 fixture |
| 协议冻结 | 路由语义、错误码、幂等行为一字未改——鉴权只是包装 | 全部 389 |

### 复验（P2 要求第 3 条）

断点续传/服务重启/重复提交在**鉴权开启**状态下由 `test_e2e_mvp.py` 全链重测通过；恶意分块语义按冻结契约钉死（见 P3）。

## P3：自动化端到端回归（DESKTOP，明确标记）

`test_e2e_mvp.py` 单文件真实链路（2.4 MB 高熵文件 = 真实相机量级）：

```
照片文件 → SHA-256 → /verify(cryptographically_valid) → 证据包落盘
→ 离线 CLI 子进程验证(overall=true) → 鉴权上传 10 分块 → 丢 5 块
→ TestClient 重启 → 续传 5 → duplicate_acked → complete 哈希一致
→ content 回流逐字节相等
```

实测日志：
```
[E2E DESKTOP CHAIN - NOT iPhone verification]
  hash: caee38b1a94a...  | verify: cryptographically_valid
  offline CLI: overall=true  | upload: 10 chunks, resumed 5 after restart
  complete: 2400000 B byte-identical
```

恶意块测试按**冻结的真实端点语义**钉死：put 时按计划 size 接受（注册时无块哈希——W003 契约），complete 时整包 sha256 **拒绝**（409）；同槽异字节 409；诚实新批量可完成。篡改字节**永远无法到达完成态**。

**桌面标记**：全部为桌面数据/软件测试密钥，不声称 iPhone 真机验收。

## 需要 Mac 配合的事项

1. **执行联调脚本**：真机照片 + SE 密钥（`--claim-se`）跑 `chain_joint_debug.py`，回传 10 步输出
2. 向量对拍（`test-vectors/tech-lab/`，交接文档 §1-§4 不变）
3. 上传协议 iPhone 客户端实现时按 `upload_auth.py` 语义：`X-Device-Token` header + `/auth/challenge`→`/auth/verify` 身份证明（若后端开了 auth）
4. 鉴权层真机复验：两台设备（或同设备不同密钥）验证隔离 404 行为

## 剩余风险

1. 鉴权信任表为文件型（`devices.jsonl`），无管理界面——**enroll 是带外操作**（设计如此，见 docstring）；生产需换持久化凭据库
2. 限额桶为进程内存（重启清零）——联合调试可接受，生产需共享存储
3. 审计日志无轮转（append-only；生产需 logrotate 类机制）
4. 全部真机项（T01/T04/链路 SE 验收）状态不变，仍 DEVICE_PENDING
