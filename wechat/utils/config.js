// 本地联调后端：本机 wechat-gateway 网关端口 18090（见仓库根 wechat-gateway/）。
// 真机预览时把 127.0.0.1 换成电脑的局域网 IP（如 http://192.168.1.8:18090）。
module.exports = {
  API_BASE: 'http://127.0.0.1:18090',
  CHAT_PATH: '/chat',
}
