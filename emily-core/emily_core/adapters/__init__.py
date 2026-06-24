"""adapters —— Emily Core 适配器层。

  · standard/ —— 跨平台标准协议对象（StandardMessage / ReplyMessage 等，纯 dataclass）
  · session/  —— Session 池管理层（SessionPoolManager / SessionFactory）

注：AstrBot 平台适配器（inbound_adapter / outbound_sender）已下沉到薄通信层插件
（data/plugins/emily_agent/adapters/astrbot/），不在 Core 容器内。
"""
