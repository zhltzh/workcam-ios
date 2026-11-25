# B02-W028 商业化技术基础研究 — 技术设计文档

日期 2026-11-25 · 分支 `dev/windows-b02-w028-commerce` · 基线 `31917e3` (W013-R1)
状态：12 研究点设计完成 + 隔离 PoC 通过（10/10）；全量回归见 §9 与进度文档

---

## 0. 定位声明与硬红线（不可省略）

**本轮仅进行技术设计、风险分析及隔离 PoC。**

- **不确定产品价格**——全文不出现任何价格数字；
- **不接入真实支付**——StoreKit 只做接入边界研究（§2.7），零代码实接；
- **不擅自决定收费功能**——所有产品向取舍一律标注 `DECISION_REQUIRED`，
  本文只给工程选项、成本与风险，不给商业决定；
- 遵守 AGENTS.md：不虚构、未实现写 Not Implemented、不为"设计文档"
  引入任何依赖、不改生产路由。

设计基线纪律继承（后续各节反复引用，先集中声明）：

| 纪律 | 出处 |
|---|---|
| 原图字节不可变；任何删除策略=产品决策 | W025 设计文档 §0/§9（分支 `dev/windows-b02-w025-storage`，`git show origin/dev/windows-b02-w025-storage:docs/mvp/b02-w025-storage-design.md`） |
| 跨主体访问 404 同形（no existence oracle） | W021 审计清单 #07（分支 `dev/windows-b02-w021-security`，`git show origin/dev/windows-b02-w021-security:docs/security/b02-w021-audit-2026-11-25.md`）；实现层 `app/api/mvp.py` 模块 docstring |
| 设备吊销即时且完全 | W021 清单 #03；`app/services/device_identity.py:175-210` |
| 客户端声明不可信（client claims are DATA, not trust） | `app/db.py:479-483`（trust_notes）；W021 #06 |
| 引入任何第三方依赖需显式授权 | AGENTS.md；W019 §2.3（c2pa-swift 属授权事项的同款结论） |

---

## 1. 现状基线（全部为 31917e3 工作区可查证事实）

**身份**：`require_caller()`（`app/api/identity.py:22-27`）读 `X-Device-Token` →
`resolve_device()`（`app/services/device_identity.py:270-314`）返回 device_id；
**调用者的 token 本身即所有权键**（owner key）——`mvp_projects.owner_token`
（`app/db.py:32-43`）以及 photos / annotations / issues / derivatives /
reports / sync_ops / sync_events / photo_originals 全部业务表都以
owner_token 为归属列。当前**没有 user / account 实体**：一个自然人换一台
设备注册 = 一个新 owner，数据天然分裂在不同 token 名下。

**注册控制**：`enroll_device()`（`device_identity.py:108-172`）需要带外
enrollment code + P-256 公钥验证；token 为服务端随机 secret
（`secrets.token_hex(32)`，W012 起），同公钥重注册幂等返回同 token；
吊销后同公钥 409 拒绝复活。挑战-应答（单次 nonce + P-256 签名）与
滑窗限流（60 req/60s，`device_identity.py:386-415`）均落 SQLite。

**存储**：单 SQLite 业务库（WAL，busy_timeout 30s，`app/db.py:372-380`），
迁移 v1–v8（PRAGMA user_version，幂等，`app/db.py:88-352`）。不可变对象
内容寻址（SHA-256 即身份，`mvp_originals` + `mvp_photo_originals` 引用绑定）。

**配额**（W025 已交付、分支未合入基线）：`mvp_storage_quota(project_id,
quota_bytes, policy IN ('report_only','enforce_reject'))`，迁移 v11；
`check_quota()` 按项目**逻辑口径**评估；**强制执行 Not Implemented**——
超限状态下上传依然成功，`enforce_reject` 只是词表占位（W025 §7）。

**审计**：`mvp_audit_log`（append-only 应用日志、非防篡改；敏感字段
脱敏、token 只存 16 位指纹，`device_identity.py:59-90`）；保留能力
`prune_audit(max_age_days=90)`（`device_identity.py:93-102`）是**能力
而非已执行策略**——基线没有任何定时器调用它。

**服务形态**：FastAPI 单进程（`app/main.py:38-45`，title 自述
"NOT a production-grade authentication service"），W009 部署形态为
单实例（docs/mvp/w009-deployment.md）。

---

## 2. 十二研究点逐项设计

### 2.1 个人用户与企业用户模型（身份 / 数据归属差异）

**现状**：身份=设备 token（§1），归属=owner_token 列，无人、无组织。

**设计**：引入 `accounts` 层，token 从"身份"降级为"凭据"：

```
现:  device_token ──owns──> project ──> photos/...
目标: account ──owns──> project/workspace ──> ...
       device_token ──credential of──> account（多设备一账户）
企业: org(tenant) ──owns──> workspace ──> project（成员操作留痕 actor）
```

