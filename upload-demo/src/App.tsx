import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import './App.css'
import { stripDisplayMarkdown } from './stripDisplayMarkdown'

// 开发环境：走 Vite 代理（见 vite.config.ts），请求同源 /health、/upload 等，避免浏览器跨域拦截。
// 生产构建：默认同域（API_BASE 为空时会拼出 /chat 等相对路径）。
// 如需跨域直连后端，请在构建时提供 VITE_API_BASE_URL，例如 https://example.com:8000
const API_BASE = import.meta.env.DEV
  ? ''
  : import.meta.env.VITE_API_BASE_URL || ''

type Role = 'user' | 'assistant'

interface ChatMessage {
  id: number
  role: Role
  text: string
}

interface HistoryRecord {
  id: string
  title: string
  messages: ChatMessage[]
  videoTitle: string
  videoId: string
  savedPath: string
  createdAt: number
  updatedAt: number
}

interface SearchResult {
  id: number
  title: string
  snippet: string
  timeText: string
  seconds: number
}

interface CvResultItem {
  time_offset: number | null
  text: string
  frame_url: string | null
}

// 兼容 /chat 的 SSE 返回：提取每条 data: 后的 JSON 文本
async function readSseAnswer(resp: Response): Promise<string> {
  if (!resp.body) throw new Error('后端未返回可读数据流')
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let answer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const rawLine of lines) {
      const line = rawLine.trim()
      if (!line.startsWith('data:')) continue
      const payload = line.slice(5).trim()
      if (!payload || payload === '[DONE]') continue
      let obj: { type?: string; text?: string }
      try {
        obj = JSON.parse(payload) as { type?: string; text?: string }
      } catch {
        continue
      }
      if (obj.type === 'error' && typeof obj.text === 'string') {
        throw new Error(obj.text)
      }
      if (obj.type === 'content' && typeof obj.text === 'string') {
        answer += obj.text
      }
    }
  }

  return answer.trim()
}

// 将 "mm:ss" 文本转换为秒数
function timeTextToSeconds(timeText: string): number | null {
  const match = /^(\d{1,2}):([0-5]\d)$/.exec(timeText.trim())
  if (!match) return null
  const minutes = Number(match[1])
  const seconds = Number(match[2])
  return minutes * 60 + seconds
}

// 将消息中的时间戳（如 01:15）高亮并可点击
function renderHighlightedMessage(
  text: string,
  onJump: (seconds: number) => void,
): React.ReactElement {
  // 匹配 0:15、00:15、12:30 等时间戳
  // 不使用 \b，避免和中文字符之间的"单词边界"识别不一致导致无法高亮
  const TIMESTAMP_REG = /(\d{1,2}:[0-5]\d)/g
  const parts: React.ReactElement[] = []
  let lastIndex = 0
  let key = 0

  text.replace(TIMESTAMP_REG, (match, _g1, offset) => {
    if (offset > lastIndex) {
      parts.push(
        <span key={key++}>{text.slice(lastIndex, offset)}</span>,
      )
    }

    const seconds = timeTextToSeconds(match)
    if (seconds != null) {
      parts.push(
        <button
          key={key++}
          type="button"
          className="timestamp-link"
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            console.log(`点击时间戳: ${match}，转换为秒数: ${seconds}`)
            onJump(seconds)
          }}
        >
          {match}
        </button>,
      )
    } else {
      // 如果转换失败，仍然显示文本
      parts.push(<span key={key++}>{match}</span>)
    }

    lastIndex = offset + match.length
    return match
  })

  if (lastIndex < text.length) {
    parts.push(<span key={key++}>{text.slice(lastIndex)}</span>)
  }

  return <>{parts}</>
}

/** 未上传视频时的占位讲义（与后端无关） */
const DEMO_LECTURE =
  '示例讲义：本视频主要讲解 XXX 的核心概念、应用场景与实现步骤。你可以复制这段文字，用于汇报材料或知识库整理。'

