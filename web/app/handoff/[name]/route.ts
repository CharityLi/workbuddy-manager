import {readFile} from 'node:fs/promises';
import path from 'node:path';

/**
 * 描述文件下发（2026-10-23 ✓）
 *
 * 为什么需要这个 route：
 *   AirDrop 对 `.mobileconfig` 经常直接拒收 ✗
 *   微信 / QQ 传过去会被改名或拦下 ✗
 *   ⇒ 最可靠的是「手机用 Safari 直接从这台 Mac 下载」✓
 *     手机本来就访问得到 `192.168.31.28:3000` ✓
 *
 * 为什么不放 `app/api/*`：
 *   `next.config.ts` 把 `/api/:path*` **全部 rewrite 到后端 7864** ✗
 *   放在 `/api` 下会被转发走，这个 route 根本收不到请求 ✓
 *   ⇒ 改用 `/handoff/*` ✓ 与现有 rewrite 无交集 ✓
 *
 * 为什么不能只靠 `public/` 里那份：
 *   Next 的静态目录对未知扩展名返回 `application/octet-stream` ✗
 *   iOS 靠 MIME 判断是否进入「安装描述文件」流程 ✓
 *   标准 MIME 是 `application/x-apple-aspen-config` ✓
 *   静态目录改不了 header ⇒ 必须走 route handler ✓
 *
 * ★ 白名单而非拼接路径：`[name]` 来自 URL，直接 path.join 会被 `../` 穿越 ✗
 */

const ALLOWED = new Set(['wb-lan-host.mobileconfig', 'wb-lan-ip.mobileconfig']);

export async function GET(
  _req: Request,
  {params}: {params: Promise<{name: string}>},
) {
  const {name} = await params;

  // ★ 只允许名单内的文件 ✓ 其余一律 404 ✓（同时挡住路径穿越）
  if (!ALLOWED.has(name)) {
    return new Response('Not Found', {status: 404});
  }

  try {
    const file = path.join(process.cwd(), 'public', name);
    const buf = await readFile(file);

    return new Response(new Uint8Array(buf), {
      status: 200,
      headers: {
        // iOS / macOS 看到这个 MIME 才会走描述文件安装流程 ✓
        'Content-Type': 'application/x-apple-aspen-config',
        // 强制下载，避免 Safari 内联显示 XML 源码 ✗
        'Content-Disposition': `attachment; filename="${name}"`,
        'Content-Length': String(buf.byteLength),
        'Cache-Control': 'no-store',
      },
    });
  } catch {
    return new Response('配置未生成，请联系管理员', {status: 404});
  }
}
