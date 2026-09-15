// 网关地址，二选一：
//   生产：经宿主机 nginx 路径分流 https://ellym.asia/wxmp → wechat-gateway（见 deploy/nginx/）
//         上线前还需在微信公众平台把 https://ellym.asia 配为 request 合法域名
//   本地：本机网关 http://127.0.0.1:18090；真机预览换成电脑局域网 IP
//         （如 http://192.168.1.8:18090），并勾选“不校验合法域名”
const LOCAL_API_BASE = 'http://127.0.0.1:18090'
const PROD_API_BASE = 'https://ellym.asia/wxmp'

module.exports = {
  API_BASE: LOCAL_API_BASE,
  CHAT_PATH: '/chat',
}
