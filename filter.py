#!/usr/bin/env python3
import base64, os, re, requests, yaml, json, datetime, sys

SUB_URL = os.environ.get("SUB_URL",  "https://url.v1.mk/sub?target=clash&url=https%3A%2F%2F47.238.198.94%2Fiv%2Fverify_mode.htm%3Ftoken%3De017cb1dc789505b3add5f3299fd9ea8&insert=false&config=https%3A%2F%2Fraw.githubusercontent.com%2FACL4SSR%2FACL4SSR%2Fmaster%2FClash%2Fconfig%2FACL4SSR_Online_Full_NoAuto.ini&emoji=true&list=false&xudp=false&udp=false&tfo=false&expand=true&scv=false&fdn=false&new_name=true")
SUFFIX  = "HK|SG"
BAN     = "USG"

# ---------- 0. 取远端内容 ----------
raw = requests.get(SUB_URL, timeout=15).text.strip()

# ---------- 1. Base64 → YAML ----------
try:
    decoded = base64.b64decode(raw).decode('utf-8')
except Exception:
    decoded = raw   # 本来就不是 base64

# ---------- 2. 兼容三种常见格式 ----------
nodes = []
# 2-a YAML 已经是 dict
if decoded.startswith('proxies:'):
    parsed = yaml.safe_load(decoded)
    nodes = parsed['proxies']
# 2-b 直接是一个 proxy 列表
elif decoded.startswith('- '):
    nodes = yaml.safe_load(decoded)
# 2-c JSON 列表
elif decoded.startswith('[{'):
    nodes = json.loads(decoded)

if not nodes:
    print("❌ 无法解析节点", file=sys.stderr)
    exit(1)

# ---------- 3. 过滤 ----------
expr_keep = re.compile(fr'{SUFFIX}', re.I)
expr_ban  = re.compile(fr'{BAN}',   re.I)

filtered = [
    n for n in nodes
    if expr_keep.search(n.get('name', '')) and not expr_ban.search(n.get('name', ''))
]
print(f'[{datetime.datetime.utcnow()}] 原始节点：{len(nodes)}，过滤后：{len(filtered)}')

# ---------- 4. 写入模板 ----------
template = yaml.safe_load(open('order-clash/clash.yaml', encoding='utf-8'))
template['proxies'] = filtered

# proxy-groups 替换
for grp in template.setdefault('proxy-groups', []):
    if grp.get('proxies') is None:
        grp['proxies'] = []
    grp['proxies'] = [f['name'] for f in filtered]

yaml.dump(template,
          open('a.yaml', 'w', encoding='utf-8'),
          default_flow_style=False,
          sort_keys=False,
          allow_unicode=True)
