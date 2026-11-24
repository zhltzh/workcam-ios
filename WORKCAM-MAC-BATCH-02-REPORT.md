# WORKCAM-MAC-BATCH-02

2026-11-25 · 本地阶段交付

## 基线与分支

- 目标仓库为 zhltzh/workcam-ios。外层目录的 main 无提交且包含未跟踪文件。实际源仓库 WorkCam-GitHub-Sync 的 dev/mac-tech-lab 在创建 worktree 前无未提交修改。
- GitHub 核对结果显示 PR #6 和 #9 均为 open draft。#9 以 #6 的头 f3b9330 为基线，当前头为 4676a30afa2e7921df155a4eb0ded35b29b1a8cf。本地 c1690eb 与 f3b9330 各有一个独有提交，树内容一致。f3b9330 已位于 4676a30 的祖先链。推荐先审 #6，再审 #9，最后以 #9 集成后的基线审本批改动。
- 本地独立分支 codex/workcam-mac-batch-02 从 4676a30 建立。新 GitHub 分支和 PR 尚不可用。git ls-remote 因 GitHub DNS 失败，连接器可读取 PR 与分支元数据。
- 本地 WorkCam 文档未发现 M014–M025 的现有交付。邻近 wc-m011-source 的 M017 属于字典工程，未纳入。Windows 已看到 W011–W013 PR #17–#19，以及 W014、W016 分支名。候选 SHA、审查与部署状态仍需核定，分支存在不构成联调通过。
- 历史 PR [#6](https://github.com/zhltzh/workcam-ios/pull/6) 与 [#9](https://github.com/zhltzh/workcam-ios/pull/9) 的原始文件及分支均保留。历史报告记载两份真机导出、两份 OpenSSL 验签和篡改拒绝。当前批次未重跑这些真机实验。

## 逐项验收

| 任务 | 状态 | 实际进展与剩余验收 |
|---|---|---|
| WC-M014 | DESKTOP_VERIFIED / DEVICE_PENDING | 完整暂存事务经原图哈希和元数据校验后可恢复。签名事务还检查独立 DER；不完整、冲突或不可读的暂存保留。DER 未写成功时不会提交签名元数据。桌面夹具验证恢复、重复执行和原图保留。低存储、锁屏、飞行模式、退出与重启实测待完成。定位和同步 Not Implemented。 |
| WC-M015 | DEVICE_PENDING | 现有 Secure Enclave P-256 与仅本机解锁可用的 Keychain 引用已审计。历史数学验签通过，本轮无新设备证据。密钥复用及重启需真机。远程照片密钥硬件来源 NOT_ATTESTED，设备注册与 App Attest 集成 Not Implemented。 |
| WC-M016 | DESKTOP_VERIFIED / DEVICE_PENDING | 复用三模式 Debug Lab 和归档检查。Windows W016 分析接口、三模式分别统计及卷尺基准采样待完成。 |
| WC-M017 | DESKTOP_VERIFIED / BLOCKED | 已核对 #6→#9 祖先关系、创建独立 worktree 并编译两配置。Windows 候选对象未获取，联合集成与冲突验证待做。 |
| WC-M018 | BLOCKED | 现有相机原型可作为基线。完整英文工程拍摄界面及验收 Not Implemented。 |
| WC-M019 | BLOCKED | W011 PR 说明已读。iOS 渲染、衍生图及几何向量对拍 Not Implemented，待获取规范与测试向量。 |
| WC-M020 | BLOCKED | 项目及照片管理、离线业务存储与 W017 兼容 Not Implemented。需确认 W017 批量操作与保留策略。 |
| WC-M021 | BLOCKED | 已定位 W013 PR。iOS 标注、问题管理及同步 Not Implemented。需 W013 EXIF 归一化规范和 W014 同步契约。 |
| WC-M022 | BLOCKED | 已定位 W012 PR。iOS PDF、ZIP 及六份样例 Not Implemented。需确认 W015 规范及样例。 |
| WC-M023 | BLOCKED | iOS 联调 Not Implemented。需经过审查的 Windows 候选与私网测试地址，验证随机 Token、迁移、吊销、恢复和幂等。 |
| WC-M024 | DESKTOP_VERIFIED / DEVICE_PENDING | LiDAR 与证据篡改工具限制在 Debug，历史证据重验与导出入口保留。稳定性与性能矩阵、真机权限和异常场景待验证。 |
| WC-M025 | BLOCKED | 本地分支为批次开发基线。MVP 候选集成、真机安装验收、截图、接口兼容及上架差距分析待完成。 |

所有任务使用上文同一工作分支与基线 SHA。已发生的代码与报告变更由该分支当前提交记录。新 PR、界面截图与新真机证据均未生成。M018–M022 的 BLOCKED 表示本次未交付，独立原型工作仍可继续。

## 真机操作清单

1. 连接并解锁 iPhone。保留现有应用、原图与 Keychain。安装候选时用同应用标识原位更新。
2. 打开飞行模式，确认 Wi‑Fi 已关闭。拍摄两张无个人数据的测试照片。打开 Evidence 并重新验证，记录公钥指纹、两张照片的哈希与状态。
3. 锁屏后解锁并重新验证。强制退出后重开，再重启手机并首次解锁后重开。复核同一记录和公钥指纹，离线再拍一张并比较公钥指纹。
4. 在系统设置暂时关闭相机权限，检查拒绝状态，再恢复。存储耗尽与写入故障安排在隔离测试环境。
5. 在 Debug LiDAR Lab 中使用卷尺建立基准。逐一采集 Raycast、Scene Depth、Smoothed Scene Depth，记录距离、光照、材质、角度和失败原因。分享 CSV 和不含敏感内容的界面截图。精度按模式独立计算。

## 验证记录

实际命令、结果与已知限制见 [docs/progress.md](docs/progress.md)。下一阶段优先执行签名候选原位部署、离线和密钥连续性实测，同时获取 Windows 固定 SHA 的交接材料并继续独立业务功能。
