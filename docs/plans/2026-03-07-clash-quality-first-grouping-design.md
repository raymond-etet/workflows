# Clash 质量优先分组设计

**日期：** 2026-03-07

## 目标

在不删除任何现有 `rule-providers`、`rule-set`、`rules` 和既有规则目标组名的前提下，重构 Clash 配置生成逻辑，使节点分组从“地区优先”改为“质量优先”。

核心优先级为：`专线 > 高倍率 > 其他`。

## 约束

- 保留现有全部 `rule-providers` 与 `rules`
- 保留既有规则引用的组名：`Proxy`、`ai组`、`Microsoft`、`Amazon`、`币安`、`pikpak`、`欧美`
- 不在本地执行构建脚本；由 GitHub Actions 负责生成产物

## 设计

### 入口层

保留现有业务入口组，并让它们变成质量优先入口：

- `Proxy`
- `ai组`
- `Microsoft`
- `币安`
- `pikpak`
- `Amazon`
- `欧美`
- `香港`
- `东南亚`
- `全节点`
- `其他`

### 质量层

为常用入口组增加质量子组：

- `AI-专线`、`AI-高倍率`、`AI-其他`
- `MS-专线`、`MS-高倍率`、`MS-其他`
- `BN-专线`、`BN-高倍率`、`BN-其他`
- `PK-专线`、`PK-高倍率`、`PK-其他`
- `AMZ-专线`、`AMZ-高倍率`、`AMZ-其他`
- `EUUS-专线`、`EUUS-高倍率`、`EUUS-其他`
- `HK-专线`、`HK-高倍率`、`HK-其他`
- `SEA-专线`、`SEA-高倍率`、`SEA-其他`

### `Proxy` 入口

`Proxy` 不再只放分组，而是采用“手动优选节点 + 分组兜底”的混合模式：

- 前 8 个位置放自动选出的真实原始节点
- 后续放业务组和兜底组

手动优选配比固定为：

- 4 个专线节点
- 3 个高倍率节点
- 1 个其他稳定节点

并增加约束：

- 同一订阅源最多 2 个
- 同一地区最多 2 个

## 配置表达方式

在 `test.yaml` 的 `proxy-groups` 中新增生成元字段，仅供脚本消费：

- `fallback-groups`
- `quality-order`
- `preferred-regions`
- `require-quality`
- `region-include`
- `exclude-patterns`
- `manual-pick-count`
- `manual-pick-mix`
- `manual-source-cap`
- `manual-region-cap`

脚本在输出最终 `dist/config.yaml` 前统一移除这些字段。

## 节点分类

脚本先给每个节点打标签：

- `quality`: `dedicated` / `high` / `other`
- `multiplier`: 从节点名解析倍率
- `region`: `HK` / `SG` / `JP` / `TW` / `SEA` / `US` / `EU` / `OTHER`

分类规则：

- 命中 `iepl|iplc|专线|原生|住宅|家宽` 等关键词时归类为 `dedicated`
- 倍率 `>= 2x` 时归类为 `high`
- 剩余节点归类为 `other`

## 验证

本次仅做静态与语法验证：

- `python -m compileall scripts`

不在本地运行 `python scripts/build_clash_config.py`。