- **个人账户**：account 1:1 拥有其项目；数据归属=账户本人；设备凭据
  与 account 绑定（mvp_devices 加 `account_id` 列，多设备共享归属）。
  这直接修复"换设备数据分裂"的现状缺陷。
- **企业账户（org）**：项目与照片归属 **org** 而非操作者个人；
  `actor`（操作者 account）单独记录用于审计与责任划分（对齐既有
  `mvp_issue_history.actor_token` 的先例，`app/db.py:324-333`）。
  员工离职 = 吊销其 account 的成员资格与设备凭据，**数据留在 org**。

**归属语义差异表**（这是两个模型的核心差别）：

| 维度 | 个人 | 企业 |
|---|---|---|
| 资源 owner | account | org |
| 操作者 | = owner | member（≠ owner） |
| 离职/注销 | 数据随之处理（§2.10） | 数据保留，仅吊销成员资格 |
| 付费主体 | account | org（席位或容量） |

**迁移难点**：owner_token 在 20+ 处 CHECK/索引/查询里同时承担"身份"
与"归属"两个职责。演进路径必须**两步走**：先加映射层
（`owner_token -> account_id` 解析函数，读路径经映射），后改列
（新行写 account_id，旧行靠映射回填）。一次性全表改列风险不可控。

**DECISION_REQUIRED-01**：是否引入统一 account 层作为商业化前置；
旧数据（现存 owner_token 名下项目）归属映射规则（按 enrollment
对应关系还是人工认领）。

### 2.2 团队工作空间（team workspace 数据边界）

**设计**：`workspaces(workspace_id PK, tenant_id, name)`，project 挂
workspace（`mvp_projects` 加列或经 join 表）。workspace 是**成员可见性
的基本边界**（§2.8），project 是配额与幂等的基本边界（沿用 W025）。

**数据边界三要点**：

1. **行级 scope key**：所有成员可见表的查询必须带 workspace 过滤——
   本轮 PoC（§4）已验证该过滤在成员模型下依然 404 同形。
2. **物理字节共享不构成信息面**：W025 §2 已论证"拿到 sha 等于拿到
   内容，而 sha 本来就是内容的哈希"——跨 workspace 的内容去重
   （`mvp_photo_originals` 引用计数）可以保留，但 §2.8 的可见性过滤
   仍然必须在行级执行。
3. **同步游标**：`mvp_sync_events` 现按 owner_token 索引
   （`app/db.py:181-182`）；团队化后事件流要增加 workspace 维度，
   但**事件流的 owner 语义不能直接改名了事**——拉取端
   `entity_type CHECK` 约束（`app/db.py:171-173`）与既有客户端
   cursor 兼容性需要版本化协商（W013-R1 已记录"拉取侧 entity_type
   未扩展"为遗留项，团队化正好与之合并处理）。

**DECISION_REQUIRED-02**：workspace 是否个人账户也可拥有（个人
workspace = 单成员特例，推荐：是，减少分支逻辑）；workspace 数量
上限与配额层级关系（§2.4）。

### 2.3 项目成员权限（与 device-token 单设备认证的演进关系）

**现状**：`require_caller()` 返回 token，之后每个路由手写
owner 过滤（全有或全无，无角色）。

**设计**：认证与授权分两层：

```
第 1 层（不变）: X-Device-Token → resolve_device → device 凭据有效？
第 2 层（新增）: device → account（映射）→ membership(account, workspace,
                role) → 资源级判定
```

- **role 词表**：`owner / admin / member / viewer` 之类的最小集——
  **词表本身 DECISION_REQUIRED-03**（本节只给技术形态）。PoC（§4）
  采用 `owner/member/viewer` 三值验证读路径，写路径的 role 判定
  留给实现轮（PoC 范围外，如实声明）。
- **双栈共存**：过渡期 device 未绑定 account 时保持现行为（token
  即 owner），绑定后走成员判定——保证零回退（基线 632 项测试语义
  不动），新判定只在显式开启的租户上生效。
- **判定点统一**：复用现有 `require_caller` 的接线位置，在其后加
  `require_membership`；404 同形纪律保持——非成员访问一律 404 同形，
  与资源不存在不可区分（W021 #07 纪律平移）。

**风险**：owner_token 兼容路径与 account 路径并存期间，任何路由
"忘了走新层"就是越权缺口。缓解：集中判定点 + W021 式安全回归测试
逐面 pin（下一轮任务项）。

### 2.4 存储配额（衔接 B02-W025 配额模型）

W025 已交付评估层（`report_only`，强制 Not Implemented）。本节在其上
**只设计产品化维度**，不推翻其模型：

