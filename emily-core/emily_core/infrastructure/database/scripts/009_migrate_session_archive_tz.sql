-- 009: session_archives 时间列口径由 UTC 改为北京时间（+08:00）
--
-- 背景：归档索引（本表）原先写 UTC，而归档正文 md 写 UTC+8，同一时刻落成两个
--       不同的墙钟字符串，导致「归档时间」与「对话记录/归档正文」相差 8 小时。
--       统一口径后本表三个时间列一律存北京时间 ISO8601。
--
-- 幂等：仅处理仍以 '+00:00' 结尾（UTC）的值，重复执行无副作用。
-- 执行：docker cp <本文件> emily-postgres:/tmp/ && \
--       docker exec emily-postgres psql -U emily -d emily -f /tmp/009_migrate_session_archive_tz.sql

BEGIN;

UPDATE session_archives
   SET started_at = to_char((started_at::timestamptz AT TIME ZONE 'Asia/Shanghai'),
                            'YYYY-MM-DD"T"HH24:MI:SS.US') || '+08:00'
 WHERE started_at LIKE '%+00:00';

UPDATE session_archives
   SET last_active_at = to_char((last_active_at::timestamptz AT TIME ZONE 'Asia/Shanghai'),
                                'YYYY-MM-DD"T"HH24:MI:SS.US') || '+08:00'
 WHERE last_active_at LIKE '%+00:00';

UPDATE session_archives
   SET archived_at = to_char((archived_at::timestamptz AT TIME ZONE 'Asia/Shanghai'),
                             'YYYY-MM-DD"T"HH24:MI:SS.US') || '+08:00'
 WHERE archived_at LIKE '%+00:00';

COMMIT;
