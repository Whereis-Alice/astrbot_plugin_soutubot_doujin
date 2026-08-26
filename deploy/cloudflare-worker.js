/**
 * soutubot 反向代理 Worker（astrbot_plugin_soutubot_doujin 配套文件，可选组件）
 * ============================================================================
 *
 * 这是什么：
 *   插件默认直连 https://soutubot.moe。部分机房 / 家宽出口 IP 会被 Cloudflare
 *   拦截，表现为所有请求返回 HTTP 403。把这个 Worker 部署到你自己的 Cloudflare
 *   账号后，插件会把请求打到 Worker 域名，由 Worker 代为转发到 soutubot.moe，
 *   从而绕开出口 IP 被拦的问题。
 *
 * 部署步骤（Cloudflare 控制台，免费套餐即可）：
 *   1. 登录 https://dash.cloudflare.com → 左侧「Workers & Pages」（新版在「计算 Compute」下）。
 *   2. 点「Create application」→「Create Worker」，起个名字（例如 soutubot-proxy），
 *      点 Deploy 创建出一个空 Worker。
 *   3. 点「Edit code」，把编辑器里的默认代码全选删除，粘贴本文件的全部内容，
 *      再点右上角「Deploy」保存部署。
 *   4. 回到 Worker 的「Settings」→「Variables and Secrets」（旧版叫 Variables），
 *      添加变量：Name = PROXY_TOKEN，Value = 自己随机生成的一串长口令，
 *      类型建议选 Secret，保存后重新 Deploy 使其生效。
 *   5. 在 Worker 概览页复制访问域名（形如 https://soutubot-proxy.xxx.workers.dev），
 *      填进插件配置项 reverse_proxy_url（结尾不要带斜杠）；把第 4 步的口令填进
 *      插件配置项 reverse_proxy_token。
 *   6. 如果还想让预览图也走反代（用到本文件的 /img 路由），把插件配置项
 *      reverse_proxy_images 打开；只在 send_preview_image 开启时才有意义。
 *
 * 关于 PROXY_TOKEN：
 *   不设置 PROXY_TOKEN 时本 Worker 不做任何鉴权，等于把一个公开的 soutubot 代理
 *   挂在公网上，任何人都能刷你的 Worker 免费额度。强烈建议设置。
 *
 * ⚠️ 严重警告：不要修改 User-Agent 相关代码 ⚠️
 *   soutubot 的接口签名算法是：
 *       md5(str(time^2 + len(User-Agent)^2 + boot_token))
 *   签名由插件在本地计算，其中直接用到了 User-Agent 的「字符串长度」。因此 Worker
 *   必须把插件发来的 User-Agent 原样透传给上游，一个字符都不能增删。一旦在这里改写、
 *   补默认值或追加 " via Cloudflare Worker" 之类的后缀，长度就变了，签名随之失效，
 *   上游会返回 HTTP 401。同理 Referer / Origin 必须保持插件发来的 soutubot.moe，
 *   不要改写成 Worker 自己的域名。
 *
 * 兼容性：纯单文件、零 npm 依赖、Module Worker 语法，直接粘贴即可运行。
 */

'use strict';

/** 上游站点：所有非 /img 请求都转发到这里（仅替换 origin，path 与 query 原样保留）。 */
const UPSTREAM_ORIGIN = 'https://soutubot.moe';

/** 上游请求超时时间（毫秒）。搜索接口偶尔较慢，留够余量。 */
const UPSTREAM_TIMEOUT_MS = 30000;

/** 允许的 HTTP 方法，其余一律 405。插件只会用到 GET / POST。 */
const ALLOWED_METHODS = new Set(['GET', 'POST', 'HEAD', 'OPTIONS']);

/** 允许的精确路径：首页（用于提取 boot token）、站点图标。 */
const ALLOWED_EXACT_PATHS = new Set(['/', '/favicon.ico']);

/**
 * 允许的路径前缀：
 *   /api/    —— 搜索与结果查询接口（/api/search、/api/results/{id}）
 *   /_nuxt/  —— 首页的 Nuxt 静态资源
 *   /assets/ —— 首页的其他静态资源
 * 白名单之外的路径直接 404，避免这个 Worker 被当成通用代理使用。
 */
const ALLOWED_PATH_PREFIXES = ['/api/', '/_nuxt/', '/assets/'];

