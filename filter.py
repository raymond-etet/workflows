#!/usr/bin/env python3
import base64, os, re, requests, yaml, datetime

SUB_URL = os.environ.get("SUB_URL",  "https://47.238.198.94/iv/verify_mode.htm?token=e017cb1dc789505b3add5f3299fd9ea8")
SUFFIX   = os.environ.get("SUFFIX",  "HK|SG")     # 需要保留的关键字
BAN      = os.environ.get("BAN",     "USG")       # 需要排除的关键字

raw   = base64.b64decode(requests.get(SUB_URL, timeout=15).text).decode()
nodes = yaml.safe_load(raw)["proxies"]

# 正则筛选
expr_keep = re.compile(rf'(?:{SUFFIX})', re.I)
expr_ban  = re.compile(rf'{BAN}', re.I)

filtered = [
    n for n in nodes
    if expr_keep.search(n["name"]) and not expr_ban.search(n["name"])
]

print(f'[{datetime.datetime.utcnow()}] 原始节点：{len(nodes)}，过滤后：{len(filtered)}')

template = yaml.safe_load(open("order-clash/clash.yaml"))
template["proxies"] = filtered

# 重构 proxy-groups 引用的 proxies
for grp in template.get("proxy-groups", []):
    if grp["name"] in {"🚀 节点选择", "♻️ 自动选择"}:
        new_proxies = [n["name"] for n in filtered]
        grp["proxies"] = new_proxies

yaml.dump(template,
          open("a.yaml", "w", encoding="utf-8"),
          default_flow_style=False,
          sort_keys=False,
          allow_unicode=True)