1. **配额主体扩展**：W025 是 project 级（`mvp_storage_quota` 表）；
   产品化需要层级聚合——org 配额 → workspace 汇总 → project 明细。
   实现上沿用 W025 的**逻辑口径动态计算**（不落库、避免漂移，
   W025 §2 的引用计数同款理由），在 org 层 SUM 即得，零新表。
2. **enforce 策略接线**：W025 已留 `enforce_reject` 词表占位。
   接线时机与拒绝面（上传注册？衍生图？报告生成？）是产品决策；
   工程建议拒绝点放在**照片注册**（最早的容量入口，幂等且客户端
   可预期），衍生图/报告不拦（不产生新增不可变对象）。
   **DECISION_REQUIRED-04**。
3. **订阅联动**：配额数值由 entitlement 决定（§2.5/§2.6）——
   `quota_bytes = plan_def[plan].storage_quota`，评估函数读 entitlement
   服务而非硬编码；降级（订阅到期）时**不回溯删除**，只冻结新增
   （对齐"删除=产品决策"红线，W025 §9）。
4. **计费口径声明**：W025 §5 已定双口径（物理/逻辑）。商业化文案
   必须明确"按逻辑口径计配额"（用户心智可理解），物理去重节省
   归平台——这是产品表述，DECISION_REQUIRED-05。

### 2.5 订阅权益模型（free/pro 权益项抽象）

**设计**：entitlement 与 plan 分离，**全部数值 DECISION_REQUIRED-06**：

```
plans:        plan_id -> { entitlement_key: value, ... }   # 静态定义表
subscriptions: account_id, product_id, status, expires_at, source
entitlements:  = f(active subscriptions)  # 计算视图，非物化（或物化+失效标记）
```

- **权益项键（key）建议集**（值不定）：`storage_quota_bytes`、
  `max_workspaces`、`max_team_members`、`export_formats`、
  `history_retention_days`、`report_branding`。
- **source 维度**：`iap`（App Store）/ `manual`（运营授予）/
  `promo`——决定续费/撤回路径：iap 的撤回走 Apple 通知（退款、
  订阅过期），manual/promo 走内部生命周期。
- **计算原则**：entitlement 是**订阅状态的纯函数**（多订阅并存时
  取并集/最大值——规则 DECISION_REQUIRED-07），不缓存长 TTL，使
  退款传播（§2.7）延迟可控。
- **与配额的接缝**：即 §2.4 第 3 点。

### 2.6 免费版与专业版能力控制（服务端判定设计）

**原则**（既有纪律的直接延伸）：客户端声明不可信（W021 #06 已证
客户端不能自证 trusted status；entitlement 同理）。**一切能力判定
在服务端**：

- 判定点：`require_entitlement(key)` 依赖注入，与 `require_caller`
  并列；对"动作类"能力（新建 workspace、加成员）返回 typed
  `403 entitlement_required`；对"资源可见性"仍走 404 同形（不泄漏
  资源存在）。
- **fail-closed 语义**：entitlement 服务异常时拒绝**新动作**、
  不中断**既有读**（用户已存数据不能因计费故障不可读——
  具体取舍 DECISION_REQUIRED-08，工程倾向此方案）。
- **降级行为**：订阅到期不删除数据（对齐 §2.4 第 3 点），只冻结
  超额动作；"冻结面清单"（哪些动作算超额）DECISION_REQUIRED-09。
- **feature flag 与 entitlement 的区别**（设计澄清）：flag 控制
  "功能是否上线灰度"（全局/租户级开关，运营工具）；entitlement
  控制"这个账户有没有权"（付费语义）。两者不混用同一存储。

### 2.7 Apple StoreKit 2 接入边界（只做边界研究，不接真实支付）

> **Not Implemented**：本轮零代码实接；以下为边界事实与设计约束。
> 信源状态见 §7：App Store Server API 主文档已于 2026-11-25 直接
> 抓取验证；字段级与 Connect 配置项未逐页核验，标注于 §7。

**客户端侧边界**：StoreKit 2（iOS 15+；W019 §2.3 记录本工程
Xcode/Swift 要求需与 Mac 工作流核对）。客户端完成的动作止于：
发起购买、得到 App Store 签名的 JWS 交易、把 `transactionId` +
`appAccountToken`（关联我方 account 的 UUID，由我方客户端生成并
传入 StoreKit——App Store Server API 主文档 "App Account Token"
节，已验证）上送我方服务器。**客户端上送的交易信息一律视为
待验声明，不是权益凭证**。

**服务器侧验证边界**（与 verifyReceipt 旧路线的关键差异）：
- 首选**本地验签**：App Store Server API 返回的 JWS（RFC 7515，
  主文档 Overview 原文已验证）可用 App Store 根证书链本地验证，
  不需要每次回 Apple 服务器问询——这正是"离线宽限"的支点。
