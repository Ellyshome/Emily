# -*- coding: utf-8 -*-
import psycopg2
conn = psycopg2.connect(host='127.0.0.1', port=25432, user='emily', password='emily_secret_2026', dbname='emily')
cur = conn.cursor()
cur.execute("SELECT event_no, title FROM events WHERE event_no='EVT-TRACE-9001'")
r = cur.fetchone()
print(r)
print(type(r[1]), len(r[1]))
cur.close()
conn.close()
