#!/usr/bin/env python3
import base64, os, re, requests, yaml, json, datetime, sys

SUB_URL = os.environ.get(
    "SUB_URL",
    "https://47.238.198.94/iv/verify_mode.htm?token=e017cb1dc789505b3add5f3299fd9ea8"
)
SUFFIX = "HK|SG"
BAN = "USG"

def is_base64(s: str) -> bool:
    # 判断是否是标准 Base64 字符串
    try:
        # 字符过滤
        if not re.fullmatch(r'[A-Za-z0-9+/=\r\n]+', s):
            return False
        # 长度必须是4的倍数
        if len(s.strip()) % 4 != 0:
            return False
        base64.b64decode(s, validate=True)
        return True
    except Exception:
        return False

# ---------- 0. 获取远端内容 ----------
try:
    resp = requests.get(SUB_URL, timeout=15)
    resp.raise_for_status()
    raw = resp.text.strip()
except Exception as e:
    print(f"❌ 请求失败: {e}", file=sys.stderr)
    sys.exit(1)

# ---------- 1. Base64 → 文本 ----------
if is_base64(raw):
    try:
        decoded = base64.b64decode(raw).decode('utf-8', errors='replace').strip()
    except Exception as e:
        print(f"❌ Base64 解码失败: {e}", file=sys.stderr)
        sys.exit(1)
else:
    decoded = raw

# ---------- 2. 尝试多种解析 ----------
nodes = []
try:
    # YAML 格式
    if decoded.startswith('proxies:'):
        parsed = yaml.safe_load(decoded)
        nodes = parsed.get('proxies', [])
    elif decoded.startswith('- '):
        nodes = yaml.safe_load(decoded)
    # JSON 格式
    elif decoded.startswith('[{'):
        nodes = json.loads(decoded)
except Exception as e:
    print(f"❌ 解析失败: {e}", file=sys.stderr)

if not nodes:
    print("❌ 无法解析节点", file=sys.stderr)
    print("原始数据前200字符：")
    print(decoded[:200])
    sys.exit(1)

# ---------- 3. 过滤 ----------
expr_keep = re.compile(fr'{SUFFIX}', re.I)
expr_ban = re.compile(fr'{BAN}', re.I)

filtered = [
    n for n in nodes
    if expr_keep.search(n.get('name', '')) and not expr_ban.search(n.get('name', ''))
]
print(f'[{datetime.datetime.utcnow()}] 原始节点：{len(nodes)}，过滤后：{len(filtered)}')

# ---------- 4. 写入模板 ----------
try:
    template = yaml.safe_load(open('order-clash/clash.yaml', encoding='utf-8'))
except FileNotFoundError:
    print("❌ 找不到模板文件 order-clash/clash.yaml", file=sys.stderr)
    sys.exit(1)

template['proxies'] = filtered

# proxy-groups 替换
for grp in template.setdefault('proxy-groups', []):
    if grp.get('proxies') is None:
        grp['proxies'] = []
    grp['proxies'] = [f['name'] for f in filtered]

yaml.dump(
    template,
    open('a.yaml', 'w', encoding='utf-8'),
    default_flow_style=False,
    sort_keys=False,
    allow_unicode=True
)