- 远端问询仅在**对账/恢复**场景：Get Transaction Info / History /
  All Subscription Statuses（JWS 响应，主文档已验证）；沙盒与
  生产环境用 `environment` 字段区分，生产 4040010 后再试沙盒
  （主文档 Test using the sandbox 节已验证）。
- **服务器通知 V2**：HTTPS 回调（App Store Connect 配置），
  200-206 视为成功、40x/50x 触发重试（Notifications V2 主页
  已验证）；漏通知可用 Get Notification History 追溯 180 天
  （沙盒 30 天，主文档已验证）。退款走 Refund History +
  CONSUMPTION_REQUEST 通知（主文档已验证）。
- **API 调用鉴权**：App Store Connect 密钥签 JWT（主文档
  Authorize your API calls 节已验证）——密钥属签名秘密，
  按 AGENTS.md 纪律不入库不入日志。
- **企业席位**：Multiseat purchases / Seat management 端点族
  （Get Customer Groups、Get Group Members、Claim/Unclaim/Associate
  Seat，developer-hosted invitations；生产环境仅支持 groupType=
  ORGANIZATION——主文档已验证）与 §2.1/§2.8 的企业成员模型
  天然对应，是"团队订阅"的 Apple 官方形态。**其与我方自建
  成员模型（§2.3）的主从关系需要决策**：以 Apple 组订阅为
  权益源、我方席位映射为执行面（推荐），还是我方完全自管
  （则 Apple 侧仅按账号数买自动续订订阅）——DECISION_REQUIRED-10。

**离线宽限策略**（工作假设，接实支付前需 Apple 实测复核）：
- 服务器侧权威 entitlement 永远以本地验签的最新 JWS + 通知推送
  为准；客户端缓存自己的最近交易（StoreKit 2 Transaction
  currentEntitlements 支持离线读取——**此 API 行为细节未于本轮
  核验**，接入前以 Apple 文档/实测为准）。
- 计费重试与宽限期：Apple 对扣款失败的自动续订订阅有 billing
  retry 与可配置的 billing grace period（**具体档位未于本轮核验**，
  不写数字）；我方策略空间在于"通知未达期间如何判"——工程
  建议：本地最后已验 JWS 的 `expiresAt` 前 100% 放行；
  过期后进入**我方自定宽限窗口**（同步节奏决定上限，
  现有 sync 引擎天然周期性上送）——窗口数值
  DECISION_REQUIRED-11。
- 沙盒/生产双环境：JWS `environment` 字段区分（已验证）；
  测试通知端点 Request a Test Notification 可自证回调通路
  （已验证）。

**合规边界（外链支付）**：美国区与欧盟区的替代支付通道政策仍在
演变（Epic 案救济、DMA），**本轮不依赖任何外链支付形态**；
若未来企业版走企业直购（B2B 合同），IAP 完全可以不参与
（实体服务/企业合同的边界）——此属法律-商业判断，
DECISION_REQUIRED-12（且建议专项法律意见，工程侧只保证
entitlement 抽象（§2.5）对多支付来源开放）。

### 2.8 企业数据隔离（多项目归属 + 成员可见性隔离模型）

**设计**：三层归属链 `tenant(org) → workspace → project → 资源行`。
可见性判定=**行级过滤经 join 链**（PoC §4 的
`photos → projects → workspaces → memberships` 四链即最小形态）。

- **成员可见性两档**（词表 DECISION_REQUIRED-13）：
  a) workspace 全可见（成员见 workspace 内全部项目）；
  b) 项目级白名单（memberships 再挂 project 维度）。
  工程倾向 a) 起步（表少、判定链短、PoC 已验证），b) 按需演进。
- **404 同形纪律平移**：非成员的一切访问（跨项目、跨 workspace、
  跨租户）与不存在资源**字节同形**——PoC 已 pin（§4）。
- **审计与责任**：成员操作记 actor（§2.1），org 级审计视图
  （现有 `mvp_audit_log` 已是 append-only，加 org 维度列即可，
  不新建机制）。
- **租户间物理共享**：同 §2.2 第 2 点——内容寻址去重保留，
  行级可见性独立执行，两者不矛盾（W025 §2 结论平移到成员模型）。

### 2.9 数据导出（GDPR/CCPA 可携带性需求）

**需求锚点**：GDPR Art. 20（数据可携带权：结构化、常用、机器可读
格式）与 Art. 15（访问权）；CCPA/CPRA 有对应的 portability/know
条款（条文编号属地法律确认——**DECISION_REQUIRED-14**，此处只做
工程形态）。条文文本以官方出版物为准（§7 信源状态）。

**现状盘点**：基线的 `backup_to()`（VACUUM INTO，`app/db.py:503-515`；
W016 备份恢复验收沿用其上）是**灾难恢复整库备份**——不是按数据主
体的导出：它含全部租户全部行，直接交给用户=跨主体泄漏，**绝不能
当导出用**（这是两个必须分开的能力，设计文档显式划界）。

