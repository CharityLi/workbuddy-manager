/**
 * 把 User-Agent 归成一个「渠道」名，用于请求日志的「渠道」列。
 *
 * 为什么按 UA 而不是按密钥：同一个人可能既用 VS Code 插件又用命令行脚本，
 * 密钥是同一个。真正决定"这次调用从哪来"的是客户端自己报的 UA —— 它虽可伪造，
 * 但用来给用户自己看「我哪些工具在打请求」足够。
 *
 * 归类顺序有讲究：先认具体的（codex/vscode/cursor），再认通用的库名，
 * 否则 `OpenAI/Python ... vscode` 这类串会被归到「API」而不是「VS Code」。
 */
export function channelOf(ua: string | null | undefined): string {
  const s = (ua || '').toLowerCase();
  if (!s.trim()) return '';
  if (s.includes('codex')) return 'Codex';
  if (s.includes('cursor')) return 'Cursor';
  if (s.includes('vscode') || s.includes('visual studio code')) return 'VS Code';
  if (s.includes('workbuddy')) return 'WorkBuddy';
  if (s.includes('deepseek')) return 'DeepSeek';
  if (s.includes('claude code') || s.includes('anthropic-cli')) return 'Claude Code';
  if (s.includes('jetbrains') || s.includes('intellij')) return 'JetBrains';
  if (s.includes('postman')) return 'Postman';
  if (s.includes('insomnia')) return 'Insomnia';
  if (
    s.includes('python') || s.includes('requests') || s.includes('httpx') ||
    s.includes('openai-python') || s.includes('go-http') || s.includes('axios') ||
    s.includes('node') || s.includes('curl') || s.includes('okhttp') ||
    s.includes('java/') || s.includes('apache-httpclient')
  ) return 'API';
  if (s.includes('mozilla/') || s.includes('chrome') || s.includes('safari') ||
      s.includes('firefox') || s.includes('edg/')) return 'Web';
  // 兜底：取 UA 的第一段（通常是产品名），过长则截断，避免把整串塞进单元格
  const head = (ua || '').split(/[\s/]/)[0] || '';
  return head.slice(0, 14);
}
