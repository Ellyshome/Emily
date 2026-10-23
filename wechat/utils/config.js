// 网关地址，二选一：
//   生产：经宿主机 nginx 路径分流 https://ellym.asia/wxmp → wechat-gateway（见 deploy/nginx/）
//         上线前需在微信公众平台把域名分别配入三张白名单：
//           request 合法域名（/chat、/upload、/files/pending）
//           downloadFile 合法域名（/files/{token}）
//           uploadFile 合法域名（/upload）
//         三者相互独立，只配 request 会导致文件上传下载被拦截。
//   本地：当前按手机真机调试配置，指向电脑局域网 IP（手机与电脑需连同一 Wi-Fi）；
//         只在电脑模拟器调试时可改回 http://127.0.0.1:18090，并勾选“不校验合法域名”
const LOCAL_API_BASE = 'http://192.168.100.87:18090'
const PROD_API_BASE = 'https://ellym.asia/wxmp'

module.exports = {
  API_BASE: PROD_API_BASE,
  CHAT_PATH: '/chat',
  // 登录：wx.login 的 code 换网关令牌
  LOGIN_PATH: '/login',

  // ── 文件通道 ──
  // 上传暂存（小程序 → 网关），Core 随后回拉归档
  UPLOAD_PATH: '/upload',
  // Emily 主动发出的文件：GET {FILE_PATH}/{token}
  FILE_PATH: '/files',
  // 兜底补取本轮待下载文件（file_send 晚于 reply 到达时用）
  FILES_PENDING_PATH: '/files/pending',
  // 单文件上限（MB）：与网关 WXMP_MAX_FILE_MB 保持一致
  MAX_FILE_MB: 200,
}
