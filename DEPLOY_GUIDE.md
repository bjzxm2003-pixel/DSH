# 招标监控 · 待机运行部署手册（路线 C）

> 目标：电脑待机/关机也能每天自动抓国能e招招标 → 推微信(Server酱) → 把结果写回本公开仓库 → 工作台（手机端/网页端）跨域读取渲染「☁️ GitHub 待机监控」看板。
> **不依赖任何 WorkBuddy 令牌**，纯 GitHub Actions + 公开仓库 JSON。

仓库：`bjzxm2003-pixel/DSH`（公开，默认分支 `main`）

---

## 一、仓库里有什么

| 文件 | 作用 |
|------|------|
| `tender_monitor.py` | 爬国能e招列表 → 关键词过滤 → 推微信 + 落盘 `matched.json` |
| `.github/workflows/monitor.yml` | 定时任务（北京 09:00 / 18:00）+ 手动触发；用内置 `GITHUB_TOKEN` 把 `matched.json`、`history.json` 提交回仓库 |
| `matched.json` | **数据源**：每次运行后由 Actions 自动提交，工作台 fetch 它 |
| `history.json` | 去重状态，跨次运行保留 |
| `requirements.txt` | Python 依赖（requests / beautifulsoup4） |
| `.gitignore` | 仅忽略 Python 缓存；**不忽略** matched.json / history.json |

---

## 二、你要在 GitHub 网页上做的 2 件事

### ① 确认仓库是 Public（最关键）
打开 `https://github.com/bjzxm2003-pixel/DSH`
- 右上角应显示 **Public**。若显示 Private：点 **Settings → 最底部 Change repository visibility → 选 Public → 确认**。
- 原因：工作台/手机端是未登录浏览器，只能跨域读取**公开**仓库的 `matched.json`。私有仓会 404。

### ② 添加 Server酱 Secret（微信推送用）
打开 `https://github.com/bjzxm2003-pixel/DSH/settings/secrets/actions`
1. 点 **New repository secret**
2. Name 填 `SCKEY`，Secret 填你的 Server酱 SendKey（形如 `SCTxxxxx`，在 https://sctapi.ftqq.com/ 后台获取）
3. **Add secret**

> 路线 C **不需要任何 WorkBuddy 令牌**。 Secrets 里只需 `SCKEY` 一项。

---

## 三、推送代码（三选一）

代码已在本机 `~/Documents/GitHub/DSH/` 准备好（已 `git add` 并提交到本地）。推上去 Actions 才生效：

**方式 A · 终端**
```bash
cd ~/Documents/GitHub/DSH
git push origin main
```

**方式 B · VS Code**
左侧「源代码管理」(`Cmd+Shift+G`) → 看到待推送提交 → 点「同步更改」云箭头。

**方式 C · GitHub Desktop**
选 `DSH` 仓库 → 上方 **Push origin**。

---

## 四、手动触发验证（确认全网通）

1. 打开 `https://github.com/bjzxm2003-pixel/DSH/actions`
2. 左侧选 **招标监控定时任务** → 右侧 **Run workflow** → 绿色按钮
3. 等 1~2 分钟，点进运行日志看：
   - `运行监控脚本` 步骤：打印 `匹配关键词 N 条` / `新增 N 条` / `✅ 推送成功`
   - `提交监控结果到仓库` 步骤：打印 `更新招标监控结果 [skip ci]`（无变化则「无变化，跳过提交」）
4. 打开 `https://github.com/bjzxm2003-pixel/DSH/blob/main/matched.json`
   - 能看到 JSON 数组 = 数据源已就绪 ✅
   - 看不到 / 404 = 检查仓库是否 Public、代码是否推送成功

---

## 五、工作台侧（已改好，指向本仓库）

工作台两个版本（`agent-hub.html` 本地版、`agent-hub-public.html` 公网版）的 `fetch` 地址已改为：
```
https://api.github.com/repos/bjzxm2003-pixel/DSH/contents/matched.json
```
- 打开工作台即自动拉取一次；招标业务模块有「☁️ GitHub 待机监控」卡片 + 「刷新云端监控」按钮。
- 公网版（手机端）要让生效，需把 `agent-hub-public.html` 用页面事务协议**原地重新发布一次**（不换链接）。

---

## 六、排错速查

| 现象 | 原因 / 处理 |
|------|------|
| 工作台显示「获取失败：HTTP 404」 | 仓库未 Public，或代码未推送，或 Actions 还没跑过一次（无 matched.json） |
| 微信没收到通知 | Secrets 缺 `SCKEY` 或 SendKey 填错；看 `运行监控脚本` 日志是否 `⚠️ 未配置SCKEY` |
| 看板永远空但 matched.json 有数据 | 旧 `.gitignore` 误忽略了 matched.json（本仓已修正）；重新跑一次 Action |
| 定时没跑 | GitHub Actions 对定时任务可能延迟几分钟；先在 Actions 页手动 Run workflow 验证 |
| 重复推送同一条 | history.json 未提交成功；确认 `提交监控结果` 步骤有 commit |

---

## 七、定时说明

`monitor.yml` 中 `cron: '0 1,10 * * *'` = **UTC 01:00 与 10:00** = **北京时间 09:00 与 18:00**。
GitHub 定时任务在负载高时可能延迟若干分钟，属正常。如需更密，改 cron 即可（注意免费额度）。
