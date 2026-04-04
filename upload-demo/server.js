import express from 'express'
import cors from 'cors'
import multer from 'multer'
import path from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const app = express()
const PORT = 3000

// 启用 CORS，允许前端访问
app.use(cors())
app.use(express.json())

// 配置 multer 用于文件上传（内存存储，不保存到磁盘）
const storage = multer.memoryStorage()
const upload = multer({ 
  storage: storage,
  limits: { fileSize: 10 * 1024 * 1024 } // 限制 10MB
})

// 模拟后端返回的文本列表数据
const generateTextList = () => {
  const now = new Date()
  return [
    { 
      id: 1, 
      text: '这是第一条文本内容，从后端 API 返回的 JSON 数据。', 
      timestamp: now.toLocaleString('zh-CN') 
    },
    { 
      id: 2, 
      text: '第二条文本：文件上传成功，后端已处理并返回文本列表数据。', 
      timestamp: now.toLocaleString('zh-CN') 
    },
    { 
      id: 3, 
      text: '第三条文本内容，展示后端返回的 JSON 数据结构。', 
      timestamp: now.toLocaleString('zh-CN') 
    },
    { 
      id: 4, 
      text: '第四条：这是一个较长的文本内容，用于测试文本区域的换行和显示效果，确保长文本能够正确展示。这些数据是从后端服务器返回的真实 JSON 数据。', 
      timestamp: now.toLocaleString('zh-CN') 
    },
    { 
      id: 5, 
      text: '第五条文本，验证后端 API 返回数据的完整性和格式。', 
      timestamp: now.toLocaleString('zh-CN') 
    },
    { 
      id: 6, 
      text: '第六条：这是后端返回的额外数据，证明 API 正常工作。', 
      timestamp: now.toLocaleString('zh-CN') 
    },
  ]
}

// 文件上传接口
app.post('/api/upload', upload.single('file'), (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: '没有上传文件' })
    }

    console.log(`收到文件上传: ${req.file.originalname}, 大小: ${req.file.size} bytes`)

    // 模拟处理时间
    setTimeout(() => {
      // 返回文本列表 JSON 数据
      const textList = generateTextList()
      
      res.json({
        success: true,
        message: '文件上传成功',
        filename: req.file.originalname,
        fileSize: req.file.size,
        data: textList  // 返回文本列表
      })
    }, 500) // 模拟 500ms 的处理时间

  } catch (error) {
    console.error('上传处理错误:', error)
    res.status(500).json({ error: '服务器处理错误' })
  }
})

// 健康检查接口
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', message: '后端服务运行正常' })
})

app.listen(PORT, () => {
  console.log(`\n🚀 后端服务器已启动`)
  console.log(`📍 服务地址: http://localhost:${PORT}`)
  console.log(`📤 上传接口: http://localhost:${PORT}/api/upload`)
  console.log(`💚 健康检查: http://localhost:${PORT}/api/health\n`)
})
