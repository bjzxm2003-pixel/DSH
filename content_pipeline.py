#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内容线管线（GitHub Actions 用）：选题侦察 → LLM 成稿 → 写回 content.json

设计要点：
- 零第三方依赖（urllib 标准库），GitHub runner 直接可跑。
- LLM 走 OpenAI 兼容协议：/chat/completions。
  环境变量：LLM_API_KEY（必需，缺失则整体优雅退出）、
           LLM_BASE_URL（默认 https://api.deepseek.com）、
           LLM_MODEL（默认 deepseek-chat）
- 选题：DuckDuckGo 网页搜索（无需任何 key）→ LLM 从搜索摘要提炼 3 条选题；
  搜索失败 → 降级用 content.json 里已有选题池（取尚无成稿的最新 3 条）。
- 产出：每条选题生成一篇 1000-1500 字今日头条图文，合并进 content.json
  （drafts.silver / drafts.guoxue，按 id 去重），并把新选题也写回选题池。
- 幂等：同一选题标题已有成稿则跳过，重跑不重复生成。
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(HERE, "content.json")

TODAY = time.strftime("%Y-%m-%d")
STAMP = time.strftime("%Y%m%d%H%M%S")

LLM_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_BASE = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL") or "deepseek-chat"

VERTICALS = [
    {
        "key": "silver",
        "name": "银发康养",
        "topics_col": "topics.silver",
        "drafts_col": "drafts.silver",
        "search_queries": [
            "银发经济 轻资产 创业",
            "退休生活 爆款 中老年",
            "社区助老食堂 运营",
            "老年人 健康 误区",
            "适老化改造 子女",
        ],
        "brief": "读者是中老年人及其40-60岁子女。定位轻资产康养创业（咨询/课程/带货/社区服务），"
                 "绝不推荐重资产养老院投资。语气像懂行的朋友聊天，不贩卖焦虑、不夸大。",
    },
    {
        "key": "guoxue",
        "name": "国学自媒体",
        "topics_col": "topics",
        "drafts_col": "drafts.guoxue",
        "search_queries": [
            "国学 短视频 爆款",
            "道德经 现代解读",
            "论语 内耗 处世",
            "王阳明 心学 焦虑",
            "古诗词 情绪 共鸣",
        ],
        "brief": "读者是30-60岁普通大众。风格通俗接地气、不学术晦涩，"
                 "把经典原文落到现代人的情绪与处世上。引用原文必须确凿，写明出处。",
    },
]


def log(*a):
    print(*a, flush=True)


# ---------- LLM（OpenAI 兼容） ----------

def llm_chat(messages, temperature=0.8, max_retries=3):
    """调用 OpenAI 兼容 chat/completions，失败重试，最终失败返回 None。"""
    url = LLM_BASE + "/chat/completions"
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }).encode("utf-8")
    last_err = ""
    for i in range(max_retries):
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + LLM_KEY)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read().decode("utf-8"))
            return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            last_err = repr(e)
            log("  ⚠️ LLM 第%d次失败: %s" % (i + 1, last_err[:160]))
            time.sleep(4 * (i + 1))
    log("  ❌ LLM 调用最终失败:", last_err[:200])
    return None


def llm_json(text):
    """从 LLM 回复里抠出 JSON 数组（容忍 ```json 包裹）。"""
    if not text:
        return None
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else None
    except Exception:
        return None


# ---------- DuckDuckGo 网页搜索（无需 key） ----------

