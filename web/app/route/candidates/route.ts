import os from 'node:os';
import {NextResponse} from 'next/server';

/**
 * 移动端自动选路：候选地址表（2026-10-26）
 *
 * 为什么需要这一层
 * ----------------
 * 同一台 Mac 上有两条完全独立的接入路径，可靠性不同：
 *
 *   · 局域网 `192.168.x.x`（en0）—— 同一 WiFi 下延迟最低，但出了这个 WiFi 就死。
 *   · Tailscale `100.x`（utun）—— **任何网络下都通**：同网段直连、跨网走 DERP 中继。
 *
 * 于是「书签该存哪个」没有正确答案：存局域网，出门就不能用；存 Tailscale，
 * 在家又白白多走一跳加密。让前端在页面里实测再决定，比让用户记两个地址合理。
 *
 * 为什么不写死在代码里
 * --------------------
 * 路由器重新分配、Tailscale 换 IP、换了 WiFi，写死的地址就会指向空气，而且
 * 失败形态是「页面白屏」——最难排查的那种。这里每次请求现读网卡，地址变了自动跟上。
 *
 * 必须排除的网段（否则会把不可用地址当成候选推给前端）
 * --------------------------------------------------
 *   · `198.18.0.0/15` —— Clash / Surge 之类代理工具的 TUN 网卡。它存在于
 *     `ifconfig` 里，但**不能用来访问本机服务**，混进候选会让探测白等一轮超时。
 *   · `169.254.0.0/16` —— link-local，只在 DHCP 失败时出现，没有实际路由。
 *   · `127.0.0.0/8` —— 回环，手机访问本来就是从外部来的，回环地址毫无意义。
 */

// 每次请求都重新读网卡：候选必须反映「此刻」的真实地址状态，不能被构建期缓存。
export const dynamic = 'force-dynamic';

/** IPv4 点分十进制 → 32 位无符号整数，用于 CIDR 归属判断。 */
function ipv4ToInt(ip: string): number | null {
	const parts = ip.split('.');
	if (parts.length !== 4) return null;
	let out = 0;
	for (const p of parts) {
		const n = Number(p);
		if (!Number.isInteger(n) || n < 0 || n > 255) return null;
		out = (out << 8) | n;
	}
	// >>> 0 把有符号位移的结果转回无符号，避免 192.x 开头出现负数
	return out >>> 0;
}

/** 判断 ip 是否落在 base/bits 指定的网段内。解析失败一律返回 false。 */
function inCidr(ip: string, base: string, bits: number): boolean {
	const a = ipv4ToInt(ip);
	const b = ipv4ToInt(base);
	if (a === null || b === null) return false;
	if (bits <= 0) return true;
	if (bits >= 32) return a === b;
	const mask = (0xffffffff << (32 - bits)) >>> 0;
	return (a & mask) === (b & mask);
}

type Kind = 'lan' | 'tailscale' | 'mdns';

interface Candidate {
	host: string;
	kind: Kind;
	label: string;
	/**
	 * 探测优先级，数字小的先试。局域网排在 Tailscale 前：
	 * 局域网通了它就一定比中继快；不通时探测会在超时窗口内失败，代价可控。
	 */
	priority: number;
}

interface Excluded {
	host: string;
	iface: string;
	reason: string;
}

export async function GET(req: Request) {
	const candidates: Candidate[] = [];
	const excluded: Excluded[] = [];

	const ifaces = os.networkInterfaces();
	for (const [name, addrs] of Object.entries(ifaces)) {
		for (const addr of addrs ?? []) {
			// 只收 IPv4：手机浏览器访问 IPv6 链路本地地址的可用性极不稳定，
			// 而且 macOS 的 IPv6 地址在每次重连后都会变，不适合做书签。
			if (addr.family !== 'IPv4') continue;
			const ip = addr.address;

			if (addr.internal || inCidr(ip, '127.0.0.0', 8)) {
				// 回环不记录，避免诊断输出被 127.0.0.1 刷屏
				continue;
			}
			if (inCidr(ip, '198.18.0.0', 15)) {
				excluded.push({host: ip, iface: name, reason: '代理 TUN 网段（Clash/Surge），不能访问本机服务'});
				continue;
			}
			if (inCidr(ip, '169.254.0.0', 16)) {
				excluded.push({host: ip, iface: name, reason: 'link-local，无实际路由'});
				continue;
			}

			// Tailscale 使用 CGNAT 段 100.64.0.0/10 —— 这是它的固定分配范围，
			// 判断依据是网段而非网卡名（utun 编号每次连接都会变）。
			if (inCidr(ip, '100.64.0.0', 10)) {
				candidates.push({host: ip, kind: 'tailscale', label: 'Tailscale', priority: 2});
				continue;
			}
			if (
				inCidr(ip, '192.168.0.0', 16) ||
				inCidr(ip, '10.0.0.0', 8) ||
				inCidr(ip, '172.16.0.0', 12)
			) {
				candidates.push({host: ip, kind: 'lan', label: '局域网', priority: 1});
				continue;
			}

			excluded.push({host: ip, iface: name, reason: '非私有网段，疑似虚拟网卡或 VPN 出口'});
		}
	}

	// mDNS 候选：iOS / macOS 原生支持，Android 10+ 多数可用。放在最后兜底——
	// 它解析依赖 Bonjour，跨 VLAN 或部分安卓机型会静默失败。
	const localHost = os.hostname().replace(/\.local$/i, '');
	if (localHost) {
		candidates.push({
			host: `${localHost}.local`,
			kind: 'mdns',
			label: 'Bonjour（.local）',
			priority: 3
		});
	}

	// 去重：多网卡（USB 网卡 / 桥接）可能报出同一个地址
	const seen = new Set<string>();
	const unique = candidates.filter((c) => {
		if (seen.has(c.host)) return false;
		seen.add(c.host);
		return true;
	});
	unique.sort((a, b) => a.priority - b.priority);

	// 请求自己是从哪个 host 进来的 —— 这个地址**必然可达**（否则页面打不开），
	// 前端应把它当作基准而非盲目探测。
	const hostHeader = req.headers.get('host') ?? '';
	const currentHost = hostHeader.replace(/:\d+$/, '') || null;
	const port = Number(hostHeader.match(/:(\d+)$/)?.[1] ?? 3000);

	return NextResponse.json({
		ok: true,
		port,
		current: currentHost,
		candidates: unique,
		excluded
	});
}
