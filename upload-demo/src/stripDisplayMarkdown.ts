/**
 * 将模型返回的 Markdown 粗体/标题/列表等转为纯文本，避免界面上出现 * # ** 等符号。
 * 在时间戳高亮等逻辑之前调用。
 */
export function stripDisplayMarkdown(raw: string): string {
  if (!raw) return ''
  let s = raw

  // 围栏代码块 ``` ... ```
  s = s.replace(/```[\w]*\s*\n?([\s\S]*?)```/g, (_, inner: string) =>
    inner.trim(),
  )

  // 行内 `code`
  s = s.replace(/`([^`]+)`/g, '$1')

  // **粗体** / __粗体__（多轮处理嵌套）
  for (let i = 0; i < 6; i++) {
    const next = s
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/__([^_]+)__/g, '$1')
    if (next === s) break
    s = next
  }

  // 行首 # 标题
  s = s.replace(/^#{1,6}\s+/gm, '')

  // 水平分隔线
  s = s.replace(/^\s*-{3,}\s*$/gm, '')

  // 无序列表行首
  s = s.replace(/^\s*[*+-]\s+/gm, '')

  // 斜体 *x*（在去掉 ** 之后）
  s = s.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '$1')

  // 链接 [文本](url) -> 文本
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')

  return s.replace(/\n{3,}/g, '\n\n').trim()
}