**设计**：
- 导出作业按 **account（个人）** 与 **org（企业整库导出）** 两种
  主体分别定义；个人导出聚合该 account 可访问范围内"其个人数据"
  （账户信息、设备凭据指纹、审计事件、其创建的标注/问题草稿）。
  **照片/项目的归属判定与 §2.10 冲突分析联动**：企业数据在
  员工账户注销时的"个人数据"边界（照片 EXIF 里的拍摄者信息算
  谁的？）DECISION_REQUIRED-15。
- 格式：JSON（元数据）+ 媒体文件逐字节（原图不可变，导出=复制
  校验和，W018 交付包的 manifest-SHA 机制可直接复用为导出清单
  格式）。
- **导出与证据链的兼容**：导出的照片带其既有 evidence/签名上下文
  的话，离线验证器（W018 `tools/evidence-verifier`，分支
  `dev/windows-b02-w018-evidence-verifier`）应仍可验——导出
  格式设计目标：**导出包本身可验**，这也是工程上的自然收敛
  （不发明第二格式）。
- 异步作业 + 就绪通知（大账户导出可能 GB 级；限速与重试复用
  sync 的幂等台账思路，不新建基础设施）。

### 2.10 账户注销（数据保留 / 删除策略——与"原图不可变"的张力，如实分析）

**冲突陈述（不回避）**：GDPR Art. 17 删除权、App 审核对账号删除
入口的要求（App Review Guidelines 5.1.1(v) 要求支持账号创建的
App 必须提供账号删除——**此为既有知识，未于本轮逐字核验**，
见 §7）与基线红线"原图不可变、删除=产品决策"（W025 §0）
**正面冲突**：工程照片可能构成个人数据（拍摄者、时间、位置、
画面内人员），删除权行使要求删除；而证据场景（施工验收、
质量责任追溯）要求照片长保留且不可篡改。

**张力分析（工程视角）**：
1. **"不可变"的本义是防篡改，不是防删除**——W025 红线表原文是
   "禁止为省空间重压缩/覆盖/自动删除"，其约束对象是**存储运营
   层**；按数据主体请求的删除是另一法域另一动作。但 W025 §9
   同时明确"任何原图删除策略=产品决策"——所以本节**不决定**，
   只列设计空间（红线纪律的正确执行方式）。
2. **法律例外与法定保留**：GDPR Art. 17(3) 有法律义务/合法利益
   抗辩空间；建设工程质量责任属地法规（各国保留年限不同）可能
   构成"必须保留"的法定义务。**这是法律判断，
   DECISION_REQUIRED-16，建议法律意见先行**。
3. **分档设计空间**（供决策参考，不定案）：
   - 个人账户注销 + 其数据为企业归属 → 只删账户/凭据/审计
     关联，企业数据保留（归属 org，§2.1）；
   - 个人账户注销 + 个人数据（个人 workspace）→ 全删或匿名化；
   - org 注销 → org 数据的保留/导出窗口期后删除。
4. **不可撤回面的如实声明**：已生成的报告 PDF、已导出的交付包、
   已发出第三方之证据材料，**技术上无法收回**——注销流程的
   用户告知文案必须如实（产品文案 DECISION_REQUIRED-17）。
5. **匿名化选项**：审计行里的 token 指纹本就不可逆
   （`device_identity.py:59-62`）；照片元数据
   （captured_at/device_id 是 client-claimed 数据，`db.py:479-483`）
   可做字段级匿名化保留（保住时间线与证据结构、剥离身份）——
   "匿名化代替删除"是否满足属地法规 DECISION_REQUIRED-16。

### 2.11 隐私数据保留策略（保留期限、匿名化）

**现状盘点**（基线事实）：

| 数据 | 现状保留 | 依据 |
|---|---|---|
| auth_challenges | TTL 60s，一次性消费 | `device_identity.py:332-342` |
| rate_limits | 滑窗自动 GC（2 窗口） | `device_identity.py:408-410` |
| audit_log | append-only；`prune_audit(90d)` 能力存在，**无任何调用方** | `device_identity.py:93-102` |
| 业务数据（项目/照片/报告） | 永久（无任何删除路径） | W025 §9（删除全部 Not Implemented） |
| 设备公钥/凭据 | 与设备同生命周期 | `mvp_devices` |

**设计**：保留策略表化（策略=数据，运营可改，不写死代码）：
每类数据一行：`retention_days / anonymize_after_days /
purpose(法律基础)`；执行器复用 prune 模式（幂等 DELETE by
cutoff）。**所有期限数值 DECISION_REQUIRED-18**（依赖 §2.10 的
法律结论）。审计日志的"非防篡改"定位（`device_identity.py:19-23`
自述）在商业化后需要重申或升级——若企业客户要求审计抗抵赖，
是独立新需求（W021 复审过的既有边界，不要在本轮顺手扩）。

