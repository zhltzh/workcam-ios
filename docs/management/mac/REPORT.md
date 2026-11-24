# WorkCam Mac MVP 统一研发报告

2026-11-25 · 独立集成候选

## 进度和 Git

00 在干净的 managed worktree 中创建 `codex/workcam-mvp-integration`，继承 `97b0c8445793f2ed4110db84d80c540468a4b5d5`。原工作树、历史分支和已验证证据继续保留。远端 [PR #6](https://github.com/zhltzh/workcam-ios/pull/6) 和 [PR #9](https://github.com/zhltzh/workcam-ios/pull/9) 是开放草稿。#9 的头为 `4676a30afa2e7921df155a4eb0ded35b29b1a8cf`、基线为 #6 的 `f3b93307043bb54b983f4656528070ac48fa429f`。本地历史分歧提交 c1690eb 与 f3b9330 树内容一致。

01、02、03 既有会话均已收到派工并进入执行。Git 确认三个独立工作树及分支 `codex/workcam-batch02-ui`、`codex/workcam-batch02-core`、`codex/workcam-batch02-qa`。本报告的代码基线是 97b0c84，新交付以实际集成提交为准。总控分支尚无新 GitHub PR。

| 组 | 进展 |
|---|---|
| 00 | 完成远端和本地基线核对、实际派工、独立工作树核验、候选分支和统一验收基线 |
| 01 | 功能开发已启动，优先项目、照片与相机入口 |
| 02 | 核心攻关已启动，优先证据恢复硬化与密钥边界 |
| 03 | 联调和验收已启动，优先真实契约、回归和 Release 检查 |

## 任务状态

| 任务 | 状态 | 已有成果或待验收 |
|---|---|---|
| M014 | DESKTOP_VERIFIED / DEVICE_PENDING | 继承暂存恢复与原图保留回归。飞行模式、强退、重启及低存储待验 |
| M015 | DEVICE_PENDING | Secure Enclave 签名代码已存在。历史 OpenSSL 证据支持数学有效性。硬件远程来源 NOT_ATTESTED，服务端注册 Not Implemented |
| M016 | DESKTOP_VERIFIED / DEVICE_PENDING | 三模式实验室已有桌面几何和归档测试。卷尺基准、原始真机 CSV 与误差统计待验 |
| M017 | DESKTOP_VERIFIED | 继承和验证历史基线，确立分支依赖。组间增量集成仍待完成 |
| M018 | IN_PROGRESS | 完整工程取景与业务流程待开发 |
| M019 | IN_PROGRESS | 五模板水印和跨平台对拍 Not Implemented |
| M020 | IN_PROGRESS | 项目与照片离线管理待开发 |
| M021 | IN_PROGRESS | 标注和整改工作流 Not Implemented |
| M022 | IN_PROGRESS | 英文 PDF 和 ZIP 交付 Not Implemented |
| M023 | IN_PROGRESS / BLOCKED | 审查交付与真实 Windows 环境待确认，真实联调未通过 |
| M024 | DESKTOP_VERIFIED / DEVICE_PENDING | Release 调试边界、基线构建与桌面回归已检查，真机稳定性待验 |
| M025 | IN_PROGRESS | 候选分支已建立。新增业务闭环、接口兼容及候选真机验收待完成 |

## 回归与真机

97b0c84 无签名 Debug、无签名 Release 与签名 Debug 构建均成功。macOS 证据回归六组、LiDAR 桌面回归三组通过。Debug App 符号表检出 LiDAR Lab／篡改工具符号，Release App 对应检查无匹配。命令见 [研发进度](../../progress.md)。

历史 4676a30 的两份导出包、两份独立 OpenSSL 验签及一字节篡改拒绝均按原记录继承。此次枚举到已配对物理 iPhone，连接复查报 CoreDevice 4000。本轮未安装或执行候选，界面截图路径暂缺。

### iPhone 一次性操作

1. 用数据线连接并解锁 iPhone，保持前台，若出现系统信任提示请确认。保留现有应用、照片和密钥。
2. 由总控原位部署签名候选后，关闭 Wi‑Fi 并开启飞行模式，拍摄两张无个人数据的测试照片，在 Evidence 中重验并导出。
3. 强退后重开，再锁屏解锁并重开，最后重启并首次解锁后重开。每次检查原记录，复拍并比对公钥指纹。
4. 在 Debug LiDAR Lab 用卷尺建立基准，分别采集三种模式并导出 CSV。集中保存脱敏截图。
5. 在隔离的测试环境复测权限拒绝与故障场景，避免删除或改写已有证据。

## Windows 协作与下一步

固定 W014 与 W016 头 SHA 及接口缺口见 [交接记录](HANDOFF.md)。需 Windows 提供 W015、W017–W022 交付索引、审查后的后端候选 SHA、测试地址和认证迁移契约。优先集成离线项目与照片闭环、证据恢复硬化和 Release 回归，再验证跨端同步及候选端到端流程。
