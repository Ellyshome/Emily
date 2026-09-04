// 本地联调后端：本机 Docker 容器映射端口 18080。
// 真机预览时把 127.0.0.1 换成电脑的局域网 IP（如 http://192.168.1.8:18080）。
module.exports = {
  API_BASE: 'http://127.0.0.1:18080',
  CHAT_PATH: '/chat',
}
