# Changelog

## v1.0.2

移除 Cloudflare Worker 反向代理（实测走不通），新增「隐藏完整结果链接」开关。

### 新增
- 配置项 `show_result_page_link`（默认 `true`）：控制结果结尾那行
  `完整结果：https://soutubot.moe/results/xxxxxxxx` 是否显示。
  关掉后命令回复与 LLM 工具返回都不再带这个链接，书源链接不受影响。
  `show_urls` 仍是总开关，关掉它则两类链接一起隐藏。

### 移除
- 配置项 `reverse_proxy_url` / `reverse_proxy_token` / `reverse_proxy_images`，
  以及反代脚本 `deploy/cloudflare-worker.js`。
  实测 Worker 回源 soutubot.moe 会稳定拿到上游 500（Worker → Cloudflare 自家 zone
  的 orange-to-orange 路由问题），这条路径不可用，故整体删除。
  被 403 时请改用配置项 `proxy` 换出口，或用 `tls_impersonate` 伪装 TLS 指纹。

### 变更
- 配置项从 38 项减少到 36 项。
- 单元测试从 362 个减少到 351 个。
- 403 提示文案不再提及反代，改为引导换代理 / 换网络。
- README 的「遇到 HTTP 403 怎么办」由三个方案精简为两个。

## v1.0.1

修复直连 soutubot.moe 时被 Cloudflare 拦成 `HTTP 403` 的问题，并补上三档绕过方案。

### 新增
- 配置项 `tls_impersonate`：可选用真实浏览器的 TLS 指纹（JA3）发请求，
  需要 `pip install curl_cffi`。默认 `auto`——只在撞上 403 时自动切换重试一次，
  且不占用 `max_retries` 额度；未安装 `curl_cffi` 时静默退回普通请求。
- 配置项 `extra_cookie`：附加 Cookie，用于粘贴浏览器里的 `cf_clearance`。
- 配置项 `reverse_proxy_url` / `reverse_proxy_token` / `reverse_proxy_images`：
  可选的 Cloudflare Worker 反向代理。走反代时 `Referer` / `Origin` 仍指向
  soutubot.moe，不会破坏接口签名。
- 反代脚本 `deploy/cloudflare-worker.js`，含图片域名白名单与 `PROXY_TOKEN` 鉴权。
- 新异常 `SoutubotBlockedError` 与 403 诊断：按 Cloudflare 响应特征区分
  人机验证页 / WAF 规则 / 地区封锁，并给出对应的处置建议。
- `搜本子 统计` 新增「访问链路」一行，显示当前的传输层与出口。

### 变更
- 403 不再被当成普通的「意外状态码」，而是走独立的分类与提示路径。
- 5xx / 429 保持退避重试；其他非预期状态码不再无谓重试。
- 配置项从 33 项增加到 38 项。
- 单元测试从 313 个增加到 362 个。

## v1.0.0

首个版本。

### 新增
- `搜本子` 命令（别名 `soutubot` / `搜图bot` / `以图搜书`），支持直接附图、
  引用带图消息、以及不带图时等待补图三种用法。
- 子命令：`严格` / `普通` / `结果 <ID>` / `镜像` / `统计` / `帮助`。
- LLM 工具 `soutubot_search_doujin`，让模型在用户问「这本叫什么」时自主检索。
- `on_llm_request` 临时提示注入，提高模型主动调用工具的概率。
- 结果缓存（内存 + 插件 KV，按图片 SHA-256 + 模式分桶，默认 72 小时）。
- 相似度分级（≥45 高 / ≥28 中 / <28 折叠）、同一本去重、标题清洗。
- nhentai 与 e-hentai 的镜像站切换。
- 白名单 / 黑名单 / 仅私聊 / 每用户冷却 / 全局并发上限。
- 可选的合并转发发送与定时撤回（aiocqhttp）。
- 运行统计（搜索次数、命中率、缓存命中、失败数、工具调用数）。

### 说明
- 默认关闭预览图发送与自动搜图，需手动开启。