/**
 * /img 预览图代理允许的图片站 host 白名单。
 *
 * 注意：这里必须用「完全相等」或「以 .域名 结尾」判断，绝对不要用 includes()。
 * 用 includes() 时 https://evil.com/?x=soutubot.moe、https://soutubot.moe.evil.com/
 * 这类地址都能通过检查，Worker 就变成了任何人都能白嫖的开放代理（可被用来隐藏真实
 * 来源、刷流量，也会给你的 Cloudflare 账号带来滥用风险）。
 */
const IMAGE_HOST_WHITELIST = [
  'soutubot.moe', // 同时覆盖 img.soutubot.moe 等全部 *.soutubot.moe 子域
  'img.76888268.xyz',
  'i.nhentai.net',
  't.nhentai.net',
  'ehgt.org',
];

/**
 * 需要原样透传给上游的请求头（小写）。
 * user-agent / x-api-key / referer / origin 与签名校验强相关，缺一不可。
 * 未列出的请求头一律不转发，因此 cf-*、x-forwarded-*、x-real-ip、x-proxy-token
 * 这些只对本 Worker 有意义、或会暴露真实来源的头天然被丢弃。
 */
const FORWARD_REQUEST_HEADERS = [
  'user-agent',
  'accept',
  'accept-language',
  'referer',
  'origin',
  'content-type', // multipart/form-data 的 boundary 必须原样保留
  'x-requested-with',
  'x-api-key', // 插件本地算出的 md5 签名
  'cookie',
  'upgrade-insecure-requests',
  'sec-fetch-dest',
  'sec-fetch-mode',
  'sec-fetch-site',
  'sec-fetch-user',
];

/**
 * 明确禁止转发的请求头（小写精确名），与上面的白名单构成双保险：
 * 即便后续有人往 FORWARD_REQUEST_HEADERS 里加了东西，这些头也不会被带到上游。
 */
const BLOCKED_REQUEST_HEADERS = new Set([
  'host',
  'x-real-ip',
  'x-proxy-token', // 鉴权口令只给本 Worker 看，不能泄漏给上游
  'true-client-ip',
  'forwarded',
]);

/** 明确禁止转发的请求头前缀（小写）：Cloudflare 注入头与各种代理链路头。 */
const BLOCKED_REQUEST_HEADER_PREFIXES = ['cf-', 'x-forwarded-'];

/**
 * 回传响应时需要丢弃的响应头（小写）。
 * content-encoding 由 Workers 运行时在自动解压后自行维护，原样抄回去会导致
 * 客户端解码失败；其余是逐跳（hop-by-hop）头，不应跨代理传递。
 */
const SKIPPED_RESPONSE_HEADERS = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'content-encoding',
  'upgrade',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
]);