**GDPR 存储限制原则**（Art. 5(1)(e)，识别/存储目的所需期限）与
工程照片证据目的的期限冲突同 §2.10，一处决策两节共用。

### 2.12 多租户架构演进（单租户 SQLite → 三选项成本与迁移风险）

**三选项对照**（以现状单 SQLite + 全表 owner_token 为起点）：

| 维度 | A. schema-per-tenant（每租户一库文件） | B. 共享 schema + tenant_id 列 | C. 独立库实例 |
|---|---|---|---|
| 隔离强度 | 文件级（最强，备份/删除天然按租户） | 行级（应用层执行，SQLite 无 RLS） | 实例级（最强运营隔离） |
| 改造量 | 低（现状即单库，路由器选文件即可） | 中（全部查询加 tenant 过滤 + 归属链列） | 中高（B 之上再加实例路由） |
| 运维成本 | 文件数随租户线性涨；跨租户运营查询要 fan-out | 单库简单；busy_timeout 30s 单写者竞争（`db.py:372-380` 现状）在最重租户影响所有人 | 每实例一套运维，成本最高 |
| SQLite 天花板 | 天然分片，写冲突隔离 | 单写者瓶颈随租户数恶化 | 同 A |
| 迁移到 Postgres | 平移困难（分库习惯） | **tenant_id 列模型直接平移**（PG RLS 还能补行级安全） | 视实现 |
| 注销/删除执行 | 删文件（快、彻底） | 批量 DELETE（慢、要 VACUUM 才回收空间） | 删实例 |

**工程评估**：
- B 是 SaaS 主流且**向 Postgres 演进时资产保留最多**；A 是高隔离
  企业档/私有部署的自然形态——**两者并不互斥**：路由器层先实现
  "租户 → 库文件"的抽象（现状单文件=一个默认租户），库内再走
  B 的 tenant_id——这就是"单租户 SQLite → 多租户"的**最小侵入
  路径**：先把 `_db_path()`（`db.py:363-369`，已是惰性解析）升级
  为 per-tenant 解析，其余不动。
- 单写者+WAL 的吞吐现实（W025 §11：~50 obj/s 摄取，瓶颈在
  每对象一事务的落盘纪律）在多租户共享库下**不会更糟**
  （瓶颈本来就是事务纪律），但长尾延迟会互相影响——
  若企业档承诺 SLA，A/C 的物理隔离才是硬保证。
- **迁移风险**：v1–v8 迁移机制（PRAGMA user_version）对 A 天然
  per-file 重放（幂等已保证，`db.py:383-415`）；B 需要一次
  "回填 tenant_id + 双写窗口 + 校验"的经典在线迁移，风险集中在
  回填一致性——用既有 `fsck()`/audit 思路做迁移校验器。

**DECISION_REQUIRED-19**：目标形态选择（推荐路线见 §3：先
抽象层+默认单租户 → 商业化上线时 B → 企业档可选 A 私有部署）。

---

## 3. 架构演进路线（文字图与阶段）

```
阶段 0（现状 31917e3）
  device_token = 身份 = 归属
  单 SQLite · 全表 owner_token · 404 同形 · 配额 report_only(W025)

阶段 1  身份分层（无支付）
  + accounts / orgs 表；mvp_devices + account_id（可空，双栈）
  + owner 解析经映射层：token -> account -> （workspace）membership
  + entitlement 键值框架（值全 free）· require_entitlement 接线
  + 审计加 actor / org 维度
  回归保障：未绑定 account 的设备行为 100% 等价基线（632 测试零回退）

阶段 2  团队空间
  + workspaces / memberships（PoC 模型产品化：techlab/tenant_isolation_poc.py 升格）
  + 404 同形纪律平移 + 安全回归逐面 pin（W021 清单 #07 扩展版）
  + sync 事件流 workspace 维度（版本化协商，兼容既有 cursor）

阶段 3  商业化接线（本轮只设计）
  + subscriptions / 通知回调 / JWS 本地验签边界（§2.7）
  + 配额 enforce 接线点（W025 词表已留位）· 降级=冻结不删除
  + 导出（§2.9）/ 注销（§2.10）/ 保留策略表（§2.11）
  + 多租户路由器抽象（§2.12：默认单租户文件，per-tenant 解析就位）

阶段 4  规模化（远期，非本轮承诺）
  SQLite -> Postgres 平移评估（B 模型 tenant_id 资产保留）
  审计抗抵赖 / 企业 SLA 物理隔离档
```

每阶段都有独立回退点：阶段 1 双栈可全关，阶段 2 未开启的租户
走阶段 1 行为，阶段 3 entitlement 全 free = 阶段 2 行为。

---

## 4. 隔离 PoC（交付记录）