def ddg_search(query, max_results=8):
    """抓取 DuckDuckGo HTML 版搜索结果，返回 [{title, snippet}]。
    这是无 key 的最佳努力方案，偶发被限流属预期，失败返回 []。"""
    q = urllib.parse.quote(query)
    url = "https://html.duckduckgo.com/html/?q=" + q
    req = urllib.request.Request(url)
    req.add_header("User-Agent",
                   "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            html = r.read().decode("utf-8", "ignore")
    except Exception as e:
        log("  ⚠️ 搜索失败(%s): %s" % (query, repr(e)[:120]))
        return []
    out = []
    # 结果标题与摘要
    for m in re.finditer(r'result__a[^>]*>(.*?)</a>[\s\S]{0,600}?result__snippet[^>]*>(.*?)</a>', html):
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        snip = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if title:
            out.append({"title": title, "snippet": snip[:300]})
        if len(out) >= max_results:
            break
    return out


# ---------- 内容存取 ----------

def load_content():
    if os.path.exists(CONTENT):
        try:
            with open(CONTENT, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log("⚠️ content.json 解析失败，按空库处理:", e)
    return {}


def save_content(c):
    with open(CONTENT, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    log("💾 content.json 已写回")


def draft_id(seq):
    return "g%s%02d" % (STAMP, seq)


# ---------- 重复检测：避免天天写同一批爆款 ----------

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)

def norm_title(t):
    """标题归一化：去标点/空白/书名号，转小写"""
    return _PUNCT.sub("", str(t or "")).lower()

def bigrams(t):
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}

def similar(a, b):
    """判定两条标题是否同一选题：归一化后 完全相同 / 4字以上片段互相包含 /
    bigram Jaccard>0.4 / 共享4字以上实义片段（如专名「永乐大典」）"""
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 4 and na in nb:
        return True
    if len(nb) >= 4 and nb in na:
        return True
    ba, bb = bigrams(na), bigrams(nb)
    inter = ba & bb
    if inter and len(inter) / len(ba | bb) > 0.4:
        return True
    if len(na) >= 8 and len(nb) >= 8:
        common = _lcs(na, nb)
        if len(common) >= 4 and _contentful(common):
            return True
    return False

_STOP_CHARS = set("的了了吗呢吧啊呀是把是在和与就都也很又才更最不没为确保等等上下中里去来说而对于因其这那个有会能可以要想到对被让从向于由基么什怎么")

def _contentful(s):
    """片段里实义字（非虚词）至少 3 个，才算有区分度的共享片段"""
    return sum(1 for ch in s if ch not in _STOP_CHARS) >= 3

def _lcs(a, b):
    """最长公共连续子串（标题级，串短，O(n²) 可接受）"""
    best = ""
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k > len(best):
                best = a[i:i + k]
    return best

def is_dup_title(title, used):
    return any(similar(title, u) for u in used if u)

def draft_titles(v, content):
    """该板块已成稿的首行标题（去掉【】）"""
    return [str(d.get("text", "")).split("\n")[0].strip("【】")
            for d in content.get(v["drafts_col"], [])]

def used_titles(v, content):
    """已写过（草稿）+ 已选过（选题池）的全部标题，用于选题查重"""
    return draft_titles(v, content) + [
        t.get("title", "") for t in content.get(v["topics_col"], [])
        if isinstance(t, dict) and t.get("title")
    ]


# ---------- 第一步：选题 ----------

def scout_topics(v, content):
    """搜索 + LLM 提炼选题（带历史查重）。失败降级用已有选题池。来源标 github。"""
    log("\n🔍 [%s] 开始选题侦察" % v["name"])
    used = used_titles(v, content)
    # 搜索词按日期轮换：不同天用不同组合，避免天天搜到同一批结果
    qs = v["search_queries"]
    day = int(TODAY.split("-")[2])
    picked_queries = [qs[(day + i) % len(qs)] for i in range(3)]
    corpus = []
    for q in picked_queries:
        hits = ddg_search(q, max_results=6)
        log("  搜索「%s」→ %d 条结果" % (q, len(hits)))
        for h in hits:
            corpus.append("标题: %s\n摘要: %s" % (h["title"], h["snippet"]))
        time.sleep(1.5)  # 礼貌限速

    if corpus:
        hist = "\n".join("- " + u for u in used[-25:]) or "（暂无）"
        prompt = (
            "以下是今天从搜索引擎抓到的关于「%s」赛道的网页标题和摘要（抓取时间 %s）：\n\n%s\n\n"
            "请据此提炼 5 条今天最值得做的今日头条内容选题。要求：\n"
            "1. 每条含 title（8-20字，口语化有钩子）、note（格式：来源：网页搜索｜爆款理由：<为什么火、踩中什么情绪/需求、建议切入角度>）\n"
            "2. 严禁编造具体数据（阅读量/点赞数/政策数字），没把握的写「待核实」\n"
            "3. 下面是近期已写过的选题标题，新选题必须避开：禁止同题重复，也禁止只换个说法重写同一话题（如已写过《永乐大典》流散，就不能再写《永乐大典》册数）。请选全新的题材或全新的切入角度：\n%s\n"
            "4. 只输出 JSON 数组，不要其他文字：[{\"title\":\"...\",\"note\":\"...\"}]\n"
            "5. %s" % (v["name"], TODAY, "\n\n".join(corpus[:18]), hist, v["brief"])
        )
        resp = llm_chat([{"role": "user", "content": prompt}], temperature=0.7)
        arr = llm_json(resp)
        if arr:
            topics = []
            for x in arr:
                if not isinstance(x, dict) or not x.get("title"):
                    continue
                title = str(x["title"])[:40]
                # 程序级查重：与历史选题/成稿比对，且与本次批次内去重
                if is_dup_title(title, used) or is_dup_title(title, [t["title"] for t in topics]):
                    log("  · 查重剔除重复选题: %s" % title)
                    continue
                topics.append({
                    "id": "tg%s%s" % (STAMP, len(topics) + 1),
                    "title": title,
                    "stage": "灵感",
                    "note": str(x.get("note", ""))[:300] + "｜数据来源：GitHub自动搜索",
                    "created": TODAY,
                })
                if len(topics) >= 3:
                    break
            if topics:
                log("  ✅ 搜索选题 %d 条（查重后）" % len(topics))
                return topics, "搜索"
    # ---- 降级：用已有选题池（与成稿查重后，取最新 3 条） ----
    log("  ↘️ 搜索/提炼失败，降级用仓库已有选题池")
    dtitles = draft_titles(v, content)
    pool = list(reversed(content.get(v["topics_col"], [])))  # 新的在前
    picked = []
    for t in pool:
        title = t.get("title", "")
        if not title:
            continue
        if is_dup_title(title, dtitles) or is_dup_title(title, [x["title"] for x in picked]):
            continue
        picked.append({
            "id": t.get("id") or ("tg%s%d" % (STAMP, len(picked) + 1)),
            "title": title,
            "stage": "灵感",
            "note": (t.get("note", "") or "") + "｜数据来源：选题池降级",
            "created": t.get("created", TODAY),
        })
        if len(picked) >= 3:
            break
    log("  ✅ 降级选题 %d 条" % len(picked))
    return picked, "选题池降级"


# ---------- 第二步：成稿 ----------

def gen_article(v, topic):
    prompt = (
        "写一篇今日头条图文文案。要求：\n"
        "1. 主题：%s（选题背景：%s）\n"
        "2. 字数 1000-1500 字\n"
        "3. 结构：吸睛标题（第一行，用【】包裹）→ 痛点引入（2-3段扎心场景）"
        "→ 干货分点（3-5条，每条有小标题，具体可操作）→ 总结与互动引导（结尾抛开放问题）\n"
        "4. 分段短、适合手机阅读，每段不超过5行\n"
        "5. 严禁编造具体数据与政策条文；严禁出现「首先/其次/最后」；不用 emoji\n"
        "6. %s\n"
        "只输出正文，不要任何解释。" % (topic.get("title", ""), topic.get("note", ""), v["brief"])
    )
    text = llm_chat([{"role": "user", "content": prompt}], temperature=0.85)
    if not text:
        return None
    text = text.strip().strip("`").strip()
    if not text.startswith("【"):
        text = "【%s】\n\n%s" % (topic.get("title", ""), text)
    return text


def main():
    if not LLM_KEY:
        log("ℹ️ 未配置 LLM_API_KEY Secret，内容管线跳过（不算失败）。")
        log("   配置方法：仓库 Settings → Secrets and variables → Actions → New repository secret")
        log("   必需: LLM_API_KEY；可选: LLM_BASE_URL（默认 https://api.deepseek.com）、LLM_MODEL（默认 deepseek-chat）")
        return 0

    content = load_content()
    stats = []
    for v in VERTICALS:
        topics, src = scout_topics(v, content)
        if not topics:
            log("  ⚠️ [%s] 无可用选题，跳过成稿" % v["name"])
            stats.append({"板块": v["name"], "选题": 0, "成稿": 0, "来源": src})
            continue
        drafts = content.setdefault(v["drafts_col"], [])
        # 幂等：与已成稿标题做相似度查重（同题/换皮一律跳过）
        dtitles = draft_titles(v, content)
        def has_draft(title):
            return is_dup_title(title, dtitles)

        made = 0
        seq_base = len(drafts)
        for i, t in enumerate(topics):
            if has_draft(t["title"]):
                log("  · 已有成稿，跳过: %s" % t["title"])
                continue
            log("  ✍️ [%s] 成稿: %s" % (v["name"], t["title"]))
            text = gen_article(v, t)
            if not text:
                log("    ❌ 生成失败，跳过该条")
                continue
            drafts.append({
                "id": draft_id(seq_base + made + 1),
                "text": text,
                "time": time.strftime("%Y-%m-%d %H:%M"),
                "review": True,
                "source": "github",
                "topicId": t.get("id", ""),
            })
            made += 1
            time.sleep(2)
        # 选题写回选题池（去重）
        pool = content.setdefault(v["topics_col"], [])
        exist = {x.get("title") for x in pool}
        for t in topics:
            if t["title"] not in exist:
                pool.append(t)
        stats.append({"板块": v["name"], "选题": len(topics), "成稿": made, "来源": src})

    save_content(content)
    log("\n========== 执行汇总 ==========")
    for s in stats:
        log(" · %s：选题 %d 条（%s），成稿 %d 篇" % (s["板块"], s["选题"], s["来源"], s["成稿"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