export default {
  /**
   * Worker 入口。
   * @param {Request} request 客户端（插件）发来的请求
   * @param {{ PROXY_TOKEN?: string }} env 环境变量，PROXY_TOKEN 为可选鉴权口令
   * @param {ExecutionContext} ctx 执行上下文（此处未使用）
   * @returns {Promise<Response>}
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1) 方法校验：插件只会发 GET / POST，另外放行 HEAD / OPTIONS 便于手动自检。
    if (!ALLOWED_METHODS.has(request.method)) {
      return jsonResponse(405, { error: 'method not allowed' }, { allow: 'GET, POST, HEAD, OPTIONS' });
    }
    if (request.method === 'OPTIONS') {
      // 插件是服务端 HTTP 客户端，不存在浏览器同源策略，所以不需要任何 CORS 头。
      return new Response(null, { status: 204, headers: { allow: 'GET, POST, HEAD, OPTIONS' } });
    }

    // 2) 可选鉴权：只有设置了 PROXY_TOKEN 才校验。
    const expectedToken = typeof env.PROXY_TOKEN === 'string' ? env.PROXY_TOKEN.trim() : '';
    if (expectedToken && !isAuthorized(request, url, expectedToken)) {
      return jsonResponse(403, { error: 'invalid or missing proxy token' });
    }

    // 3) /img 是插件自定义的预览图代理路由（soutubot 本身没有这个接口）。
    if (url.pathname === '/img') {
      return handleImageProxy(request, url);
    }

    // 4) 其余路径按白名单转发到 soutubot.moe。
    if (!isAllowedPath(url.pathname)) {
      return jsonResponse(404, { error: 'path not allowed by this proxy' });
    }
    return handleUpstreamProxy(request, url);
  },
};

/**
 * 鉴权校验。普通路由只认 X-Proxy-Token 请求头；/img 由于可能被直接当图片链接使用，
 * 额外接受 ?token= 查询参数。
 * @param {Request} request
 * @param {URL} url
 * @param {string} expectedToken
 * @returns {boolean}
 */
function isAuthorized(request, url, expectedToken) {
  const headerToken = request.headers.get('x-proxy-token');
  if (headerToken && isTokenEqual(headerToken, expectedToken)) {
    return true;
  }
  if (url.pathname === '/img') {
    const queryToken = url.searchParams.get('token');
    if (queryToken && isTokenEqual(queryToken, expectedToken)) {
      return true;
    }
  }
  return false;
}

/**
 * 定长比较，避免通过响应时间差逐字节猜口令。
 * @param {string} provided
 * @param {string} expected
 * @returns {boolean}
 */
function isTokenEqual(provided, expected) {
  if (provided.length !== expected.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < expected.length; i += 1) {
    diff |= provided.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return diff === 0;
}

/**
 * 路径白名单判断。
 * @param {string} pathname
 * @returns {boolean}
 */
function isAllowedPath(pathname) {
  if (ALLOWED_EXACT_PATHS.has(pathname)) {
    return true;
  }
  return ALLOWED_PATH_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

/**
 * 把请求转发给 soutubot.moe：只替换 origin，路径与查询串保持不变。
 * @param {Request} request
 * @param {URL} url
 * @returns {Promise<Response>}
 */
async function handleUpstreamProxy(request, url) {
  const upstreamUrl = UPSTREAM_ORIGIN + url.pathname + url.search;
  const hasBody = request.method !== 'GET' && request.method !== 'HEAD';

  const init = {
    method: request.method,
    headers: buildUpstreamHeaders(request.headers),
    // multipart/form-data 直接流式转发，不解析、不重新编码，boundary 与字节完全保持原样。
    body: hasBody ? request.body : null,
    // 让 Worker 自己跟随 3xx；否则 Location 会把插件重新指回 soutubot.moe，代理就白做了。
    redirect: 'follow',
  };
  const signal = createTimeoutSignal();
  if (signal) {
    init.signal = signal;
  }

  let upstreamResponse;
  try {
    upstreamResponse = await fetch(upstreamUrl, init);
  } catch (error) {
    return jsonResponse(502, { error: 'upstream request failed: ' + describeError(error) });
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: copyResponseHeaders(upstreamResponse.headers, false),
  });
}

/**
 * /img?url=<urlencoded 图片直链>[&token=...]
 * 取出 url 参数，校验 host 在白名单内，然后把图片字节流原样回传。
 * @param {Request} request
 * @param {URL} url
 * @returns {Promise<Response>}
 */
async function handleImageProxy(request, url) {
  const rawTarget = url.searchParams.get('url');
  if (!rawTarget) {
    return jsonResponse(400, { error: 'missing url parameter' });
  }

  let target;
  try {
    target = new URL(rawTarget);
  } catch (error) {
    return jsonResponse(400, { error: 'url parameter is not a valid absolute URL' });
  }

  // 只允许 http / https，挡掉 file:、data:、blob: 之类的协议。
  if (target.protocol !== 'https:' && target.protocol !== 'http:') {
    return jsonResponse(403, { error: 'unsupported url scheme' });
  }
  if (!isHostAllowed(target.hostname, IMAGE_HOST_WHITELIST)) {
    return jsonResponse(403, { error: 'image host not allowed: ' + target.hostname });
  }

  // 图片站只关心 UA 与 Referer；同样原样透传插件发来的 UA（不要改写）。
  const headers = new Headers();
  copyHeaderIfPresent(request.headers, headers, 'user-agent');
  copyHeaderIfPresent(request.headers, headers, 'accept-language');
  headers.set('accept', request.headers.get('accept') || 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8');
  headers.set('referer', request.headers.get('referer') || UPSTREAM_ORIGIN + '/');

  const init = { method: request.method === 'HEAD' ? 'HEAD' : 'GET', headers, redirect: 'follow' };
  const signal = createTimeoutSignal();
  if (signal) {
    init.signal = signal;
  }

  let imageResponse;
  try {
    imageResponse = await fetch(target.toString(), init);
  } catch (error) {
    return jsonResponse(502, { error: 'image fetch failed: ' + describeError(error) });
  }

  // 图片一般不会被运行时解压，可以保留上游的 Content-Type 与 Content-Length。
  const responseHeaders = copyResponseHeaders(imageResponse.headers, true);
  if (imageResponse.ok) {
    responseHeaders.set('cache-control', 'public, max-age=86400');
  }
  return new Response(imageResponse.body, {
    status: imageResponse.status,
    statusText: imageResponse.statusText,
    headers: responseHeaders,
  });
}

/**
 * host 白名单判断：完全相等，或者是白名单域名的子域。
 * 不使用 includes()，避免 soutubot.moe.evil.com 之类的地址绕过检查。
 * @param {string} hostname
 * @param {string[]} whitelist
 * @returns {boolean}
 */
function isHostAllowed(hostname, whitelist) {
  const host = hostname.toLowerCase();
  return whitelist.some((allowed) => host === allowed || host.endsWith('.' + allowed));
}

/**
 * 构造转发给上游的请求头：白名单内原样复制，其余全部丢弃。
 * 特别注意 user-agent 必须逐字节一致，否则 md5 签名失效（上游返回 401）。
 * @param {Headers} sourceHeaders
 * @returns {Headers}
 */
function buildUpstreamHeaders(sourceHeaders) {
  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    if (isBlockedRequestHeader(name)) {
      continue;
    }
    copyHeaderIfPresent(sourceHeaders, headers, name);
  }
  return headers;
}

/**
 * 是否为禁止转发的请求头（Cloudflare 注入头、代理链路头、本地鉴权头）。
 * @param {string} name 头名（大小写不敏感）
 * @returns {boolean}
 */
function isBlockedRequestHeader(name) {
  const lower = name.toLowerCase();
  if (BLOCKED_REQUEST_HEADERS.has(lower)) {
    return true;
  }
  return BLOCKED_REQUEST_HEADER_PREFIXES.some((prefix) => lower.startsWith(prefix));
}

/**
 * 存在则复制一个请求头，值不做任何加工。
 * @param {Headers} source
 * @param {Headers} target
 * @param {string} name
 */
function copyHeaderIfPresent(source, target, name) {
  const value = source.get(name);
  if (value !== null) {
    target.set(name, value);
  }
}

/**
 * 复制上游响应头：保留 Content-Type 等业务头，逐条保留多个 Set-Cookie，丢弃逐跳头。
 * Set-Cookie 单独用 getSetCookie() 处理，避免多个 Cookie 被合并成一行、
 * 导致客户端只认到第一个。
 * @param {Headers} source
 * @param {boolean} keepContentLength 是否保留 Content-Length（仅图片透传时为 true）
 * @returns {Headers}
 */
function copyResponseHeaders(source, keepContentLength) {
  const headers = new Headers();
  for (const [name, value] of source.entries()) {
    const lower = name.toLowerCase();
    if (lower === 'set-cookie') {
      continue; // 见下方单独处理
    }
    if (SKIPPED_RESPONSE_HEADERS.has(lower)) {
      continue;
    }
    if (lower === 'content-length' && !keepContentLength) {
      continue;
    }
    headers.set(name, value);
  }

  if (typeof source.getSetCookie === 'function') {
    for (const cookie of source.getSetCookie()) {
      headers.append('set-cookie', cookie);
    }
  } else {
    // 极老的运行时没有 getSetCookie()，退化为单值处理。
    const cookie = source.get('set-cookie');
    if (cookie !== null) {
      headers.append('set-cookie', cookie);
    }
  }
  return headers;
}

/**
 * 生成上游请求的超时信号；运行时不支持 AbortSignal.timeout 时返回 null。
 * @returns {AbortSignal | null}
 */
function createTimeoutSignal() {
  if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
    return AbortSignal.timeout(UPSTREAM_TIMEOUT_MS);
  }
  return null;
}

/**
 * 把异常转成一句简短描述，避免把内部细节大量暴露给调用方。
 * @param {unknown} error
 * @returns {string}
 */
function describeError(error) {
  if (error && typeof error === 'object' && error.name === 'TimeoutError') {
    return 'timeout after ' + UPSTREAM_TIMEOUT_MS + 'ms';
  }
  if (error && typeof error === 'object' && typeof error.message === 'string') {
    return error.message.slice(0, 200);
  }
  return String(error).slice(0, 200);
}

/**
 * 统一构造 JSON 响应。
 * @param {number} status
 * @param {Record<string, unknown>} payload
 * @param {Record<string, string>} [extraHeaders]
 * @returns {Response}
 */
function jsonResponse(status, payload, extraHeaders) {
  const headers = new Headers({ 'content-type': 'application/json; charset=utf-8' });
  if (extraHeaders) {
    for (const [name, value] of Object.entries(extraHeaders)) {
      headers.set(name, value);
    }
  }
  return new Response(JSON.stringify(payload), { status, headers });
}
