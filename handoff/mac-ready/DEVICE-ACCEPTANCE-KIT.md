# WorkCam 真机验收工具包（FE-K11）

**候选版本**：`dev/windows-tm-geometry` @ `4740151`（后端）
**验收对象**：Mac + iPhone 首发候选
**谁操作**：会按步骤点手机/开终端即可，不需要读任何研发代码。
**时间**：全流程约 30–40 分钟。

---

## 准备（5 分钟）

1. Mac 上打开终端（Terminal），逐行粘贴：

```bash
git clone --branch dev/windows-tm-geometry https://github.com/zhltzh/workcam-ios.git workcam-accept
cd workcam-accept/backend
bash bootstrap.sh
```

预期：最后显示 `bootstrap COMPLETE` 且全量测试通过（约 1600 项）。
**失败记录**：把终端最后的 20 行复制到 `失败记录.md`。

2. 启动后端（保持这个终端窗口开着）：

```bash
.venv/bin/uvicorn app.main:app --port 8788
```

预期：出现 `Application startup complete`。手机与 Mac 连同一 Wi-Fi，
用 `ifconfig | grep "inet "` 找到 Mac 的局域网 IP（形如 192.168.x.x）。

---

## A 组：App 连接与拍摄（10 分钟）

> 打开 WorkCam App，后端地址填 `http://<Mac的IP>:8788`，
> 注册码向开发者索取（测试码随包提供）。

| # | 操作 | 预期 | 实际结果（打勾/描述） |
|---|---|---|---|
| A1 | 输入注册码连接 | App 显示已连接，设备注册成功 | ☐ |
| A2 | 拍第一张照片 | 照片出现，屏幕显示时间/地点水印 | ☐ |
| A3 | 飞行模式后再拍 3 张 | 照片正常保存，App 显示"待同步 N 张" | ☐ |
| A4 | 关闭飞行模式 | 待同步数量开始下降到 0 | ☐ |
| A5 | 再拍一张后立刻杀掉 App 重开 | 无照片丢失 | ☐ |

## B 组：可信证据（10 分钟）

| # | 操作 | 预期 | ☐ |
|---|---|---|---|
| B1 | 拍一张 → 查看证据详情 | 显示"签名有效"或"密码学验证通过" | ☐ |
| B2 | 时间来源 | 显示"服务器锚定"或"离线推算"字样（**不应**只显示手机时间） | ☐ |
| B3 | 定位来源 | 显示定位精度与来源；没有任何"绝对可信"字样 | ☐ |
| B4 | 断网拍 1 张 → 恢复网络 | 该照片最终显示已同步，证据状态正常 | ☐ |

## C 组：工程业务（10 分钟）

| # | 操作 | 预期 | ☐ |
|---|---|---|---|
| C1 | 创建项目并选 Construction 模板 | 出现检查清单 | ☐ |
| C2 | 勾选任一检查项但**不**加照片 | App **拒绝**该项记为通过（提示需照片） | ☐ |
| C3 | 给一项加照片后标记通过 | 该项变为通过 | ☐ |
| C4 | 新建整改问题（高严重度） | 问题出现在清单 | ☐ |
| C5 | 给问题挂 Before 照片，再挂 After 照片 | 显示前后对比 | ☐ |
| C6 | 同一问题在两处同时改名（或改状态） | 后提交方收到**冲突提示**，没有静默覆盖 | ☐ |

## D 组：报告与导出（5 分钟）

| # | 操作 | 预期 | ☐ |
|---|---|---|---|
| D1 | 生成 Inspection Report | PDF 可打开，含照片与问题清单 | ☐ |
| D2 | 检查 PDF 页脚 | 每页都有"Page X of Y" | ☐ |
| D3 | 导出证据 ZIP | 得到 .evpack/.zip 文件 | ☐ |
| D4 | 用下面的验证器检查导出包 | 见 D 组验证命令 | ☐ |

---

## 导出包验证命令（D 组用）

把导出的包拷到 Mac 任意目录后运行：

```bash
cd workcam-accept/backend
.venv/bin/python tools/web_verifier.py ../docs/web-verifier
# 然后用浏览器打开 docs/web-verifier/verifier.html，
# 把导出的包拖进去 —— 应显示"file_integrity: verified"
# 且"signature_validity"为绿色。
```

**篡改自检（可选，1 分钟）**：把包里任何一张 .jpg 用预览打开改一个像素再存回去，重新拖入验证器 —— 必须显示红色 `TAMPERED`。
若未检出，记录为**严重缺陷**。

---

## E 组：LiDAR 实测（可选，10 分钟）

按 `docs/tech-lab/tm11-gnss-research-brief.md` 的 G1 场景：

1. 室外空旷处，开飞行模式
2. App 内 LiDAR 实验模式对准一面墙，从 0.5m / 1m / 2m 各测 3 次
3. 用下面的命令回传 CSV（把 <file> 换成导出的文件）：

```bash
.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from tools.tm88_gen_geometry_vectors import *; print('analyzer ready')"
```
（分析器使用说明见 `docs/tech-lab/experiments/b02-w016-lidar-experiment.md`）

---

## 失败记录模板（复制到 `失败记录.md`）

```
## 失败 #N
- 步骤编号：A3
- 手机型号 / 系统版本：
- 实际发生了什么：
- 截图/录屏：是/否
- 可复现：每次 / 偶尔 / 一次
```

---

## 红线自查（验收结论前）

- [ ] 所有"预期"栏没有出现"静默覆盖/自动通过/绝对可信"类行为
- [ ] 原图没有任何被修改的迹象（证据详情始终可重新验证）
- [ ] 冲突都弹了提示而不是悄悄选边
- [ ] 断网拍摄的照片一张都没丢
- [ ] 导出包在验证器里全绿

**全部打勾 → 首发候选通过本工具包验收。**
**任何一项不打勾 → 把失败记录发给研发，附本文件。**

---

## 给研发的回传说明

验收完成后把以下内容回传：
1. `失败记录.md`（即使全过也回传一份标"全过"）
2. 每组的打勾表照片
3. E 组 CSV（如执行）
4. 一句总结：`PASS` 或 `FAIL: <步骤编号列表>`
