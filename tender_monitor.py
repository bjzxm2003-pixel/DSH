import re
import requests
from bs4 import BeautifulSoup
import hashlib
import json
import os
import time

# ==================== 配置区 ====================
BASE_URL = "https://www.chnenergybidding.com.cn/bidweb/001/001002/moreinfo.html"
KEYWORDS = ["能源", "EPC", "风电", "光伏", "煤电", "水处理"]   # 改成你的关键词
MAX_PAGES = 4
HISTORY_FILE = "history.json"
# =============================================

def fetch_page(page_num):
    if page_num == 1:
        url = BASE_URL
    else:
        url = f"https://www.chnenergybidding.com.cn/bidweb/001/001002/{page_num}.html"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        print(f"  第{page_num}页状态码: {resp.status_code}")
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("li.right-item")
        print(f"  找到 {len(items)} 个条目")
        tenders = []
        for item in items:
            link_tag = item.select_one("a.infolink")
            if not link_tag:
                link_tag = item.select_one("a[title]")
            if not link_tag:
                continue
            title = link_tag.get("title", "").strip()
            if not title:
                title = link_tag.text.strip()
            link = link_tag.get("href", "").strip()
            if link and not link.startswith("http"):
                link = "https://www.chnenergybidding.com.cn" + link
            date_tag = item.select_one("span.r")
            date = date_tag.text.strip() if date_tag else ""
            if title and link:
                tenders.append({
                    "title": title,
                    "link": link,
                    "date": date,
                    "id": hashlib.md5((title + link).encode()).hexdigest()
                })
        return tenders
    except Exception as e:
        print(f"  抓取异常: {e}")
        return []

def fetch_all_pages(max_pages):
    all_tenders = []
    for page in range(1, max_pages + 1):
        print(f"正在抓取第 {page} 页...")
        tenders = fetch_page(page)
        all_tenders.extend(tenders)
        time.sleep(1)
    return all_tenders

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(ids):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)

def filter_by_keywords(tenders):
    matched = []
    for t in tenders:
        for kw in KEYWORDS:
            if kw in t["title"]:
                matched.append(t)
                break
    return matched

def send_to_wechat(title, content):
    sendkey = os.environ.get("SCKEY")
    if not sendkey:
        print("⚠️ 未配置SCKEY，跳过推送")
        return
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    data = {"title": title, "desp": content}
    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.json().get("code") == 0:
            print("✅ 推送成功")
        else:
            print("❌ 推送失败")
    except Exception as e:
        print(f"推送异常: {e}")

def _norm_dt(s):
    """把详情页里抓到的日期片段归一化为 'YYYY-MM-DD HH:MM:SS' 或 'YYYY-MM-DD'。"""
    if not s:
        return ""
    m = re.search(r"(\d{4})[-年.](\d{1,2})[-月.](\d{1,2})[日]?\s*(\d{1,2}:\d{2}(?::\d{2})?)?", s)
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    t = m.group(4) or ""
    if t:
        p = t.split(":")
        if len(p) == 2:
            t = "%s:%s:00" % (p[0].zfill(2), p[1].zfill(2))
        else:
            t = "%s:%s:%s" % (p[0].zfill(2), p[1].zfill(2), p[2].zfill(2))
        return "%s-%s-%s %s" % (y, mo, d, t)
    return "%s-%s-%s" % (y, mo, d)


def fetch_detail(link):
    """访问招标详情页，抽取购标截止时间(buy)与开标时间(open)。
    失败或字段缺失一律返回空串（工作台侧按"未知"处理，不影响其他条目）。"""
    res = {"buy": "", "open": ""}
    if not link:
        return res
    try:
        resp = requests.get(
            link,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36"},
            timeout=20,
        )
        resp.encoding = "utf-8"
        html = resp.text
    except Exception as e:
        print("  ⚠️ 详情页抓取失败 %s: %s" % (link, repr(e)[:120]))
        return res
    text = re.sub(r"<[^>]+>", " ", html)
    # 购标截止：兼容多种叫法（购买/售卖/获取/发售 截止时间）
    for label in ("招标文件购买截止时间", "招标文件售卖截止时间", "购买招标文件截止时间",
                  "招标文件获取截止时间", "招标文件发售截止时间", "购标截止时间"):
        m = re.search(re.escape(label) + r"\s*[:：]?\s*"
                      r"(\d{4}[-年.]\d{1,2}[-月.]\d{1,2}[日]?"
                      r"(?:\s*\d{1,2}:\d{2}(?::\d{2})?)?)", text)
        if m:
            res["buy"] = _norm_dt(m.group(1))
            break
    # 开标时间（常与"投标截止时间"同行，形如"…及开标时间为YYYY-MM-DD HH:MM:SS"）
    m = re.search(r"开标时间\s*[:：]?\s*为?\s*"
                  r"(\d{4}[-年.]\d{1,2}[-月.]\d{1,2}[日]?"
                  r"(?:\s*\d{1,2}:\d{2}(?::\d{2})?)?)", text)
    if m:
        res["open"] = _norm_dt(m.group(1))
    return res


def main():
    print("=" * 50)
    print("开始抓取招标信息...")
    print(f"目标: {BASE_URL}")
    print(f"关键词: {KEYWORDS}")
    print("=" * 50)
    all_tenders = fetch_all_pages(MAX_PAGES)
    print(f"共抓取 {len(all_tenders)} 条")
    matched = filter_by_keywords(all_tenders)
    print(f"匹配关键词 {len(matched)} 条")
    # 访问每条命中项的详情页，抽取购标截止时间(buy)与开标时间(open)
    # 工作台「GitHub 待机监控」按 buy 过滤"未到购标截止日"的项目；缺失则视为未知、全显
    for t in matched:
        det = fetch_detail(t["link"])
        t["buy"] = det["buy"]
        t["open"] = det["open"]
        if det["buy"]:
            print(f"  · 购标截止 {det['buy']} ← {t['title'][:22]}")
        time.sleep(0.6)
    # 落盘完整匹配列表（路线 C：提交回仓库后作为工作台 GitHub 监控数据源，待机也能更新看板）
    fetched_at = time.strftime("%Y-%m-%d %H:%M:%S")
    matched_out = [{
        "id": t["id"],
        "title": t["title"],
        "link": t["link"],
        "date": t["date"],
        "status": "待确认",
        "buy": t.get("buy", ""),
        "open": t.get("open", ""),
        "source": "github",
        "fetchedAt": fetched_at
    } for t in matched]
    with open("matched.json", "w", encoding="utf-8") as f:
        json.dump(matched_out, f, ensure_ascii=False, indent=2)
    history_ids = load_history()
    new_tenders = [t for t in matched if t["id"] not in history_ids]
    print(f"新增 {len(new_tenders)} 条")
    if new_tenders:
        content = "## 📢 新增招标信息\n\n"
        for t in new_tenders:
            content += f"**{t['title']}**\n📅 {t['date']}\n🔗 [查看详情]({t['link']})\n\n---\n\n"
        send_to_wechat(f"招标监控提醒（{len(new_tenders)}条新增）", content)
        save_history(history_ids + [t["id"] for t in new_tenders])
        print("✅ 推送完成")
    else:
        print("ℹ️ 没有新内容")

if __name__ == "__main__":
    main()
