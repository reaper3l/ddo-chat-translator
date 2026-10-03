// 公共词典「贡献回传」收件端（Cloudflare Workers 免费版就能跑）
//
// 部署步骤（一次就够，约 10 分钟）：
//   1. 注册/登录 Cloudflare → Workers & Pages → Create → Worker，起个名字；
//   2. 把本文件内容整段粘进在线编辑器，Deploy；
//   3. 左边 Storage & Databases → KV → Create namespace，名字随便（例如 ddo-contrib）；
//   4. 回到 Worker → Settings → Bindings → 添加 KV Namespace：
//         Variable name 填 DDO_KV，Namespace 选刚建的那个；
//   5. 同一个页面加一个环境变量并勾上 Encrypt：
//         ADMIN_TOKEN = 一串你自己编的长随机串（只放你本机，绝不写进程序）；
//   6. Deploy。之后 Worker 的地址（形如 https://xxx.yyy.workers.dev）填到程序
//      data/config.json 的 contribute_url 里，用户端就能"直接上传"了。
//
// 取数据（作者本机）：
//   curl.exe -s "https://xxx.yyy.workers.dev/?token=<ADMIN_TOKEN>" > 贡献.json
//   然后：python tools\collect_contributions.py --json 贡献.json --out 候选
//
// 安全边界：
//   * 只接受 POST（写入）和带 token 的 GET（导出）；token 只在你本机，不进程序；
//   * 按 IP 限流、限制请求体大小、只收合法 JSON；
//   * 收到的内容一律当不可信输入：真正的过滤在 python 侧的自动闸门里做。

const MAX_BODY = 200 * 1024;      // 单次贡献最多 200KB
const RATE_PER_HOUR = 30;         // 同一 IP 每小时最多 30 次

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "POST") {
      const ip = request.headers.get("CF-Connecting-IP") || "unknown";
      const bucket = "rate:" + ip + ":" + new Date().toISOString().slice(0, 13);
      const used = parseInt((await env.DDO_KV.get(bucket)) || "0", 10);
      if (used >= RATE_PER_HOUR) {
        return json({ ok: false, error: "too many requests" }, 429);
      }
      await env.DDO_KV.put(bucket, String(used + 1), { expirationTtl: 3600 });

      const body = await request.text();
      if (body.length > MAX_BODY) return json({ ok: false, error: "too large" }, 413);
      let parsed;
      try {
        parsed = JSON.parse(body);
      } catch (err) {
        return json({ ok: false, error: "bad json" }, 400);
      }
      if (!parsed || typeof parsed.uid !== "string") {
        return json({ ok: false, error: "missing uid" }, 400);
      }
      const id = crypto.randomUUID();
      await env.DDO_KV.put("c:" + id, body);
      return json({ ok: true });
    }

    if (request.method === "GET") {
      const token = url.searchParams.get("token") || "";
      if (!env.ADMIN_TOKEN || token !== env.ADMIN_TOKEN) {
        return json({ ok: false, error: "forbidden" }, 403);
      }
      const out = [];
      let cursor = undefined;
      do {
        const page = await env.DDO_KV.list({ prefix: "c:", cursor });
        for (const key of page.keys) {
          const value = await env.DDO_KV.get(key.name);
          if (value) {
            try {
              out.push(JSON.parse(value));
            } catch (err) {
              // 坏数据跳过，不影响导出
            }
          }
        }
        cursor = page.list_complete ? undefined : page.cursor;
      } while (cursor);
      return json(out);
    }

    return json({ ok: false, error: "method not allowed" }, 405);
  },
};
