// 构建后注入后端地址
import fs from 'fs'
import path from 'path'

const assetsDir = path.join(process.cwd(), 'dist', 'assets')
if (!fs.existsSync(assetsDir)) {
  console.error('dist/assets not found')
  process.exit(1)
}

const API_BASE = 'http://100.71.165.71:8000'
let patched = 0

for (const file of fs.readdirSync(assetsDir)) {
  if (!file.endsWith('.js')) continue
  const fp = path.join(assetsDir, file)
  let content = fs.readFileSync(fp, 'utf-8')

  // 替换相对路径 fetch('/chat', ... 为绝对路径
  const replacements = [
    [/fetch\(\"\\\/chat\"/g, `fetch("${API_BASE}/chat"`],
    [/fetch\(\'\\\/chat\'/g, `fetch('${API_BASE}/chat'`],
    [/fetch\(\"\\\/upload\"/g, `fetch("${API_BASE}/upload"`],
    [/fetch\(\"\\\/cv\"/g, `fetch("${API_BASE}/cv"`],
    [/fetch\(\"\\\/cv\/progress\"/g, `fetch("${API_BASE}/cv/progress"`],
    [/fetch\(\"\\\/cv\/upload-video\"/g, `fetch("${API_BASE}/cv/upload-video"`],
    [/fetch\(\"\\\/media\"/g, `fetch("${API_BASE}/media"`],
    [/setVideoSrc\(\$\{API_BASE\}\+"\/videos\//g, `setVideoSrc("${API_BASE}/videos/`],
  ]

  for (const [pattern, replacement] of replacements) {
    if (pattern.test(content)) {
      content = content.replace(pattern, replacement)
      patched++
    }
  }

  fs.writeFileSync(fp, content, 'utf-8')
  console.log(`Patched: ${file} (+${patched} replacements)`)
}

console.log(`Done. Total replacements: ${patched}`)
