# Notice

## 本插件与上游代码的关系

本插件（`astrbot_plugin_soutubot_doujin`）**不是**任何现有插件的 fork。
对 soutubot.moe 接口的调用方式，是通过阅读该站点公开的前端 JavaScript
（`https://soutubot.moe/build/assets/app-*.js`）并配合实际请求验证，
独立实现（clean-room）而成的。

具体地：
- 请求签名算法、`multipart/form-data` 封包格式、相似度阈值（28 / 35 / 45）
  与三种来源（nhentai / ehentai / panda）的路径拼接规则，均来自官方前端逻辑本身。
- 我们**没有阅读、也没有复制**任何第三方实现（包括下列以 AGPL-3.0 授权的项目）
  的源代码。
- 因此本插件以 MIT 授权发布，不受上游 AGPL 传染条款约束。

## 致谢的同类项目

下列项目与本插件解决同一个问题。它们在思路与功能设计上给了我们启发，
在此列出以示尊重与感谢。它们的代码未被引入本仓库。

| 项目 | 语言 | 授权 |
| --- | --- | --- |
| [AUsokiYu/astrbot_plugin_soutubot](https://github.com/AUsokiYu/astrbot_plugin_soutubot) | Python | AGPL-3.0 |
| [NanMuFengtai/Astrbot-soutubot](https://github.com/NanMuFengtai/Astrbot-soutubot) | Python | 未声明 |
| [sharman121/astrbot_search_download](https://github.com/sharman121/astrbot_search_download) | Python | 未声明 |
| [crosage/nonebot-plugin-spiders](https://github.com/crosage/nonebot-plugin-spiders) | Python | AGPL-3.0 |
| [Miuzarte/SoutuBot-go](https://github.com/Miuzarte/SoutuBot-go) | Go | MIT |
| [shezhao/soutubot_app](https://github.com/shezhao/soutubot_app) | Java | 未声明 |
| [crosage/search_nhentai_UI](https://github.com/crosage/search_nhentai_UI) | Dart | 未声明 |

## 上游服务

- **搜图Bot酱** — https://soutubot.moe/ ：本插件全部检索能力的来源，免费公益服务。
- **lolishinshi/imsearch** — https://github.com/lolishinshi/imsearch ：
  搜图Bot酱页脚标注的底层图像检索引擎。

本插件不隶属于、也未获得搜图Bot酱官方授权或背书。
