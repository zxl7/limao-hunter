# -*- coding: utf-8 -*-
import json, re

card = json.load(open("/Users/zxl/WorkBuddy/狸猫猎手/cache/gangtise_morning_research.json", encoding="utf-8"))
ALLOW = ("600","601","603","605","000","001","002","003","300","301")
stock_re = re.compile(r"([\u4e00-\u9fa5A-Za-z·]+?)\((\d{6})\)")
emoji_re = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")

def strip_title(t):
    t = t.replace("**", "")
    t = emoji_re.sub("", t)
    return t.strip()

items = {}  # code -> dict
order = []

def add(name, code, theme):
    if not code.startswith(ALLOW):
        return
    if code in items:
        return
    items[code] = {"code": code, "name": name, "theme": theme}
    order.append(code)

for el in card["elements"]:
    if el.get("tag") != "div":
        continue
    content = el["text"]["content"]
    if content.startswith("📎 说明"):
        continue
    if content.startswith("**🧭"):
        # speed view block: each line "▸ label：text"
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("▸ "):
                body = line[2:]
                if "：" in body:
                    label, _, txt = body.partition("：")
                    theme = label.strip()
                else:
                    theme = "机构速览"
                for m in stock_re.finditer(txt):
                    add(m.group(1), m.group(2), theme)
        continue
    # main block
    theme = strip_title(content.split("\n", 1)[0])
    for line in content.split("\n"):
        if line.strip().startswith("▸ 机构点名："):
            txt = line.split("：", 1)[1]
            for m in stock_re.finditer(txt):
                add(m.group(1), m.group(2), theme)

pool = {"date": "2026-09-21", "count": len(order), "items": [items[c] for c in order]}
out = "/Users/zxl/WorkBuddy/狸猫猎手/cache/institution_pool_2026-09-21.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(pool, f, ensure_ascii=False, indent=2)
print("pool written:", out, "count=", pool["count"])
for it in pool["items"]:
    print(it["code"], it["name"], "|", it["theme"])