**代码**：`backend/techlab/tenant_isolation_poc.py`（lab 模块，
**未接线任何生产路由**，未触碰 `app/db.py` MIGRATIONS——
零生产代码改动）。
**测试**：`backend/tests/test_techlab/test_b02_w028_tenant_poc.py`
（10 项，全部 pin 断言）。

**研究命题**：数据归属从"单设备 token"演进为"账户 + 成员关系"
（tenant → workspace → project）后，W021 #07 pin 过的
**404 同形隔离纪律是否依然成立**。

**结论：成立，且被逐项 pin**：

| # | 性质 | 测试 | 结果 |
|---|---|---|---|
| 1 | 成员可读共享 workspace 内照片 | test_member_reads_own_workspace_photo / test_member_reads_shared_workspace_photo_of_other_account | PASS |
| 2 | viewer 角色读路径成立（读权限与 role 词表解耦验证） | test_viewer_role_can_read | PASS |
| 3 | 跨 workspace（同租户）访问 = 404 同形 | test_cross_workspace_same_tenant_is_404_identical | PASS |
| 4 | 跨租户访问 = 404 同形 | test_cross_tenant_is_404_identical | PASS |
| 5 | 拒绝读与不存在资源**字节同形**（string 级相等） | test_denied_read_is_byte_identical_to_nonexistent | PASS |
| 6 | 成员资格移除**即时生效**（对齐 W021 #03 吊销纪律） | test_membership_removal_is_instant | PASS |
| 7 | 重新加入恢复访问（UPSERT 复合同一行） | test_membership_rejoin_restores_access | PASS |
| 8 | 列表查询严格按成员关系过滤，无泄漏 | test_list_scoped_to_memberships_no_leak | PASS |
| 9 | 无存在性 oracle：列表计数不随他租户数据量变化 | test_no_existence_oracle_via_counts | PASS |

命令与输出（verbatim）：

```
.venv\Scripts\python.exe -m pytest tests\test_techlab\test_b02_w028_tenant_poc.py -q
→ 10 passed in 0.11s
```

**边界声明**：PoC 只覆盖**读路径**与成员资格生命周期；写路径的
role 判定、并发迁移、与真实 `mvp_devices` 联动均为范围外
（Not Implemented，留给实现轮）。

---

## 5. 风险清单

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | owner_token 双语义（身份+归属）改造期间路由遗漏走新判定层 → 越权缺口 | 高 | 判定点集中（require_caller 后单点）+ W021 式逐面 pin 安全回归（下轮任务项） |
| R2 | 404 同形纪律在团队模型下的回归破坏（新代码走捷径） | 高 | PoC 纪律已成测试资产（§4），产品化时升格为生产回归 |
| R3 | entitlement 判定与配额评估的 fail-open（计费故障时全放行） | 高 | fail-closed 新动作（§2.6）+ 评估函数无状态化（W025 动态计算先例） |
| R4 | Apple 支付边界认知过期（政策/JWS 链/环境行为变化） | 中 | §7 标注核验日期；实接前以 sandbox 全链路实测为准（Request a Test Notification 已验证可用） |
| R5 | 注销-不可变红线冲突未决即上线收费（删不掉又必须删） | 高 | §2.10 DECISION_REQUIRED-16 法律意见前置，商业化接线（阶段 3）以该决策为门 |
| R6 | 共享库多租户的单写者竞争长尾（重租户影响轻租户） | 中 | §2.12 路由器抽象先行；企业 SLA 档走文件/实例隔离 |
| R7 | sync 游标/事件流 workspace 化破坏既有客户端兼容 | 中 | 版本化协商 + 保留 owner 维度只增不改（W014 复审确认 seq 稳定性先例） |
| R8 | 密钥资产（App Store Connect 私钥、JWT）泄漏 | 高 | AGENTS.md 不入库纪律 + 日志过滤已有（main.py SensitiveFilter）+ 密钥独立存放与轮换策略（实接轮交付） |
| R9 | 导出被当备份用（或反之）→ 跨主体数据泄漏 | 高 | §2.9 显式划界：backup=整库恢复（不含凭据分发）；export=按主体可携带——两个能力、两条路径、互不代用 |
| R10 | 多订阅/退款传播窗口内的权益状态不一致 | 中 | entitlement=纯函数（§2.5）+ 通知重试（200-206/40x 语义已验证）+ 本地 JWS 过期时间为自然上界 |

---

## 6. DECISION_REQUIRED 清单（要点汇总）

