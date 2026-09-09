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
    # 落盘完整匹配列表（路线 C：提交回仓库后作为工作台 GitHub 监控数据源，待机也能更新看板）
    fetched_at = time.strftime("%Y-%m-%d %H:%M:%S")
    matched_out = [{
        "id": t["id"],
        "title": t["title"],
        "link": t["link"],
        "date": t["date"],
        "status": "待确认",
        "buy": "",
        "open": "",
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
