const {
  API_BASE,
  CHAT_PATH,
  UPLOAD_PATH,
  FILES_PENDING_PATH,
  MAX_FILE_MB,
} = require('../../utils/config.js')

const DEFAULT_UPPER = 25
const MIN_UPPER = 20
const MAX_UPPER = 75

const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']

function newId() {
  return `m-${Date.now()}-${Math.floor(Math.random() * 1000)}`
}

function upperStyle(pct) {
  return 'height: ' + pct + '%;'
}

function extOf(name) {
  const i = (name || '').lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

function isImageName(name) {
  return IMAGE_EXT.indexOf(extOf(name)) >= 0
}

function baseName(path) {
  const s = (path || '').split('?')[0]
  const i = s.lastIndexOf('/')
  return i >= 0 ? s.slice(i + 1) : s
}

function pickReply(data) {
  if (typeof data === 'string') return data
  const root = data && data.data && typeof data.data === 'object' ? data.data : data
  if (!root || typeof root !== 'object') return ''
  const keys = ['reply', 'answer', 'content', 'text', 'message', 'msg', 'output']
  for (const k of keys) {
    const v = root[k]
    if (typeof v === 'string' && v) return v
  }
  return ''
}

Page({
  data: {
    upperPercent: DEFAULT_UPPER,
    upperStyle: upperStyle(DEFAULT_UPPER),
    dividerActive: false,
    scrollAnchorId: 'tail-0',
    tools: [
      { key: 'camera', name: '拍照', icon: '拍' },
      { key: 'album', name: '相册', icon: '册' },
      { key: 'location', name: '位置', icon: '位' },
      { key: 'more', name: '更多', icon: '…' },
    ],
    messages: [
      { id: 'm-1', role: 'ai', text: '你好，我是 Emily，有什么可以帮你？' },
    ],
    inputValue: '',
    voiceMode: false,
    morePanel: false,
    sending: false,
  },

  onLoad() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    this._winH = info.windowHeight
    this._startY = 0
    this._startPct = DEFAULT_UPPER
    this._moved = false
    // 已选、尚未随消息发出的本地附件：[{localPath, name, kind, size}]
    this._pending = []
    // 本地假身份：生成一次并持久化，作为对话用户标识（正式 code2session 属后续阶段）
    let devId = wx.getStorageSync('dev_openid')
    if (!devId) {
      devId = 'dev_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
      wx.setStorageSync('dev_openid', devId)
    }
    this._devId = devId
  },

  onDividerStart(e) {
    this._startY = e.touches[0].clientY
    this._startPct = this.data.upperPercent
    this._moved = false
    this.setData({ dividerActive: true })
  },

  onDividerMove(e) {
    const y = e.touches[0].clientY
    if (!this._moved && Math.abs(y - this._startY) > 3) this._moved = true
    if (!this._winH) return
    let pct = this._startPct + ((y - this._startY) / this._winH) * 100
    pct = Math.round(Math.max(MIN_UPPER, Math.min(MAX_UPPER, pct)))
    if (pct !== this.data.upperPercent) {
      this.setData({ upperPercent: pct, upperStyle: upperStyle(pct) })
    }
  },

  onDividerEnd() {
    this.setData({ dividerActive: false })
    if (!this._moved) {
      this.setData({ upperPercent: DEFAULT_UPPER, upperStyle: upperStyle(DEFAULT_UPPER) })
    }
  },

  onDividerCancel() {
    this.setData({ dividerActive: false })
  },

  onToolTap(e) {
    const { name } = e.currentTarget.dataset
    wx.showToast({ title: `${name}功能开发中`, icon: 'none' })
  },

  onInput(e) {
    this.setData({ inputValue: e.detail.value })
  },

  toggleVoice() {
    const voiceMode = !this.data.voiceMode
    this.setData({ voiceMode, morePanel: false })
    if (voiceMode) wx.showToast({ title: '语音功能开发中', icon: 'none' })
  },

  onHoldTalk() {
    wx.showToast({ title: '语音功能开发中', icon: 'none' })
  },

  toggleMorePanel() {
    this.setData({ morePanel: !this.data.morePanel })
  },

  closeMorePanel() {
    this.setData({ morePanel: false })
  },

  noop() {},

  // ══════════════════════════════════════════════════════════════════════
  // 附件选择：先本地入列，待发送时统一上传
  // ══════════════════════════════════════════════════════════════════════

  onMoreTap(e) {
    const type = e.currentTarget.dataset.type
    this.setData({ morePanel: false })
    if (type === 'album' || type === 'camera') {
      wx.chooseMedia({
        count: 1,
        mediaType: ['image'],
        sourceType: [type === 'album' ? 'album' : 'camera'],
        success: res => {
          const f = res.tempFiles[0]
          this.pushAttachment({
            localPath: f.tempFilePath,
            name: baseName(f.tempFilePath) || '图片',
            kind: 'image',
            size: f.size,
          })
        },
        fail: () => {},
      })
    } else if (type === 'file-wechat') {
      wx.chooseMessageFile({
        count: 1,
        type: 'file',
        success: res => {
          const f = res.tempFiles[0]
          this.pushAttachment({
            localPath: f.path,
            name: f.name || '文件',
            kind: 'file',
            size: f.size,
          })
        },
        fail: () => {},
      })
    } else if (type === 'file-local') {
      wx.navigateTo({
        url: '/pages/local-files/local-files',
        success: res => {
          res.eventChannel.on('selectFile', file => {
            this.pushAttachment({
              localPath: file.path,
              name: file.name || '文件',
              kind: 'file',
              size: file.size,
            })
          })
        },
      })
    }
  },

  pushAttachment(att) {
    if (att.size && att.size > MAX_FILE_MB * 1024 * 1024) {
      wx.showToast({ title: `文件超过 ${MAX_FILE_MB}MB 上限`, icon: 'none' })
      return
    }
    this._pending.push(att)
    if (att.kind === 'image') {
      this.pushMessage({ type: 'image', src: att.localPath })
    } else {
      this.pushMessage({ type: 'file', name: att.name, src: att.localPath, stateText: '待发送' })
    }
  },

  pushMessage(m) {
    const message = Object.assign({ id: newId(), role: 'user' }, m)
    const messages = this.data.messages.concat([message])
    this.setData({ messages, scrollAnchorId: `tail-${Date.now()}` })
  },

  setMsgState(id, stateText) {
    const messages = this.data.messages.map(m =>
      m.id === id ? Object.assign({}, m, { stateText }) : m
    )
    this.setData({ messages })
  },

  // ══════════════════════════════════════════════════════════════════════
  // 发送：有附件则先上传暂存，再把附件引用随消息发出
  // ══════════════════════════════════════════════════════════════════════

  sendMessage() {
    if (this.data.sending) return
    const text = (this.data.inputValue || '').trim()
    const pending = this._pending.slice()
    if (!text && !pending.length) return
    this.setData({ inputValue: '' })

    // 无附件：纯文本直发（保持原路径）
    if (!pending.length) {
      this.pushMessage({ text })
      this.askBackend(text, [])
      return
    }

    this.setData({ sending: true })
    this.uploadAll(pending)
      .then(attachments => {
        this._pending = []
        if (text) this.pushMessage({ text })
        this.askBackend(text, attachments)
      })
      .catch(err => {
        const detail = (err && (err.errMsg || err.message)) || String(err)
        this.pushMessage({ role: 'ai', text: `附件上传失败：${detail}` })
      })
      .then(() => this.setData({ sending: false }))
  },

  /** 逐个上传（串行：保序，也避免移动网络下的并发争抢）。 */
  uploadAll(list) {
    return list.reduce(
      (chain, att) => chain.then(done => this.uploadOne(att).then(r => done.concat([r]))),
      Promise.resolve([])
    )
  },

  uploadOne(att) {
    return new Promise((resolve, reject) => {
      wx.uploadFile({
        url: API_BASE + UPLOAD_PATH,
        filePath: att.localPath,
        name: 'file',
        header: { 'X-Dev-User': this._devId || 'dev-user' },
        timeout: 120000,
        success: res => {
          let body = {}
          try {
            body = JSON.parse(res.data || '{}')
          } catch (e) {}
          if (res.statusCode >= 200 && res.statusCode < 300 && body.url) {
            resolve({
              type: att.kind === 'image' ? 2 : body.type || 3,
              url: body.url, // 指向网关，由 Core 回拉归档
              file_name: body.file_name || att.name,
              file_size: body.file_size || 0,
            })
          } else {
            reject(new Error(body.error || `HTTP ${res.statusCode}`))
          }
        },
        fail: reject,
      })
    })
  },

  askBackend(text, attachments) {
    const history = this.data.messages
      .filter(m => m.text)
      .slice(-20)
      .map(m => ({ role: m.role === 'ai' ? 'assistant' : 'user', content: m.text }))
    wx.request({
      url: API_BASE + CHAT_PATH,
      method: 'POST',
      timeout: 60000,
      header: { 'content-type': 'application/json', 'X-Dev-User': this._devId || 'dev-user' },
      data: { message: text, history, attachments: attachments || [] },
      success: res => {
        const reply = pickReply(res.data)
        const files = (res.data && res.data.files) || []
        if (res.statusCode >= 200 && res.statusCode < 300 && (reply || files.length)) {
          if (reply) this.pushMessage({ role: 'ai', text: reply })
          this.pushFiles(files)
          // 兜底：file_send 极少数情况下晚于 reply 到达，稍后再补取一次
          this.pullPendingFiles()
          return
        }
        this.pushMessage({ role: 'ai', text: `请求失败（HTTP ${res.statusCode}）` })
      },
      fail: () => {
        this.pushMessage({ role: 'ai', text: '连接网关失败：请确认 127.0.0.1:18090 已启动，且开发者工具已勾选“不校验合法域名”' })
      },
    })
  },

  pullPendingFiles() {
    setTimeout(() => {
      wx.request({
        url: API_BASE + FILES_PENDING_PATH,
        method: 'GET',
        timeout: 15000,
        header: { 'X-Dev-User': this._devId || 'dev-user' },
        success: res => {
          if (res.statusCode >= 200 && res.statusCode < 300 && res.data) {
            this.pushFiles(res.data.files || [])
          }
        },
        fail: () => {},
      })
    }, 1200)
  },

  pushFiles(files) {
    if (!files || !files.length) return
    files.forEach(f => {
      this.pushMessage({
        role: 'ai',
        type: 'file',
        name: f.name || '文件',
        url: f.url, // 相对路径，如 /files/<token>
        stateText: '点击下载',
      })
    })
  },

  // ══════════════════════════════════════════════════════════════════════
  // 出站文件：下载 + 打开
  // ══════════════════════════════════════════════════════════════════════

  onFileTap(e) {
    const id = e.currentTarget.dataset.id
    const msg = this.data.messages.find(m => m.id === id)
    // 用户自己发的文件是本地临时路径，无需下载
    if (!msg || !msg.url) return
    this.downloadAndOpen(msg)
  },

  downloadAndOpen(msg) {
    const url = /^https?:/.test(msg.url) ? msg.url : API_BASE + msg.url
    this.setMsgState(msg.id, '下载中…')
    wx.downloadFile({
      url,
      timeout: 180000,
      header: { 'X-Dev-User': this._devId || 'dev-user' },
      success: res => {
        if (res.statusCode !== 200) {
          this.setMsgState(msg.id, `下载失败(${res.statusCode})`)
          return
        }
        this.setMsgState(msg.id, '已下载')
        this.openLocalFile(res.tempFilePath, msg.name)
      },
      fail: err => {
        this.setMsgState(msg.id, '下载失败')
        wx.showToast({ title: `下载失败：${(err && err.errMsg) || ''}`, icon: 'none' })
      },
    })
  },

  openLocalFile(path, name) {
    // 图片走预览（openDocument 不支持图片格式）
    if (isImageName(name)) {
      wx.previewImage({ urls: [path], fail: () => {} })
      return
    }
    wx.openDocument({
      filePath: path,
      showMenu: true,
      fail: err => {
        wx.showModal({
          title: '无法在线预览',
          content: `文件已下载，但该格式不支持在小程序内打开。${(err && err.errMsg) || ''}`,
          showCancel: false,
        })
      },
    })
  },
})