| ID | 决策点 | 相关节 |
|---|---|---|
| 01 | 是否引入统一 account 层；历史数据归属映射规则 | §2.1 |
| 02 | 个人账户是否也可拥有 workspace；workspace 数量上限 | §2.2 |
| 03 | 成员 role 词表（本节只给形态：owner/member/viewer 为 PoC 假设） | §2.3 |
| 04 | 配额 enforce 接线时机与拒绝面（建议：照片注册） | §2.4 |
| 05 | 计费口径的产品表述（逻辑口径） | §2.4 |
| 06 | free/pro 各权益项的数值 | §2.5 |
| 07 | 多订阅并存时的权益合成规则 | §2.5 |
| 08 | entitlement 服务异常时的 fail 语义（工程倾向：冻新不冻读） | §2.6 |
| 09 | 订阅降级时的冻结面清单 | §2.6 |
| 10 | 团队订阅主从：Apple Multiseat 组订阅为权益源 vs 我方自管席位 | §2.7 |
| 11 | 我方离线宽限窗口数值 | §2.7 |
| 12 | 外链支付/企业直购的法务路线（需法律意见） | §2.7 |
| 13 | 成员可见性档位（workspace 全可见 vs 项目白名单；工程倾向 a） | §2.8 |
| 14 | CCPA/CPRA 等属地条文适用性确认 | §2.9 |
| 15 | 企业数据中"个人数据"边界（员工照片归属） | §2.9 |
| 16 | 注销策略：法定保留例外、匿名化代替删除（需法律意见） | §2.10 |
| 17 | 注销流程用户告知文案（不可撤回面） | §2.10 |
| 18 | 各类数据保留期限数值 | §2.11 |
| 19 | 多租户目标形态（推荐路线见 §3/§2.12） | §2.12 |

以上 19 项**全部未决定**；本轮不擅自定任何一项。

---

## 7. 信源清单（访问状态如实记录，全部 2026-11-25）

**已直接访问验证**：
- `developer.apple.com/documentation/appstoreserverapi.md`（全文）——
  JWS/RFC 7515、JWT 授权、四语言 App Store Server Library、
  Transactions/Statuses/Refund/Consumption/Notification-History(180d/
  sandbox 30d)/Test-Notification/Order-ID/App-Account-Token、
  Multiseat 与 Seat management（production 仅 ORGANIZATION groupType）、
  renewal-date extension、sandbox URL 与 4040010 环境判定流程、
  TLS 1.2+
- `developer.apple.com/documentation/appstoreservernotifications/
  app-store-server-notifications-v2.md`（全文）——HTTPS 端点、
  200-206 成功语义、40x/50x 重试、TLS 1.2+

**访问失败/未核验（如实记录）**：
- `JWSRenewalInfoDecodedPayload.md`：页面为 JS 渲染空壳（仅标题），
  字段清单未取得
- `developer.apple.com/support/subscriptions/`：404
- billing grace period 档位 / billing retry 时长 / StoreKit 2
  currentEntitlements 离线行为：**未于本轮核验**，文中相应处已标注
- App Review Guidelines 5.1.1(v) 账号删除要求：既有知识，未逐字核验
- GDPR/CCPA 条文文本：条文编号为稳定常识引用，**文本以官方
  出版物为准**（eur-lex / 官方公报），本轮未逐条抓取

**仓库内信源**（分支内或 git show 可验）：
- `docs/mvp/w013-r1-acceptance.md`（认证现状与验收）
- `docs/ios/architecture.md`（iOS 现架构）
- `origin/dev/windows-b02-w025-storage:docs/mvp/b02-w025-storage-design.md`
- `origin/dev/windows-b02-w021-security:docs/security/b02-w021-audit-2026-11-25.md`
- `origin/dev/windows-b02-w019-c2pa:docs/tech-lab/c2pa/b02-w019-c2pa-study.md`
  （§2.3/§2.4 iOS 约束部分）
- 代码引用见各节内联（`app/db.py`、`app/api/identity.py`、
  `app/services/device_identity.py`、`app/main.py`，均带行号）

---

## 8. Not Implemented（如实声明，全文汇总）

- 订阅、支付、StoreKit 实接：**Not Implemented**（仅边界研究，§2.7）
- account / org / workspace / membership 生产表与迁移：**Not
  Implemented**（§2.1–2.3 为设计；PoC 为隔离 lab 模块，未接线）
- entitlement 判定服务 / feature flag 生产接线：**Not Implemented**
- 配额 enforce 接线：**Not Implemented**（沿用 W025 交付边界）
- 数据导出 / 账户注销 / 保留策略执行器：**Not Implemented**
- 多租户路由器（per-tenant 库解析）：**Not Implemented**
- `prune_audit` 定时执行：**Not Implemented**（能力存在，无调用方）
- 写路径 role 判定：**Not Implemented**（PoC 仅覆盖读路径）

---

## 9. 回归验证

- 基线（工作开始时）：`632 passed, 3 skipped, 1 warning in 392.07s`
- 交付后全量（含 PoC 10 项）：见进度文档
  `docs/progress/b02-w028-commerce-2026-11-25.md` 的 verbatim 输出
  （预期 642 passed / 3 skipped；PoC 模块零生产依赖，不触碰
  既有 632 项语义）。
