# WorkCam Mac MVP 统一研发报告

2026-11-25 · 独立集成候选

## 进度和 Git

00 在干净的 managed worktree 中创建 `codex/workcam-mvp-integration`，继承 `97b0c8445793f2ed4110db84d80c540468a4b5d5`。原工作树、历史分支和已验证证据继续保留。远端 [PR #6](https://github.com/zhltzh/workcam-ios/pull/6) 和 [PR #9](https://github.com/zhltzh/workcam-ios/pull/9) 是开放草稿。#9 的头为 `4676a30afa2e7921df155a4eb0ded35b29b1a8cf`、基线为 #6 的 `f3b93307043bb54b983f4656528070ac48fa429f`。本地历史分歧提交 c1690eb 与 f3b9330 树内容一致。

01、02、03 既有会话均已收到派工并进入执行。Git 确认三个独立工作树及分支 `codex/workcam-batch02-ui`、`codex/workcam-batch02-core`、`codex/workcam-batch02-qa`。三组首批交付已集成。新分支与提交保留在本地。Git CLI 远端读取超时，总控分支尚无新 GitHub PR。

| 组 | 进展 |
|---|---|
| 00 | 完成远端和本地基线核对、实际派工、独立工作树核验、候选分支和统一验收基线 |
| 01 | 提交 253616570eecc6f00557f55d701745414ca180fd，交付英文项目、项目相机、照片浏览、编号标记、水印和 A4 PDF 原型 |
| 02 | 提交 b8f4b5aa4497aa9527236bd43e326739ebf71a46，交付签名暂存恢复硬化、异常回归与设备验收步骤 |
| 03 | 提交 2af79cbfcc2ef8266192ab80eb1bcca5c964ab3e，交付独立离线队列和 Mock 回归，真实联调待完成 |

## 任务状态

| 任务 | 状态 | 已有成果或待验收 |
|---|---|---|
| M014 | DESKTOP_VERIFIED / DEVICE_PENDING | 继承暂存恢复与原图保留，新增完整签名、缺失/不匹配 DER、目标冲突和孤立 DER 回归。飞行模式、强退、重启及低存储待验 |
| M015 | DEVICE_PENDING | Secure Enclave 签名代码已存在。历史 OpenSSL 证据支持数学有效性。硬件远程来源 NOT_ATTESTED，服务端注册 Not Implemented |
| M016 | DESKTOP_VERIFIED / DEVICE_PENDING | 三模式实验室已有桌面几何和归档测试。卷尺基准、原始真机 CSV 与误差统计待验 |
| M017 | DESKTOP_VERIFIED | 继承和验证历史基线，确立分支依赖。三组首批增量已集成，保留各组原始提交。总控接通项目与证据存储 |
| M018 | IMPLEMENTED / DEVICE_PENDING | 英文项目相机已接入统一证据保存。镜头切换、闪光灯、完整状态仍需完善 |
| M019 | IMPLEMENTED / DEVICE_PENDING | 可独立生成标明来源的水印 JPEG。五模板和 W011 几何对拍 Not Implemented |
| M020 | IMPLEMENTED / DEVICE_PENDING | 项目创建改名、离线浏览和原图分享。搜索、标签、批量规范仍待完成 |
| M021 | IMPLEMENTED / DEVICE_PENDING | 一个编号标记及备注可持久化。其余类型、整改及对比 Not Implemented |
| M022 | IMPLEMENTED / DEVICE_PENDING | A4 Site Photo PDF 可生成和分享。其余版式、证据附录、ZIP、六份样例 Not Implemented |
| M023 | DESKTOP_VERIFIED / BLOCKED | 独立 Swift outbox 通过 Mock，App 接线、认证、上传和真实同步 Not Implemented |
| M024 | DESKTOP_VERIFIED / DEVICE_PENDING | Release 调试边界、基线构建与桌面回归已检查，真机稳定性待验 |
| M025 | IN_PROGRESS | 候选分支已建立。新增业务闭环、接口兼容及候选真机验收待完成 |

## 回归与真机

97b0c84 无签名 Debug、无签名 Release 与签名 Debug 构建均成功。macOS 证据回归六组、LiDAR 桌面回归三组通过。Debug App 符号表检出 LiDAR Lab／篡改工具符号，Release App 对应检查无匹配。命令见 [研发进度](../../progress.md)。

历史 4676a30 的两份导出包、两份独立 OpenSSL 验签及一字节篡改拒绝均按原记录继承。此次枚举到已配对物理 iPhone，连接复查报 CoreDevice 4000，Xcode 将其列为离线。真机未安装或执行候选，真机截图暂缺。

### iPhone 一次性操作

1. 用数据线连接并解锁 iPhone，保持前台，若出现系统信任提示请确认。保留现有应用、照片和密钥。
2. 由总控原位部署签名候选后，关闭 Wi‑Fi 并开启飞行模式，拍摄两张无个人数据的测试照片，在 Evidence 中重验并导出。
3. 强退后重开，再锁屏解锁并重开，最后重启并首次解锁后重开。每次检查原记录，复拍并比对公钥指纹。
4. 在 Debug LiDAR Lab 用卷尺建立基准，分别采集三种模式并导出 CSV。集中保存脱敏截图。
5. 在隔离的测试环境复测权限拒绝与故障场景，避免删除或改写已有证据。

## Windows 协作与下一步

固定 W014 与 W016 头 SHA 及接口缺口见 [交接记录](HANDOFF.md)。需 Windows 提供 W015、W017–W022 交付索引、审查后的后端候选 SHA、测试地址和认证迁移契约。优先集成离线项目与照片闭环、证据恢复硬化和 Release 回归，再验证跨端同步及候选端到端流程。

### 集成候选实测

- 无签名 iOS Debug、Release 与模拟器 Debug 构建均通过。集成后证据桌面回归七组通过。LiDAR 桌面回归三组和独立 Mock 队列通过。签名 Debug 的成功记录对应继承基线，集成候选尚未执行真机安装。
- SIMULATOR_VERIFIED — iPhone 18 Pro 模拟器完成候选安装、启动与首页观察，确认 Projects 入口。截图存于本地 `/Users/zhaohui/.codex/visualizations/2026/11/24/01a20f8b-b70f-7793-b626-b9e0f03bf6b2/workcam-candidate-simulator.png`。拍摄、Secure Enclave、LiDAR 和端到端业务流程仍待真机。
- 项目拍摄使用统一证据事务，项目只保存引用。历史本地照片继续可见。签名故障仍由证据记录如实呈现。同步队列尚未接入 App。
- 集成提交顺序为 QA `953d0e7`、核心 `91946ad`、界面 `aa4b756`，随后为包含项目证据接线及本报告的分支提交。完整 SHA 可用 `git log codex/workcam-mvp-integration` 查询。
