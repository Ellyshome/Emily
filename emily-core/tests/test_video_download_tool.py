"""video_download_tool 的纯函数测试（不联网、不依赖 DB）。"""

from emily_core.tools.video_download_tool import (
    MAX_DURATION_SECONDS,
    MAX_FILESIZE_BYTES,
    check_url_shape,
    host_allowed,
    _mime_for,
)


def test_allowed_hosts_and_subdomains():
    assert host_allowed("www.bilibili.com")
    assert host_allowed("b23.tv")
    assert host_allowed("youtube.com")
    assert host_allowed("youtu.be")
    assert host_allowed("vimeo.com")


def test_rejects_unsupported_or_lookalike_hosts():
    assert not host_allowed("evil.com")
    assert not host_allowed("bilibili.com.evil.com")
    assert not host_allowed("notbilibili.com")
    assert not host_allowed("")


def test_check_url_shape_accepts_bilibili_video():
    url, host, err = check_url_shape("https://www.bilibili.com/video/BV1xx411c7mD?p=1")
    assert err == ""
    assert host == "www.bilibili.com"
    assert url.startswith("https://www.bilibili.com/video/BV")


def test_check_url_shape_rejects_bad_scheme_and_credentials():
    assert check_url_shape("ftp://www.bilibili.com/video/BV1")[2]
    assert check_url_shape("https://user:pass@www.bilibili.com/video/BV1")[2]


def test_check_url_shape_rejects_unusual_port():
    assert check_url_shape("https://www.bilibili.com:8443/video/BV1")[2]


def test_check_url_shape_rejects_unsupported_site():
    _, _, err = check_url_shape("https://example.com/video/1")
    assert "暂不支持该站点" in err


def test_limits_are_positive():
    assert MAX_DURATION_SECONDS > 0
    assert MAX_FILESIZE_BYTES > 0


def test_mime_mapping():
    assert _mime_for("a.mp4") == "video/mp4"
    assert _mime_for("a.webm") == "video/webm"
    assert _mime_for("a.bin") == "application/octet-stream"