function App() {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const chatEndRef = useRef<HTMLDivElement | null>(null)
  const [backendReady, setBackendReady] = useState(false)
  const [backendStatusText, setBackendStatusText] = useState('正在检测后端连接...')
  const [videoSrc, setVideoSrc] = useState(
    'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4',
  )
  const [videoTitle, setVideoTitle] = useState('示例教学视频')
  const [videoId, setVideoId] = useState<string>('demo-video')
  const [isUploading, setIsUploading] = useState(false)
  const [cvGate, setCvGate] = useState<{
    expectedVideoName: string
    uploadedAtMs: number
  } | null>(null)
  const [isCvReady, setIsCvReady] = useState(false)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 1,
      role: 'assistant',
      text: '你好，我是你的视频小助手。你可以一边看视频，一边随时提问，比如："01:15 讲了什么？" 我在回答中提到的时间点（如 01:15、02:30）都可以点击，播放器会自动跳到对应位置。',
    },
  ])
  const [isThinking, setIsThinking] = useState(false)
  const [isJumping, setIsJumping] = useState(false) // 视频跳转动效状态
  const [processingProgress, setProcessingProgress] = useState<number | null>(
    null,
  ) // 视频解析 / 处理进度（0-100）
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [snapshots, setSnapshots] = useState<string[]>([])
  /** 精选截图点击放大：大图预览 URL */
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null)
  const localVideoObjectUrlRef = useRef<string | null>(null)
  const [historyPanelOpen, setHistoryPanelOpen] = useState(false)
  const [historyList, setHistoryList] = useState<HistoryRecord[]>([])
  const currentConversationIdRef = useRef<string>(Date.now().toString())

  /** DeepSeek 根据 OCR 生成的讲义；未上传视频时仍用 DEMO_LECTURE */
  const [lectureSummary, setLectureSummary] = useState('')
  const [lectureLoading, setLectureLoading] = useState(false)
  const [lectureError, setLectureError] = useState<string | null>(null)
  /** 文字讲义区域默认折叠，避免长文挤占聊天区 */
  const [lecturePanelOpen, setLecturePanelOpen] = useState(false)
  /** 视频搜索默认折叠，避免多条结果挡住聊天 */
  const [searchPanelOpen, setSearchPanelOpen] = useState(false)

  const prevCvReadyRef = useRef<boolean>(false)

  // 新消息出现时，自动滚动到底部（更像 ChatGPT 的体验）
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [messages.length, isThinking])

  // 用 ref 跟踪当前 messages，避免闭包陷阱
  const messagesRef = useRef(messages)
  messagesRef.current = messages

  // 精选截图大图预览：Esc 关闭；打开时禁止背景滚动
  useEffect(() => {
    if (!lightboxUrl) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLightboxUrl(null)
    }
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [lightboxUrl])

  useEffect(() => {
    return () => {
      if (localVideoObjectUrlRef.current) {
        URL.revokeObjectURL(localVideoObjectUrlRef.current)
      }
    }
  }, [])

  const loadLecture = useCallback(
    async (refresh: boolean) => {
      if (!cvGate || !isCvReady) return
      setLectureLoading(true)
      setLectureError(null)
      try {
        const r = await fetch(
          `${API_BASE}/cv/lecture?refresh=${refresh ? 1 : 0}`,
        )
        const data: {
          ok?: boolean
          summary?: string
          message?: string
          error?: string
          provider?: string
        } = await r.json()
        // 新版讲义接口固定返回 provider=deepseek；没有该字段说明后端仍是旧进程（智谱版）
        if (data.provider !== 'deepseek') {
          setLectureError(
            '当前连上的后端不是最新代码（接口未返回 provider=deepseek）。请关掉所有正在运行的 main.py / uvicorn，再在项目目录重新启动后端，然后点「重新生成」。',
          )
          setLectureSummary('')
          return
        }
        if (data.ok && typeof data.summary === 'string') {
          setLectureSummary(data.summary)
        } else {
          setLectureError(
            String(data.message || data.error || '讲义生成失败'),
          )
          setLectureSummary('')
        }
      } catch (e) {
        setLectureError(e instanceof Error ? e.message : '网络错误')
        setLectureSummary('')
      } finally {
        setLectureLoading(false)
      }
    },
    [cvGate, isCvReady],
  )

  useEffect(() => {
    if (!cvGate) {
      setLectureSummary('')
      setLectureError(null)
      setLectureLoading(false)
    }
  }, [cvGate])

  useEffect(() => {
    if (cvGate && !isCvReady) {
      setLectureSummary('')
      setLectureError(null)
    }
  }, [cvGate, isCvReady])

  useEffect(() => {
    if (!cvGate || !isCvReady) return
    void loadLecture(false)
  }, [cvGate, isCvReady, videoId, loadLecture])

  const displayedKnowledgeText = useMemo(() => {
    let raw: string
    if (!cvGate) raw = DEMO_LECTURE
    else if (!isCvReady) {
      raw = '视频正在解析，完成后将根据 OCR 自动生成文字讲义（DeepSeek）…'
    } else if (lectureLoading && !lectureSummary) {
      raw = '正在根据 OCR 调用 DeepSeek 生成讲义，请稍候…'
    } else if (lectureError) {
      raw = `讲义生成失败\n\n${lectureError}\n\n（讲义与聊天共用 DEEPSEEK_API_KEY，配置在 .env）`
    } else if (lectureSummary) raw = lectureSummary
    else raw = '暂未生成讲义，请点击「重新生成」。'
    return stripDisplayMarkdown(raw)
  }, [cvGate, isCvReady, lectureLoading, lectureSummary, lectureError])

  // 从 localStorage 加载历史记录
  useEffect(() => {
    try {
      const raw = localStorage.getItem('video-chat-history-v1')
      if (raw) setHistoryList(JSON.parse(raw) as HistoryRecord[])
    } catch { /* ignore */ }
  }, [])

  // 保存历史记录到 localStorage（防抖）
  const saveHistory = useCallback((list: HistoryRecord[]) => {
    try {
      localStorage.setItem('video-chat-history-v1', JSON.stringify(list))
    } catch { /* ignore */ }
  }, [])

  // 归档当前对话（每条新消息后自动调用）
  const archiveCurrentConversation = useCallback(
    (messages: ChatMessage[]) => {
      if (messages.length <= 1) return
      const id = currentConversationIdRef.current
      const firstUser = messages.find((m) => m.role === 'user')
      const title =
        firstUser?.text.slice(0, 30).replace(/\n/g, ' ').trim() || '未命名对话'
      const newRecord: HistoryRecord = {
        id,
        title,
        messages,
        videoTitle,
        videoId,
        savedPath: videoId,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }
      setHistoryList((prev) => {
        const idx = prev.findIndex((r) => r.id === id)
        const next =
          idx >= 0
            ? prev.map((r) => (r.id === id ? { ...newRecord } : r))
            : [newRecord, ...prev]
        saveHistory(next)
        return next
      })
    },
    [videoTitle, videoId, videoSrc, saveHistory],
  )

  // 开始新对话
  const startNewConversation = useCallback(() => {
    archiveCurrentConversation(messagesRef.current)
    const newId = Date.now().toString()
    currentConversationIdRef.current = newId
    setMessages([
      {
        id: Date.now() + Math.random(),
        role: 'assistant',
        text: '你好，我是你的视频小助手。你可以一边看视频，一边随时提问，比如："01:15 讲了什么？" 我在回答中提到的时间点（如 01:15、02:30）都可以点击，播放器会自动跳到对应位置。',
      },
    ])
    setHistoryPanelOpen(false)
  }, [messages, archiveCurrentConversation])

  // 加载历史记录
  const loadHistoryRecord = useCallback(
    (record: HistoryRecord) => {
      // 不再先归档当前对话（避免把 OCR 提示消息混入历史）
      // 当前对话的归档由 handleSend 的 finally 自动处理
      currentConversationIdRef.current = record.id
      setMessages(record.messages)

      // 视频恢复：savedPath 形如 "uploads/xxx.mp4"，/videos 挂载在 uploads 目录
      if (record.savedPath) {
        const videoFile = record.savedPath.replace(/^uploads\//, '')
        setVideoSrc(`${API_BASE}/videos/${videoFile}`)
        setVideoId(record.savedPath)
        setVideoTitle(record.videoTitle)
        setCvGate({ expectedVideoName: record.videoTitle, uploadedAtMs: record.createdAt })
        setIsCvReady(true)
        setSnapshots([])
      }

      setHistoryPanelOpen(false)
    },
    [],
  )

  // 删除历史记录
  const deleteHistoryRecord = useCallback(
    (id: string, e: React.MouseEvent) => {
      e.stopPropagation()
      setHistoryList((prev) => {
        const next = prev.filter((r) => r.id !== id)
        saveHistory(next)
        return next
      })
    },
    [saveHistory],
  )

  // 轮询后端健康状态，避免前端"假连接"。
  useEffect(() => {
    let cancelled = false
    let timerId: number | undefined

    const checkHealth = async () => {
      try {
        const resp = await fetch(`${API_BASE}/health`, { method: 'GET' })
        if (!cancelled && resp.ok) {
          setBackendReady(true)
          setBackendStatusText(`后端已连接：${API_BASE}`)
          return
        }
      } catch {
        // ignore
      }
      if (!cancelled) {
        setBackendReady(false)
        setBackendStatusText(`后端未连接，请先启动：${API_BASE}`)
      }
    }

    checkHealth()
    timerId = window.setInterval(checkHealth, 5000)
    return () => {
      cancelled = true
      if (timerId) window.clearInterval(timerId)
    }
  }, [])

  const handleCopyKnowledge = async () => {
    const text = stripDisplayMarkdown(
      !cvGate ? DEMO_LECTURE : lectureSummary || displayedKnowledgeText,
    )
    try {
      await navigator.clipboard.writeText(text)
      alert('讲义内容已复制到剪贴板')
    } catch {
      alert('复制失败，请手动选择文本复制')
    }
  }

  // OCR 就绪闸门：等后端处理完"当前上传的视频"，再允许用户提问
  useEffect(() => {
    if (!cvGate) return

    let cancelled = false
    let timerId: number | undefined

    const poll = async () => {
      try {
        const statusUrl = `${API_BASE}/cv/status?video_id=${encodeURIComponent(videoId || '')}`
        const resp = await fetch(statusUrl, { method: 'GET' })
        if (!resp.ok) return
        const data: {
          ran: boolean
          success?: boolean
          video_name?: string
          video_path?: string
          finished_at?: string
        } = await resp.json()

        if (cancelled) return
        if (!data.ran || !data.success) return

        // 后端有时只给 video_name，有时可能给完整 video_path，前端两者都兼容。
        const backendBaseName =
          String(data.video_name || '')
            .split(/[\\/]/)
            .pop() ||
          String(data.video_path || '')
            .split(/[\\/]/)
            .pop() ||
          ''
        const expectedBaseName =
          String(cvGate.expectedVideoName || '')
            .split(/[\\/]/)
            .pop() || ''
        const currentVideoBaseName =
          String(videoId || '')
            .split(/[\\/]/)
            .pop() || ''
        const isSameVideo =
          backendBaseName === expectedBaseName ||
          backendBaseName === currentVideoBaseName

        // 只要后端已经对"当前上传的视频"成功处理完成，就直接放行前端 UI。
        // 不再使用 finished_at >= uploadedAtMs 的时间阈值：后端可能处理得很快，
        // 客户端记录 uploadedAtMs 时 finished_at 已经比它早，导致闸门永远不触发。
        if (isSameVideo && data.success) {
          setIsCvReady(true)
          if (timerId) window.clearInterval(timerId)
        }
      } catch {
        // 轮询失败不阻塞 UI，下一轮继续尝试
      }
    }

    setIsCvReady(false)
    poll()
    timerId = window.setInterval(poll, 2000)

    return () => {
      cancelled = true
      if (timerId) window.clearInterval(timerId)
    }
  }, [cvGate, videoId])

  // 当 OCR 从"未完成"变为"完成"时，给用户一条明确提示，可开始提问。
  useEffect(() => {
    if (!cvGate) return
    const prev = prevCvReadyRef.current
    if (!prev && isCvReady) {
      setMessages((prevMsgs) => [
        ...prevMsgs,
        {
          id: Date.now() + Math.random(),
          role: 'assistant',
          text: 'OCR 已完成，可以直接询问当前视频的内容（例如：30秒处讲了什么？）。',
        },
      ])
    }
    prevCvReadyRef.current = isCvReady
  }, [isCvReady, cvGate])

  // 在指定时间点截取视频帧，返回图片 dataURL
  const captureFrameAt = async (
    video: HTMLVideoElement,
    seconds: number,
  ): Promise<string | null> => {
    if (!video.videoWidth || !video.videoHeight) return null

    await new Promise<void>((resolve) => {
      const handler = () => resolve()
      video.addEventListener('seeked', handler, { once: true })
      video.currentTime = seconds
    })

    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
    try {
      return canvas.toDataURL('image/jpeg', 0.82)
    } catch {
      return null
    }
  }

  // 自动根据视频时长截取几张关键帧
  const generateSnapshots = async (video: HTMLVideoElement) => {
    try {
      if (!video.duration || Number.isNaN(video.duration)) return
      const originalTime = video.currentTime
      const duration = video.duration
      const positions = [
        Math.max(1, duration * 0.1),
        Math.max(1, duration * 0.4),
        Math.max(1, duration * 0.75),
      ]

      const list: string[] = []
      for (const pos of positions) {
        const url = await captureFrameAt(video, Math.min(pos, duration - 0.5))
        if (url) list.push(url)
      }
      setSnapshots(list)
      // 恢复到原来的时间点
      video.currentTime = originalTime
    } catch {
      // 截图失败时保持占位图
    }
  }

  const handleSearch = async () => {
    const q = searchQuery.trim()
    if (!q || isSearching) return

    setIsSearching(true)
    setHasSearched(true)

    try {
      const resp = await fetch(
        `${API_BASE}/cv/search?q=${encodeURIComponent(q)}`,
        { method: 'GET' },
      )
      if (!resp.ok) {
        setSearchResults([])
        return
      }
      const data: {
        ok?: boolean
        items?: Array<{
          title?: string
          snippet?: string
          time_text?: string
          seconds?: number
          time_offset?: number
        }>
      } = await resp.json()
      if (!data.ok || !Array.isArray(data.items)) {
        setSearchResults([])
        return
      }
      const mapped: SearchResult[] = data.items.map((it, i) => ({
        id: Number(it.time_offset ?? i) * 1000 + i,
        title: String(it.title ?? '关键片段'),
        snippet: String(it.snippet ?? ''),
        timeText: String(it.time_text ?? '00:00'),
        seconds: Math.max(
          0,
          Math.floor(
            Number(it.seconds ?? it.time_offset ?? 0),
          ),
        ),
      }))
      setSearchResults(mapped)
    } catch {
      setSearchResults([])
    } finally {
      setIsSearching(false)
    }
  }

  // 生成唯一 id
  const nextId = useMemo(
    () => () => Date.now() + Math.random(),
    [],
  )

  const handleJump = (seconds: number) => {
    const video = videoRef.current
    if (!video) {
      console.error('❌ 视频元素不存在，无法跳转')
      alert('视频元素未找到，请刷新页面重试')
      return
    }

    console.log(`🎯 开始跳转: ${seconds} 秒`)
    console.log(`   视频 URL: ${video.src}`)
    console.log(`   视频 readyState: ${video.readyState} (0=无, 1=元数据, 2=当前帧, 3=未来帧, 4=足够数据)`)
    console.log(`   视频 duration: ${video.duration}`)
    console.log(`   当前时间: ${video.currentTime}`)

    // 检查视频是否已加载
    if (video.readyState === 0) {
      console.warn('⚠️ 视频还未加载，先加载元数据...')
      const onLoadedMetadata = () => {
        console.log('✅ 元数据加载完成，执行跳转')
        performJump(video, seconds)
      }
      video.addEventListener('loadedmetadata', onLoadedMetadata, { once: true })
      video.load()
      return
    }

    // 直接执行跳转
    performJump(video, seconds)
  }

  // 执行实际的跳转操作
  const performJump = (video: HTMLVideoElement, seconds: number) => {
    try {
      // 确保秒数在有效范围内
      if (video.duration && seconds > video.duration) {
        console.warn(`⚠️ 跳转时间 ${seconds} 超过视频长度 ${video.duration}，跳转到末尾`)
        seconds = video.duration - 0.5
      }
      if (seconds < 0) {
        seconds = 0
      }

      console.log(`⏩ 设置 currentTime = ${seconds}`)
      setIsJumping(true)
      video.currentTime = seconds

      // 监听跳转完成事件
      const onSeeked = () => {
        console.log(`✅ 跳转成功！当前时间: ${video.currentTime.toFixed(2)} 秒`)
        // 结束跳转动画
        setTimeout(() => setIsJumping(false), 220)
        // 按产品交互要求：跳转后停在目标时间点，等待用户手动点击播放。
        try {
          video.pause()
        } catch {
          // ignore
        }
      }

      const onError = (e: Event) => {
        console.error('❌ 视频跳转出错:', e)
      }

      video.addEventListener('seeked', onSeeked, { once: true })
      video.addEventListener('error', onError, { once: true })

      // 备用：如果 1 秒后还没触发 seeked，检查是否成功
      setTimeout(() => {
        const diff = Math.abs(video.currentTime - seconds)
        if (diff > 0.5) {
          console.warn(`⚠️ 跳转可能未完全成功，期望 ${seconds}，实际 ${video.currentTime.toFixed(2)}`)
        }
      }, 1000)

    } catch (e) {
      console.error('❌ 设置 currentTime 时出错:', e)
      alert(`跳转失败: ${e instanceof Error ? e.message : '未知错误'}`)
    }
  }

  // 使用支持 Range 的 /media 接口，恢复"你最满意的那一版"上传逻辑
  const handleUpload: React.ChangeEventHandler<HTMLInputElement> = async (e) => {
    const file = e.target.files?.[0]
    if (!file || isUploading) return
    if (!backendReady) {
      alert(`后端未连接，请先启动 FastAPI 服务：${API_BASE}`)
      e.target.value = ''
      return
    }

    if (!file.type.startsWith('video/')) {
      alert('请上传视频文件（mp4、webm 等）')
      return
    }

    setIsUploading(true)
      setProcessingProgress(0)

    try {
      const formData = new FormData()
      formData.append('file', file)

      const resp = await fetch(`${API_BASE}/upload`, {
        method: 'POST',
        body: formData,
      })

      if (!resp.ok) {
        throw new Error(`上传失败，后端状态码: ${resp.status}`)
      }

      const data: {
        filename: string
        saved_as: string
        content_type: string
      } = await resp.json()

      // 新后端未必提供 /media，先用本地 blob URL 保证上传后必定可播放。
      if (localVideoObjectUrlRef.current) {
        URL.revokeObjectURL(localVideoObjectUrlRef.current)
      }
      const objectUrl = URL.createObjectURL(file)
      localVideoObjectUrlRef.current = objectUrl
      setVideoSrc(objectUrl)
      setVideoTitle(data.filename || '已上传视频')
      setVideoId(data.saved_as)
      // 闸门：等后端 cv/result.json 更新到"刚上传的视频"后才允许提问
      const expectedVideoName =
        (data.saved_as ? String(data.saved_as).split(/[\\/]/).pop() : null) || data.filename
      setCvGate({ expectedVideoName, uploadedAtMs: Date.now() })
      setIsCvReady(false)
      setSnapshots([]) // 换新视频时清空旧截图

      // 启动一个前端"解析进度"动画（模拟后端处理进度），后续可以替换为真实接口
      const start = Date.now()
      const duration = 5000 // 5 秒内从 0% 走到 100%
      const timer = setInterval(() => {
        setProcessingProgress((prev) => {
          const elapsed = Date.now() - start
          const ratio = Math.min(1, elapsed / duration)
          const next = Math.max(prev ?? 0, Math.round(ratio * 100))
          if (next >= 100) {
            clearInterval(timer)
            // 稍微停留一下 100%，再隐藏
            setTimeout(() => setProcessingProgress(null), 800)
          }
          return next
        })
        if (Date.now() - start >= duration + 1000) {
          clearInterval(timer)
        }
      }, 200)

      // 等待后端完成标准化转码（H.264 + AAC + yuv420p + faststart）
      // 转码完成后，视频的时间轴和关键帧都是规范的，浏览器可以稳定拖动和跳转
      setTimeout(() => {
        const video = videoRef.current
        if (video) {
          // 强制重新加载视频，确保加载转码后的标准化版本
          video.load()
          const onCanSeek = () => {
            console.log('✅ 视频已准备好（已标准化转码），可以拖动和点击时间戳跳转')
            video.removeEventListener('loadedmetadata', onCanSeek)
            video.removeEventListener('canplay', onCanSeek)
          }
          video.addEventListener('loadedmetadata', onCanSeek, { once: true })
          video.addEventListener('canplay', onCanSeek, { once: true })
        }
      }, 5000) // 等待 5 秒，给转码足够的时间（大视频可能需要更久）
    } catch (err) {
      const msg = err instanceof Error ? err.message : '视频上传失败'
      alert(msg)
    } finally {
      setIsUploading(false)
      e.target.value = ''
    }
  }

  // 当视频元数据加载完成后，自动生成几张截图（仅用于默认演示视频）。
  // 用户上传视频后，截图以 /cv/result 返回的后端抽帧为准。
  useEffect(() => {
    if (cvGate) return
    const video = videoRef.current
    if (!video) return

    const onLoaded = () => {
      generateSnapshots(video)
    }

    video.addEventListener('loadedmetadata', onLoaded)
    // 如果已经加载过 metadata（例如默认示例视频）
    if (video.readyState >= 1) {
      generateSnapshots(video)
    }

    return () => {
      video.removeEventListener('loadedmetadata', onLoaded)
    }
  }, [videoSrc, videoId, cvGate])

  // OCR 完成后，从后端 result.json 拉取真实抽帧截图显示到"知识导图/精选截图"
  useEffect(() => {
    if (!cvGate || !isCvReady) return
    let cancelled = false
    let retryId: number | undefined

    const loadCvSnapshots = async () => {
      try {
        const resultUrl = `${API_BASE}/cv/result?limit=3&video_id=${encodeURIComponent(videoId || '')}`
        const resp = await fetch(resultUrl, { method: 'GET' })
        if (!resp.ok) return
        const data: { ok: boolean; items: CvResultItem[] } = await resp.json()
        if (!data.ok || !Array.isArray(data.items)) return
        const urls = data.items
          .map((it) => it.frame_url)
          .filter((u): u is string => Boolean(u))
          .map((u) => `${API_BASE}${u}?t=${Date.now()}`)
        if (!cancelled && urls.length > 0) {
          setSnapshots(urls)
          if (retryId) window.clearInterval(retryId)
        }
      } catch {
        // 拉取失败时保留占位图，不中断流程
      }
    }

    loadCvSnapshots()
    // 兼容 result.json 稍后写入：就绪后短轮询几次，拿到截图就停止。
    retryId = window.setInterval(loadCvSnapshots, 1500)
    return () => {
      cancelled = true
      if (retryId) window.clearInterval(retryId)
    }
  }, [cvGate, isCvReady, videoId])

  const handleSend = async () => {
    const content = input.trim()
    if (!content || isThinking) return
    if (!backendReady) {
      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        text: `后端未连接，请先启动 FastAPI 服务：${API_BASE}`,
      }
      setMessages((prev) => [...prev, assistantMsg])
      return
    }

    // OCR 结果还没更新到"刚上传的视频"时，只提示风险，不再禁用发送。
    if (cvGate && !isCvReady) {
      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        text: '提示：当前视频仍在 OCR 处理中，现在提问可能会命中上一个视频的结果。建议稍后再问。',
      }
      setMessages((prev) => [...prev, assistantMsg])
    }

    // 如果用户输入像 "40秒处讲了什么 / 跳转视频30秒处 / 01:15 在讲什么"，则：
    // 1) 自动生成"可点击的跳转点"(mm:ss) 并跳转
    // 2) 同时把该时间点作为 current_time 传给后端，让 AI 回答更贴近该片段
    const tryParseJumpSeconds = (text: string): number | null => {
      // 01:15 / 1:05 / 12:30
      const mmss = /(\d{1,2}:[0-5]\d)/.exec(text)
      if (mmss?.[1]) {
        const s = timeTextToSeconds(mmss[1])
        if (s != null) return s
      }
      // 30秒 / 75 秒 / 30s
      const secs = /(\d{1,5})\s*(秒|s)\b/.exec(text)
      if (secs?.[1]) return Number(secs[1])
      // "跳转视频30秒处 / 定位到30处 / 去30左右"这种口语（允许中间夹字，不写单位也行）
      // 例：跳转视频30秒处、跳转到视频30、定位在 75 处
      const loose = /(跳转|定位|到|去)[^\d]{0,8}(\d{1,5})\s*(秒|s|处|左右)?/.exec(text)
      if (loose?.[2]) return Number(loose[2])
      // "40秒处讲了什么 / 40秒讲什么 / 40s在讲什么"
      const askAt = /(\d{1,5})\s*(秒|s)\s*(处)?\s*(讲|说|发生|内容)/.exec(text)
      if (askAt?.[1]) return Number(askAt[1])
      return null
    }

    const jumpSeconds = tryParseJumpSeconds(content)
    const targetTime =
      jumpSeconds != null && Number.isFinite(jumpSeconds) ? jumpSeconds : null

    const userMsg: ChatMessage = {
      id: nextId(),
      role: 'user',
      text: content,
    }

    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setIsThinking(true)

    try {
      // 自动生成"跳转点"（可点击 mm:ss），并执行跳转
      if (targetTime != null) {
        handleJump(targetTime)
        const mm = Math.floor(targetTime / 60)
          .toString()
          .padStart(2, '0')
        const ss = Math.floor(targetTime % 60)
          .toString()
          .padStart(2, '0')
        const jumpHint: ChatMessage = {
          id: nextId(),
          role: 'assistant',
          text: `建议跳转到：${mm}:${ss}（已为你自动跳转）。下面是该时间点的内容说明：`,
        }
        setMessages((prev) => [...prev, jumpHint])
      }

      const sseResp = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          question: content,
          video_id: videoId || undefined,
          // 与后端 nlp_retrieval 时间窗口对齐，优先检索该秒附近的 OCR/表情
          target_seconds:
            targetTime != null && Number.isFinite(targetTime)
              ? targetTime
              : undefined,
        }),
      })

      if (!sseResp.ok) {
        throw new Error(`后端返回错误状态码: ${sseResp.status}`)
      }
      const answerText = await readSseAnswer(sseResp)

      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        text: answerText || '已收到请求，但后端未返回可展示内容。',
      }

      setMessages((prev) => [...prev, assistantMsg])
    } catch (e) {
      const msg =
        e instanceof Error ? e.message : '请求后端智能体失败，请检查服务是否启动'
      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        text: `❌ 调用智能体出错：${msg}`,
      }
      setMessages((prev) => [...prev, assistantMsg])
    } finally {
      setIsThinking(false)
      // 发完消息后显式归档当前对话
      archiveCurrentConversation(messagesRef.current)
    }
  }

  const handleKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (
    e,
  ) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="page">
      {/* 历史记录面板（放在 page 根层级，避免被 card overflow 截断） */}
      {historyPanelOpen && (
        <div className="history-overlay" onClick={() => setHistoryPanelOpen(false)}>
          <div className="history-panel" onClick={(e) => e.stopPropagation()}>
            <div className="history-panel-header">
              <span className="history-panel-title">历史记录</span>
              <button
                type="button"
                className="history-panel-close"
                onClick={() => setHistoryPanelOpen(false)}
                aria-label="关闭历史记录"
              >
                ×
              </button>
            </div>
            <div className="history-panel-list">
              {historyList.length === 0 ? (
                <div className="history-empty">暂无历史记录</div>
              ) : (
                historyList.map((record) => (
                  <div
                    key={record.id}
                    className="history-item"
                    onClick={() => loadHistoryRecord(record)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') loadHistoryRecord(record)
                    }}
                  >
                    <div className="history-item-content">
                      <div className="history-item-title">{record.title}</div>
                      <div className="history-item-meta">
                        {record.videoTitle && (
                          <span className="history-item-video">📹 {record.videoTitle}</span>
                        )}
                        <span className="history-item-time">
                          {new Date(record.updatedAt).toLocaleString('zh-CN', {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="history-item-delete"
                      onClick={(e) => deleteHistoryRecord(record.id, e)}
                      title="删除此记录"
                      aria-label="删除历史记录"
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6" />
                        <path d="M10 11v6M14 11v6" />
                        <path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2" />
                      </svg>
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
      <div className="card app-layout">
        <div className="page-header">
      <div>
            <h1 className="page-title">智能视频问答助手</h1>
            <p className="page-subtitle">
              参赛作品 · 支持「看视频 + 搜内容 + 问 AI + 点时间戳秒跳转」，适合课堂讲解回放和知识复盘。
            </p>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
            <button
              type="button"
              className="history-btn"
              onClick={() => setHistoryPanelOpen((v) => !v)}
              title="查看历史记录"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              历史记录
              {historyList.length > 0 && (
                <span className="history-badge">{historyList.length}</span>
              )}
            </button>
            <button
              type="button"
              className="history-btn history-btn--new"
              onClick={startNewConversation}
              title="开始新对话"
            >
              + 新对话
            </button>
          </div>
        </div>

        <div className="main-layout">
          {/* 左侧：视频区域 */}
          <div className="video-panel">
            <div className="video-upload-bar">
              <label className="upload-btn">
                选择本地视频
                <input
                  type="file"
                  accept="video/*"
                  onChange={handleUpload}
                  disabled={isUploading}
                  style={{ display: 'none' }}
                />
              </label>
              <span className="upload-hint">
                {isUploading
                  ? '正在上传并处理视频…'
                  : '上传你自己的长视频，上传完成后左侧会自动切换为新视频'}
              </span>
            </div>
            <div className="video-subtitle">{backendStatusText}</div>

            <div className="video-wrapper">
              {isJumping && (
                <div className="video-jump-overlay">
                  <div className="video-jump-pulse" />
                  <span className="video-jump-text">已跳转到指定时间点</span>
                </div>
              )}
              <video
                key={videoId} // 使用 videoId 作为 key，切换视频时强制重新渲染
                ref={videoRef}
                className="video-player"
                controls
                src={videoSrc}
                preload="metadata"
                playsInline
              >
                您的浏览器不支持 video 标签。
              </video>
            </div>
            <div className="video-meta">
              <div className="video-title">{videoTitle}</div>
              <div className="video-subtitle">
                播放你上传的视频，然后在右侧对话框里问，例如"01:15 讲了什么？" 来体验"点时间戳直接跳"的效果。
              </div>
              {cvGate && (
                <div className="video-subtitle">
                  {isCvReady
                    ? 'OCR 结果已更新到当前视频，可以开始提问。'
                    : 'OCR 正在处理当前视频，请稍后提问。'}
                </div>
              )}
              {processingProgress !== null && (
                <div className="video-progress">
                  <div className="video-progress-label">
                    正在解析 / 处理视频… {processingProgress}%
                  </div>
                  <div className="video-progress-bar">
                    <div
                      className="video-progress-inner"
                      style={{ width: `${processingProgress}%` }}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* 右侧：聊天区域 */}
          <div className="chat-panel">
            <div
              className={`search-panel ${searchPanelOpen ? 'search-panel--open' : 'search-panel--collapsed'}`}
            >
              <div className="search-panel-toolbar">
                <button
                  type="button"
                  className="search-panel-toggle"
                  onClick={() => setSearchPanelOpen((v) => !v)}
                  aria-expanded={searchPanelOpen}
                  title={searchPanelOpen ? '收起搜索' : '展开视频搜索'}
                >
                  <span className="search-panel-chevron" aria-hidden>
                    {searchPanelOpen ? '▼' : '▶'}
                  </span>
                  <span className="search-title search-title--inline">视频内容搜索</span>
                  {!searchPanelOpen && (
                    <span className="search-panel-hint">
                      {hasSearched && searchResults.length > 0
                        ? `已搜到 ${searchResults.length} 条，点击展开`
                        : hasSearched && !isSearching
                          ? '无结果，点击展开再搜'
                          : '点击展开输入关键词'}
                    </span>
                  )}
                </button>
              </div>

              {searchPanelOpen && (
                <>
                  <div className="search-header">
                    <span className="search-desc search-desc--block">
                      输入关键词，快速定位视频中的关键片段。
                    </span>
                  </div>
                  <div className="search-bar">
                    <input
                      className="search-input"
                      placeholder="例如：开场引入 / 定义 / 案例…"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleSearch()
                      }}
                    />
                    <button
                      type="button"
                      className="primary search-btn"
                      onClick={handleSearch}
                      disabled={
                        !searchQuery.trim() || isSearching || !backendReady
                      }
                    >
                      {isSearching ? '搜索中…' : '搜索片段'}
                    </button>
                  </div>

                  <div className="search-results">
                    {!hasSearched && (
                      <div className="search-placeholder">
                        {backendReady
                          ? '输入关键词并点击「搜索片段」，这里会展示命中的视频片段和时间戳（基于 OCR 文本）。'
                          : '请先连接后端，再使用视频内容搜索。'}
                      </div>
                    )}
                    {hasSearched && isSearching && (
                      <div className="search-placeholder">
                        正在搜索视频内容…
                      </div>
                    )}
                    {hasSearched && !isSearching && searchResults.length === 0 && (
                      <div className="search-placeholder">
                        没有找到相关片段。
                      </div>
                    )}
                    {searchResults.map((item) => (
                      <div
                        key={item.id}
                        className="search-item"
                        role="button"
                        tabIndex={0}
                        onClick={() => handleJump(item.seconds)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            handleJump(item.seconds)
                          }
                        }}
                      >
                        <div className="search-item-main">
                          <div className="search-item-title">{item.title}</div>
                          <div className="search-item-snippet">{item.snippet}</div>
                        </div>
                        <span className="timestamp-link search-time-btn">
                          {item.timeText}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            <div className="chat-messages">
              {messages.map((m) => {
                const content = renderHighlightedMessage(
                  m.role === 'assistant'
                    ? stripDisplayMarkdown(m.text)
                    : m.text,
                  handleJump,
                )
                return (
                  <div
                    key={m.id}
                    className={`message ${m.role === 'user' ? 'message-user' : 'message-assistant'}`}
                  >
                    <div className="avatar">
                      {m.role === 'user' ? '我' : 'AI'}
                    </div>
                    <div className="bubble">
                      {content}
                    </div>
                  </div>
                )
              })}
              {isThinking && (
                <div className="message message-assistant">
                  <div className="avatar">AI</div>
                  <div className="bubble bubble-thinking">
                    正在思考…
                  </div>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            <div className="chat-input-area">
              <textarea
                className="chat-input"
                placeholder="问我：这个视频 01:15 在讲什么？按 Enter 发送，Shift+Enter 换行。"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={3}
              />
              <div className="chat-input-footer">
                <div className="chat-hint">
                  {cvGate && !isCvReady
                    ? '正在解析你上传的视频，请等待 OCR 结果更新完成后再提问。'
                    : '支持输入自然语言提问，回答中的时间戳可点击跳转。'}
      </div>
                <button
                  type="button"
                  className="primary send-btn"
                  onClick={handleSend}
                  disabled={!input.trim() || isThinking || !backendReady}
                >
                  {isThinking ? '回答中…' : '发送'}
        </button>
              </div>
            </div>
          </div>
        </div>
        <div className="knowledge-panel">
          <div className="knowledge-left">
            <div className="knowledge-title">知识导图 / 精选截图</div>
            <div className="knowledge-images">
              {snapshots.length === 0 &&
                [1, 2, 3].map((i) => (
                  <div key={i} className="knowledge-image-placeholder">
                    截图 {i}
                  </div>
                ))}
              {snapshots.map((url, idx) => (
                <button
                  key={idx}
                  type="button"
                  className="knowledge-image-placeholder knowledge-thumb"
                  onClick={() => setLightboxUrl(url)}
                  title="点击放大查看"
                  aria-label={`放大查看截图 ${idx + 1}`}
                >
                  <img src={url} alt={`截图 ${idx + 1}`} className="knowledge-image" draggable={false} />
                </button>
              ))}
            </div>
          </div>
          <div
            className={`knowledge-right ${lecturePanelOpen ? 'knowledge-right--open' : 'knowledge-right--collapsed'}`}
          >
            <div className="knowledge-lecture-toolbar">
              <button
                type="button"
                className="knowledge-lecture-toggle"
                onClick={() => setLecturePanelOpen((v) => !v)}
                aria-expanded={lecturePanelOpen}
                title={lecturePanelOpen ? '收起讲义' : '展开讲义全文'}
              >
                <span className="knowledge-lecture-chevron" aria-hidden>
                  {lecturePanelOpen ? '▼' : '▶'}
                </span>
                <span className="knowledge-title knowledge-title--toolbar">
                  文字讲义 / 总结
                </span>
                {!lecturePanelOpen && (
                  <span className="knowledge-lecture-hint">
                    {lectureLoading
                      ? '生成中…'
                      : lectureSummary && cvGate && isCvReady
                        ? '已生成，点击展开'
                        : !cvGate
                          ? '示例，点击展开'
                          : '点击展开查看'}
                  </span>
                )}
              </button>
              <div className="knowledge-actions">
                <button
                  type="button"
                  className="knowledge-refresh-btn"
                  onClick={() => void loadLecture(true)}
                  disabled={
                    !cvGate ||
                    !isCvReady ||
                    lectureLoading
                  }
                  title="忽略缓存，重新请求 DeepSeek 生成"
                >
                  重新生成
                </button>
                <button
                  type="button"
                  className="primary knowledge-copy-btn"
                  onClick={handleCopyKnowledge}
                  disabled={
                    Boolean(cvGate && isCvReady && lectureLoading && !lectureSummary)
                  }
                >
                  复制讲义
                </button>
              </div>
            </div>
            {lecturePanelOpen && (
              <div className="knowledge-text-wrap">
                <p className="knowledge-text knowledge-text-body">
                  {displayedKnowledgeText}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {lightboxUrl && (
        <div
          className="image-lightbox-backdrop"
          role="presentation"
          onClick={() => setLightboxUrl(null)}
        >
          <div
            className="image-lightbox-inner"
            role="dialog"
            aria-modal="true"
            aria-label="截图预览"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="image-lightbox-close"
              onClick={() => setLightboxUrl(null)}
              aria-label="关闭预览"
            >
              ×
            </button>
            <img
              src={lightboxUrl}
              alt="截图大图预览"
              className="image-lightbox-img"
            />
          </div>
        </div>
      )}

      <footer className="page-footer">
        © 2026 智能视频问答助手 · 用于比赛展示，仅供教学与演示使用
      </footer>
    </div>
  )
}

export default App
