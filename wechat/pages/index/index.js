const { API_BASE, CHAT_PATH } = require('../../utils/config.js')

const DEFAULT_UPPER = 25
const MIN_UPPER = 20
const MAX_UPPER = 75

function newId() {
  return `m-${Date.now()}-${Math.floor(Math.random() * 1000)}`
}

function upperStyle(pct) {
  return 'height: ' + pct + '%;'
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
  },

  onLoad() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    this._winH = info.windowHeight
    this._startY = 0
    this._startPct = DEFAULT_UPPER
    this._moved = false
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

  onMoreTap(e) {
    const type = e.currentTarget.dataset.type
    this.setData({ morePanel: false })
    if (type === 'album' || type === 'camera') {
      wx.chooseMedia({
        count: 1,
        mediaType: ['image'],
        sourceType: [type === 'album' ? 'album' : 'camera'],
        success: res => {
          this.pushMessage({ type: 'image', src: res.tempFiles[0].tempFilePath })
        },
        fail: () => {},
      })
    } else if (type === 'file-wechat') {
      wx.chooseMessageFile({
        count: 1,
        type: 'file',
        success: res => {
          const f = res.tempFiles[0]
          this.pushMessage({ type: 'file', name: f.name || '文件', src: f.path })
        },
        fail: () => {},
      })
    } else if (type === 'file-local') {
      wx.navigateTo({
        url: '/pages/local-files/local-files',
        success: res => {
          res.eventChannel.on('selectFile', file => {
            this.pushMessage({ type: 'file', name: file.name || '文件', src: file.path })
          })
        },
      })
    }
  },

  pushMessage(m) {
    const message = Object.assign({ id: newId(), role: 'user' }, m)
    const messages = this.data.messages.concat([message])
    this.setData({ messages, scrollAnchorId: `tail-${Date.now()}` })
  },

  sendMessage() {
    const text = (this.data.inputValue || '').trim()
    if (!text) return
    this.setData({ inputValue: '' })
    this.pushMessage({ text })
    this.askBackend(text)
  },

  askBackend(text) {
    const history = this.data.messages
      .filter(m => m.text)
      .slice(-20)
      .map(m => ({ role: m.role === 'ai' ? 'assistant' : 'user', content: m.text }))
    wx.request({
      url: API_BASE + CHAT_PATH,
      method: 'POST',
      timeout: 20000,
      header: { 'content-type': 'application/json' },
      data: { message: text, history },
      success: res => {
        const reply = pickReply(res.data)
        if (res.statusCode >= 200 && res.statusCode < 300 && reply) {
          this.pushMessage({ role: 'ai', text: reply })
        } else {
          this.pushMessage({ role: 'ai', text: `请求失败（HTTP ${res.statusCode}）` })
        }
      },
      fail: () => {
        this.pushMessage({ role: 'ai', text: '连接后端失败：请确认 127.0.0.1:18080 已启动，且开发者工具已勾选“不校验合法域名”' })
      },
    })
  },
})
