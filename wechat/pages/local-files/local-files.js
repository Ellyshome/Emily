const ROOT = wx.env.USER_DATA_PATH
const fs = wx.getFileSystemManager()

const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'gif', 'webp']

function extOf(name) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

function formatSize(n) {
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB'
}

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const p = v => (v < 10 ? '0' + v : '' + v)
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes())
}

function walk(dir, out, rel) {
  let names = []
  try {
    names = fs.readdirSync(dir)
  } catch (e) {
    return
  }
  for (const n of names) {
    if (n === '.' || n === '..') continue
    const p = dir + '/' + n
    let stat = null
    try {
      stat = fs.statSync(p)
    } catch (e) {
      continue
    }
    if (stat.isDirectory()) {
      walk(p, out, rel ? rel + '/' + n : n)
    } else if (stat.isFile()) {
      out.push({
        path: p,
        name: n,
        dir: rel || '',
        size: stat.size,
        time: stat.lastModifiedTime,
        sizeText: formatSize(stat.size),
        timeText: formatTime(stat.lastModifiedTime),
        isImage: IMAGE_EXT.indexOf(extOf(n)) >= 0,
      })
    }
  }
}

Page({
  data: {
    loading: true,
    files: [],
  },

  onShow() {
    this.loadFiles()
  },

  loadFiles() {
    const files = []
    try {
      walk(ROOT, files, '')
    } catch (e) {}
    files.sort((a, b) => (b.time || 0) - (a.time || 0))
    this.setData({ loading: false, files })
  },

  onPick(e) {
    const path = e.currentTarget.dataset.path
    const file = this.data.files.find(f => f.path === path)
    if (!file) return
    const data = { name: file.name, path: file.path, size: file.size }
    const channel = typeof this.getOpenerEventChannel === 'function' ? this.getOpenerEventChannel() : null
    if (channel && channel.emit) {
      channel.emit('selectFile', data)
    } else {
      const pages = getCurrentPages()
      const prev = pages[pages.length - 2]
      if (prev && typeof prev.pushMessage === 'function') {
        prev.pushMessage({ type: 'file', name: data.name, src: data.path })
      }
    }
    wx.navigateBack()
  },
})
