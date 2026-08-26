# 搜本子 · AstrBot 插件

给 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 装上「以图搜本子」的能力。

丢一张漫画截图给 bot，它会去 [搜图Bot酱（soutubot.moe）](https://soutubot.moe/)
做相似图片检索，然后告诉你这张图出自哪一本同人志，以及它在第几页。

除了手动命令，插件还向大模型注册了一个工具。所以你也可以不用记命令，
直接把图发给 bot 然后问「这本叫什么」，它会自己去搜。

---

## 目录

- [效果预览](#效果预览)
- [安装](#安装)
- [快速上手](#快速上手)
- [命令一览](#命令一览)
- [让 bot 自己搜（LLM 工具）](#让-bot-自己搜llm-工具)
- [配置说明](#配置说明)
- [遇到 HTTP 403 怎么办](#遇到-http-403-怎么办)
- [它是怎么工作的](#它是怎么工作的)
- [常见问题](#常见问题)
- [合规与风险提示](#合规与风险提示)
- [参与开发](#参与开发)
- [致谢](#致谢)

---

## 效果预览

发送 `搜本子` 并附带一张图，bot 的回复长这样：

```
🔍 搜图Bot酱 · 普通模式 · 耗时 1.89s

1. 🟢 [Fuyuno Mikan] Hajimete no Otetsudai
   相似度 91.20%（高可信） · 日语
   来源：nHentai / NH · 第 12 页
   https://nhentai.net/g/512345/12

2. 🟡 [Fuyuno Mikan] Hajimete no Otetsudai [Chinese]
   相似度 41.31%（仅供参考） · 简体中文
   来源：E-Hentai / ExHentai · 第 12 页
   https://e-hentai.org/s/abc123def4/998877-12

完整结果：https://soutubot.moe/results/2026082616234698
```

🟢（高可信）表示这条基本可以采信，🟡（仅供参考）表示只能当线索看。
相似度低于阈值的结果会被直接丢掉，同一本书也只保留最高分的那一页，不会刷屏。

最后那行「完整结果：...」是 soutubot 的结果页链接，不想让它出现，把配置项
`show_result_page_link` 关掉即可。

---

## 安装

### 方式一：插件市场

在 AstrBot WebUI 的「插件市场」里搜索 **搜本子**，点击安装。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Whereis-Alice/astrbot_plugin_soutubot_doujin.git
```

然后在 WebUI 里重载插件即可。

### 依赖

```
aiohttp>=3.9
Pillow>=10.0
```

两个依赖 AstrBot 本体通常都已自带；如有缺失，AstrBot 会在安装插件时自动补齐。

还有一个**可选**依赖 `curl_cffi`。它只在你被 Cloudflare 拦成 HTTP 403 时才需要，
不装也能正常用，详见[遇到 HTTP 403 怎么办](#遇到-http-403-怎么办)。

```bash
pip install curl_cffi
```

### 环境要求

- AstrBot `>=4.16, <5`
- Python 3.10+
- 服务器能访问 `soutubot.moe`（不通的话在配置项 `proxy` 里填一个 HTTP 代理，
  见[遇到 HTTP 403 怎么办](#遇到-http-403-怎么办)）

---

## 快速上手

**三种用法，选一个顺手的：**

**1. 图片和命令一起发**

```
搜本子 [图片]
```

**2. 引用别人发的图**

先回复那条带图的消息，然后发 `搜本子`。

**3. 先发命令，再补图**

```
你：搜本子
bot：🔍 请发送要搜索的本子图片（60 秒内有效）
你：[图片]
bot：🔍 搜图Bot酱 · 普通模式 ...
```

**4. 干脆不用命令**

只要开着 LLM 工具（默认开启），把图发给 bot 然后问它：

```
你：[图片] 这本叫什么？
bot：这张图应该出自《[Fuyuno Mikan] Hajimete no Otetsudai》，
     相似度 91%，第 12 页。你可以在 nhentai 上找到它。
```

---

## 命令一览

主命令 `搜本子`，别名 `soutubot`、`搜图bot`、`以图搜书`。
命令前缀跟随你的 AstrBot 设置（默认 `/`）。

| 命令 | 作用 |
| --- | --- |
| `搜本子` ＋ 图片 | 以图搜书（默认模式） |
| `搜本子` | 不带图时，等你在 60 秒内补发一张图 |
| `搜本子 严格` | 本次用严格模式，误报更少但更容易搜不到 |
| `搜本子 普通` | 本次强制用普通模式 |
| `搜本子 结果 <ID>` | 用之前的结果 ID 重新查看结果（不重新上传图片） |
| `搜本子 镜像` | 查看可用的镜像域名和当前设置 |
| `搜本子 统计` | 查看调用次数、命中率、缓存命中，以及当前的访问链路 |
| `搜本子 帮助` | 显示使用说明 |

`帮助`、`镜像`、`统计` 三个子命令是纯信息查询，不受白名单和冷却限制。

### 普通模式 vs 严格模式

| | 普通模式 | 严格模式 |
| --- | --- | --- |
| 内部参数 | `factor = 1.2` | `factor = 1.4` |
| 结果数量 | 多 | 少 |
| 误报率 | 较高 | 很低 |
| 适合 | 一般情况，先试这个 | 普通模式返回了一堆明显不相关的结果时 |

想长期用某一种，改配置项 `strict_mode_default`。

---

## 让 bot 自己搜（LLM 工具）

这是本插件和一般搜图插件最主要的区别。

插件向 AstrBot 注册了一个名为 `soutubot_search_doujin` 的函数工具。
只要你的 bot 接了大模型，模型就能在判断需要的时候自己调用它——
用户不需要知道任何命令。

### 工具参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `image_url` | string | 可选。**通常留空**，插件会自动从当前消息或被引用消息里取图 |
| `strict` | boolean | 可选。是否用严格模式 |
| `max_results` | integer | 可选。最多返回几条，默认跟随配置 |

### 提示注入

大模型有时候会"忘记"自己有这个工具。为此插件挂了一个 `on_llm_request` 钩子：
当这轮对话**带图片**，或者文本命中了关键词（如「这是什么本」「出处」「搜本子」）时，
插件会临时追加一句提示，告诉模型可以用这个工具。

这条提示是**临时的**，不会写进对话历史，也不会影响其他话题。
不想要可以关掉 `inject_llm_tool_hint`。

### 防幻觉

返回给模型的文本里明确写了：

> 注意：这些结果来自图片检索，不是你的先验知识，不要额外编造作者或章节信息。

当所有候选的相似度都不高时，还会追加：

> 所有候选相似度都不高，回答时必须明确说明这只是可能的结果，不要断言。

这能显著降低模型把低相似度结果说成确定答案的概率。
如果你不希望模型把成人站点链接念出来，把 `llm_tool_include_urls` 关掉，
模型就只能拿到标题和相似度。

---

## 配置说明

全部配置都在 WebUI 的插件配置面板里，共 36 项。下面按用途分组。

### 搜索行为

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `strict_mode_default` | `false` | 默认是否用严格模式。单次可用 `搜本子 严格` 覆盖 |
| `max_results` | `5` | 命令回复最多显示几条候选。建议 3–8，`0` 表示不限 |
| `min_similarity` | `28` | 可信度下限（百分比）。低于此值直接丢弃。**28 是官网默认过滤线，调低会明显增加误报** |
| `show_urls` | `true` | 链接总开关。关掉后结果里不带任何链接（书源链接和结果页链接一起没）。有平台会因外链吞消息，被吞就关掉 |
| `show_result_page_link` | `true` | 是否显示结尾那行「完整结果：https://soutubot.moe/results/...」。只想去掉这一行、保留书源链接就关它 |
| `show_language` | `true` | 是否显示「日语 / 简体中文」这类语言标签，便于判断是不是汉化版 |

### 站点镜像

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `mirror_nhentai` | `nhentai.net` | nhentai.net 被墙时可换 `nhentai.xxx` |
| `mirror_ehentai` | `e-hentai.org` | 可换 `exhentai.org`，但**需要有效登录 Cookie 才能打开** |

用 `搜本子 镜像` 可以随时查看当前设置和可选值。

### 交互流程

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `wait_timeout_seconds` | `60` | 发了 `搜本子` 但没带图时，等待补图的秒数 |
| `prompt_send_image` | 见面板 | 提示用户发图的文案。留空用内置文案 |
| `prompt_timeout` | 见面板 | 等待超时的提示文案。**留空则不发超时提示** |
| `auto_search_on_image` | `false` | 收到任何图片就自动搜索。⚠️ 见下方风险说明 |

### 限流与权限

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `cooldown_seconds` | `10` | 同一用户的搜索冷却秒数。`0` 表示不限 |
| `max_concurrency` | `2` | 全插件共享的并发上限。**soutubot 是免费公益服务，请不要调高** |
| `allowed_sessions` | `[]` | 会话白名单。填群号 / 用户 ID / 完整会话 ID（如 `aiocqhttp:GroupMessage:12345`）。留空不限 |
| `blocked_sessions` | `[]` | 会话黑名单。**优先级高于白名单**，命中即拒绝 |
| `private_only` | `false` | 只允许私聊使用。最稳妥的合规选项 |

### 结果呈现

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `send_preview_image` | `false` | 是否发送命中页缩略图。⚠️ 默认关闭，因为图直接来自成人站点 CDN |
| `max_preview_images` | `1` | 最多发几张预览图（仅在上一项开启时生效） |
| `use_forward_message` | `false` | 用合并转发发送结果，可以把内容折叠。**仅 QQ（aiocqhttp）**，其他平台自动回退 |
| `recall_after_seconds` | `0` | 多少秒后自动撤回结果，`0` 表示不撤回。**仅 QQ（aiocqhttp）** |

### LLM 工具

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_tool_enabled` | `true` | 是否注册 `soutubot_search_doujin` 工具 |
| `inject_llm_tool_hint` | `true` | 是否在相关请求里注入临时工具提示 |
| `llm_tool_max_results` | `5` | 工具最多返回几条。建议 3–5，太多会占上下文 |
| `llm_tool_include_urls` | `true` | 工具返回内容是否含链接。关掉后模型拿不到成人站点 URL |
| `tool_request_keywords` | 13 个默认词 | 命中这些词才注入提示 |
| `tool_description` | 见面板 | 工具描述。**高级设置**，直接影响模型何时主动调用 |

### 缓存

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `cache_enabled` | `true` | 同一张图重复搜索时直接命中缓存，减轻上游压力 |
| `cache_ttl_hours` | `72` | 缓存有效期（小时）。`0` 表示永不过期 |

### 网络（一般不用动）

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | `https://soutubot.moe` | 站点地址。有可用的自建镜像可以填这里 |
| `proxy` | 空 | HTTP 代理，如 `http://127.0.0.1:7890`。留空直连 |
| `user_agent` | 空 | 留空用内置值。**UA 长度参与接口签名**，改动后插件会自动保持一致，但不建议乱改 |
| `request_timeout` | `60` | 单次请求超时秒数。图大或网慢可以调高 |
| `max_retries` | `2` | 失败重试次数。401 / 429 / 5xx 会自动重试 |

### 绕过 Cloudflare（默认不用管，被 403 时才需要）

这两项都是为了应对 `HTTP 403`，正常能搜就不用动。填法见
[遇到 HTTP 403 怎么办](#遇到-http-403-怎么办)。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `tls_impersonate` | `auto` | 是否用真实浏览器的 TLS 指纹发请求。`auto` = 只在被 403 时自动切换；`on` = 一直用；`off` = 关闭。需要 `pip install curl_cffi` |
| `extra_cookie` | 空 | 附加 Cookie，一般用来粘浏览器里的 `cf_clearance=...`。**和 IP + UA 绑定，换 IP 就失效** |

---

## 遇到 HTTP 403 怎么办

如果 bot 回你这么一句：

```
❌ 搜图Bot酱暂时出错了：访问 soutubot 首页失败：HTTP 403
```

**这不是插件签名算错了，也不是你配置填错了。** soutubot.moe 挂在 Cloudflare 后面，
403 意味着 Cloudflare 在门口就把你的服务器挡下了，请求压根没送到 soutubot。

先分清两个长得很像的错误：

| 状态码 | 真实含义 | 该看哪里 |
| --- | --- | --- |
| **403** | Cloudflare 拦住了你的 IP 或 TLS 指纹 | 就是本节 |
| **401** | 请求到了 soutubot，但接口签名被拒 | 校准服务器时间，见[常见问题](#提示鉴权失败) |

判断方法：机房 / VPS 的 IP 被拦是常态，家宽（住宅 IP）基本都能直连。
插件在遇到 403 时会往日志里写一行诊断，包含 Cloudflare 的 `cf-ray`、`server`
响应头和页面特征，用来区分「人机验证页」「WAF 规则命中」「地区封锁」。
另外 `搜本子 统计` 里有一行「访问链路」，能看到当前实际走的是哪条路。

下面两个方案，**建议按 A → B 的顺序试**。

### 方案 A：换个出口 IP（最省事，成功率最高）

- 在配置项 `proxy` 里填一个能直连的 HTTP 代理，比如 `http://127.0.0.1:7890`
- 或者把 bot 挪到另一台机器 / 换一个 VPS 出口

常见云厂商的 IDC 网段被 Cloudflare 拦得最狠，家宽和小众机房通常没事。

### 方案 B：伪装浏览器 TLS 指纹

Cloudflare 除了看 IP，还会看 TLS 握手指纹（JA3）。Python 默认的指纹和任何浏览器
都不一样，很容易被判成脚本。装上 `curl_cffi` 就能用真实 Chrome 的指纹发请求：

```bash
pip install curl_cffi
```

然后看配置项 `tls_impersonate`：

| 值 | 行为 |
| --- | --- |
| `auto`（默认） | 先用普通请求；**只有撞上 403 才自动切换到伪装指纹重试一次**，而且不占用 `max_retries` 的额度 |
| `on` | 从第一个请求就用伪装指纹 |
| `off` | 永不使用 |
| 具体目标名，如 `chrome124` | 指定伪装成哪个浏览器版本 |

没装 `curl_cffi` 的话，`auto` / `on` 会静默退回普通请求（不会报错），
`搜本子 统计` 的「访问链路」里会标注「未安装 curl_cffi」。

补充手段：如果你在自己浏览器里能正常打开 soutubot.moe，可以把浏览器的
`cf_clearance` Cookie 粘到 `extra_cookie`（形如 `cf_clearance=xxxxx`）。
但这个 Cookie 和 **IP + User-Agent 绑定**，换 IP 立刻失效，只能当临时救急。

如果这两条都试过还是 403，说明你这台机器的出口 IP 在 Cloudflare 眼里信誉太低，
只能换网络或换代理出口——**这一步在插件侧绕不过去**。

> 补充一句实测结论：我试过用 Cloudflare Worker 做反向代理来「借 Cloudflare 的手」
> 访问 soutubot，实际会稳定拿到上游 500（Worker 回源到 Cloudflare 自家 zone 的
> orange-to-orange 路由问题）。所以这条路已经从插件里移除了，别再花时间折腾。

---

## 它是怎么工作的

这一节写给好奇的人和想改代码的人，只想用的话可以跳过。

### 整体流程

```
用户消息
  └─ 取图（当前消息 → 引用消息 → 平台原始附件）
       └─ 下载 / 读本地文件
            └─ 预处理：非 jpeg/png/webp 或超过 4MB → 压成 JPEG（最长边 2000px，质量 90）
                 └─ 算 SHA-256 → 查缓存
                      ├─ 命中 → 直接返回
                      └─ 未命中 → POST soutubot.moe/api/search
                                    └─ 过滤 / 去重 / 分级 → 渲染成消息
```

### 接口签名

soutubot 的接口有一层轻量防刷：每次请求都要带一个 `X-API-KEY`，
它由**当前时间戳**、**User-Agent 的长度**和一个从首页 HTML 里取出的
**一次性令牌**共同算出来。

所以插件的做法是：先抓一次首页拿到令牌（缓存 120 秒），再签名发请求。
如果服务器返回 `401`，说明签名被拒，插件会**强制刷新令牌后重试一次**。

这套逻辑是通过阅读 soutubot 公开的前端 JavaScript 独立实现的，
细节见 [NOTICE.md](NOTICE.md)。

### 相似度阈值

这三个数字直接沿用官网前端的判定：

| 相似度 | 判定 | 表现 |
| --- | --- | --- |
| `< 28%` | 噪声 | 直接丢弃，不展示 |
| `28% ~ 45%` | 仅供参考 | 🟡 黄色，文案里明确标注不确定 |
| `>= 45%` | 可信 | 🟢 绿色 |

严格模式下"可信"的门槛降到 `35%`——因为严格模式本身已经过滤掉了大量噪声。

### 三种来源

soutubot 的索引覆盖三个站点，它们的 URL 结构完全不同，插件都做了适配：

| 来源 | 单页链接 | 整本链接 | 备注 |
| --- | --- | --- | --- |
| **nhentai** | `/g/{id}/{page}` | `/g/{id}` | 支持 `nhentai.net` / `nhentai.xxx` |
| **E-Hentai** | `/s/{hash}/{gid}-{page}` | `/g/{gid}/{token}` | 支持 `e-hentai.org` / `exhentai.org` |
| **Panda** | 无单页链接 | `/archive/{id}` | 只能跳整本，插件会自动回退 |

### 去重

同一本书的多个页面经常同时命中。插件按「来源 + 画廊 ID」去重，
每本只保留相似度最高的那一页，避免同一本刷五条。

### 缓存

按 `图片 SHA-256 + 模式` 作为键，分两级：

- **内存**：最多 200 条，超出后淘汰最旧的
- **插件 KV**：持久化，重启不丢，默认 72 小时过期

结果里出现 `♻️ 本次结果来自本地缓存` 就说明这次没有打上游接口。

### 代码结构

```
main.py                插件入口：命令、事件钩子、LLM 工具、权限、缓存编排
soutubot/
  client.py            HTTP 客户端：签名、multipart 封包、重试与退避
  models.py            数据模型：SoutubotMatch / SoutubotSearchResult
  mirrors.py           镜像域名与来源、语言的中文标签
  render.py            相似度分级、去重、标题清洗、消息与 LLM 摘要渲染
  utils.py             图片嗅探与预处理、配置读取、下载
tests/                 351 个离线单元测试
```

`soutubot/` 子包**不依赖 AstrBot**，可以单独拿去别的项目用。

---

## 常见问题

### 搜不到结果 / 一条都没有

这通常是正常的。soutubot 的索引不可能覆盖所有作品，
而且如果你的图是**动画截图、单张插画、AI 生成图**，那本来就不在本子索引里。

可以试试：
- 换成封面或内页扫图，而不是二次加工过的图
- 如果用的是严格模式，换成 `搜本子 普通`
- 适度调低 `min_similarity`（但会增加误报，不建议低于 20）

### 提示「鉴权失败」

接口签名依赖时间戳，**服务器系统时间不准**就会被拒。
先校准时间：

```bash
# Linux
sudo timedatectl set-ntp true
```

Docker 容器的时间跟宿主机一致，所以要校准宿主机。

### 提示「请求太频繁」

被上游限流了，等一会儿。这也是插件默认把 `max_concurrency` 设成 `2`、
`cooldown_seconds` 设成 `10` 的原因——请不要为了图快去调高。

### 提示 HTTP 403

Cloudflare 把你的服务器挡在门外了，跟插件配置无关。
处理办法单独写了一节：[遇到 HTTP 403 怎么办](#遇到-http-403-怎么办)。

**别把 403 和 401 搞混**：403 是没进门，401 是进了门但签名被拒（校时间就行）。

### 提示「连不上搜图Bot酱」

大陆网络环境下可能需要代理，在 `proxy` 里填 `http://127.0.0.1:7890` 之类。

### E-Hentai 链接打不开

如果你把 `mirror_ehentai` 改成了 `exhentai.org`，那需要浏览器里有有效的
E-Hentai 登录 Cookie，否则会看到一张白纸。改回 `e-hentai.org` 即可。

### 结果消息被平台吞了

有些平台会拦截含外链的消息。把 `show_urls` 关掉试试；如果只是想去掉结尾那行
结果页链接、保留书源链接，关 `show_result_page_link` 就够了。
QQ 上还可以开 `use_forward_message`，用合并转发发出去。

### 图片太大

插件会自动把超过 4MB 的图压成 JPEG（最长边 2000px），
但源文件超过 32MB 会直接拒绝。手动压一下再发。

---

## 合规与风险提示

**请务必读完这一节。**

- 搜图Bot酱的索引来自 **nhentai / E-Hentai / Panda**，这些站点的内容
  绝大多数是**成人向（NSFW）**的。本插件返回的书名和链接同样如此。
- 因此插件的默认配置是**保守**的：
  - `send_preview_image = false`——不主动发缩略图
  - `auto_search_on_image = false`——不自动搜索群里的图
  - 这两项**默认关闭是有意的**，开启前请想清楚后果。
- **强烈建议**在群聊场景配置 `allowed_sessions` 白名单，或者干脆开
  `private_only` 只允许私聊。
- 在 QQ 等平台的公开群聊里传播成人内容可能违反平台规定，
  账号风险由使用者自行承担。
- 请遵守你所在地区的法律法规。**未成年人请勿使用。**
- 搜图Bot酱是**免费公益服务**。请善用缓存、不要刷接口、不要把它当批量
  处理管道用。滥用会伤害所有人。
- 本插件与搜图Bot酱官方**没有任何隶属关系**，也未获得其授权或背书。
  接口随时可能变化。

---

## 参与开发

### 跑测试

```bash
cd astrbot_plugin_soutubot_doujin
python -m pytest tests -q
```

351 个测试，全部离线（HTTP 层用假 session 注入），不会打真实接口，
1 秒左右跑完。

覆盖范围包括：签名算法（含已知向量）、令牌提取、multipart 封包、
401/429/413/5xx 等各种错误路径与重试次数、三种来源的 URL 拼接、
相似度分级边界、去重、标题清洗、图片嗅探与预处理、配置读取的各种边界值。

### 提 Issue / PR

欢迎。改动源码后请把 `python -m pytest tests -q` 跑绿。

---

## 致谢

**首先感谢 [搜图Bot酱（soutubot.moe）](https://soutubot.moe/)。**
本插件所有的检索能力都来自这个免费公益服务，没有它就没有这个插件。
也感谢它背后的图像检索引擎
[lolishinshi/imsearch](https://github.com/lolishinshi/imsearch)。

其次，感谢下面这些**同类项目**。它们和本插件解决同一个问题，
在功能设计与交互思路上给了我很多启发。特别说明：本插件对 soutubot 接口的实现
是独立完成的（clean-room，仅参考官方公开前端 JS），**没有复制这些项目的代码**，
在此列出纯粹是出于尊重与感谢：

| 项目 | 说明 |
| --- | --- |
| [AUsokiYu/astrbot_plugin_soutubot](https://github.com/AUsokiYu/astrbot_plugin_soutubot) | AstrBot 上的搜图Bot酱插件（AGPL-3.0） |
| [NanMuFengtai/Astrbot-soutubot](https://github.com/NanMuFengtai/Astrbot-soutubot) | AstrBot 上的另一个实现 |
| [sharman121/astrbot_search_download](https://github.com/sharman121/astrbot_search_download) | AstrBot 搜本子 + nhentai 下载 |
| [crosage/nonebot-plugin-spiders](https://github.com/crosage/nonebot-plugin-spiders) | NoneBot 的 ascii2d / soutubot 插件（AGPL-3.0） |
| [Miuzarte/SoutuBot-go](https://github.com/Miuzarte/SoutuBot-go) | Go 语言实现（MIT） |
| [shezhao/soutubot_app](https://github.com/shezhao/soutubot_app) | 安卓客户端 |
| [crosage/search_nhentai_UI](https://github.com/crosage/search_nhentai_UI) | Flutter 前端 |

最后，感谢 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供的插件与
LLM 工具框架。

授权详情与 clean-room 说明见 [NOTICE.md](NOTICE.md)。

---

## License

[MIT](LICENSE) © Huli3
