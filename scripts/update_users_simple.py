#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'emily-core'))

from sqlalchemy import create_engine, text

DB_URL = "postgresql://emily:emily_secret_2026@localhost:25432/emily"

def main():
    engine = create_engine(DB_URL)
    
    users_data = [
        {
            "id": "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
            "username": "Zhang Jianguo",
            "company_id": "46eabd79-9cef-4a72-bcc5-cfbaed6c78e3",
            "position": '["Project Manager"]',
            "org_category": 4,
            "perm_list": '["project:read", "project:write"]',
            "supervisor_id": None,
            "qq": "123456789",
            "wechat": "zhangjg",
            "id_card": "120104197501151234",
            "email": "zhangjianguo@tjeco.com",
            "phone": "13801234567",
        },
        {
            "id": "b2c3d4e5-f6a7-4b6c-9d0e-1f2a3b4c5d6e",
            "username": "Li Minghua",
            "company_id": "1daa344b-0efd-4427-81de-3ba3b6d444e1",
            "position": '["Civil Engineer"]',
            "org_category": 3,
            "perm_list": '["project:read", "event:create"]',
            "supervisor_id": "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
            "qq": "234567890",
            "wechat": "liminghua",
            "id_card": "120104198002162345",
            "email": "liminghua@tjeco.com",
            "phone": "13902345678",
        },
        {
            "id": "c3d4e5f6-a7b8-4c7d-8e1f-2a3b4c5d6e7f",
            "username": "Wang Xiaofang",
            "company_id": "46eabd79-9cef-4a72-bcc5-cfbaed6c78e3",
            "position": '["Quality Supervisor"]',
            "org_category": 2,
            "perm_list": '["project:read", "quality:check"]',
            "supervisor_id": "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d",
            "qq": "345678901",
            "wechat": "wangxf",
            "id_card": "120104198803173456",
            "email": "wangxiaofang@tjeco.com",
            "phone": "13703456789",
        },
        {
            "id": "d4e5f6a7-b8c9-4d8e-9f2a-3b4c5d6e7f8a",
            "username": "Zhao Wei",
            "company_id": "1daa344b-0efd-4427-81de-3ba3b6d444e1",
            "position": '["Safety Officer"]',
            "org_category": 2,
            "perm_list": '["project:read", "safety:report"]',
            "supervisor_id": "b2c3d4e5-f6a7-4b6c-9d0e-1f2a3b4c5d6e",
            "qq": "456789012",
            "wechat": "zhaowei",
            "id_card": "120104199004184567",
            "email": "zhaowei@tjeco.com",
            "phone": "13604567890",
        },
        {
            "id": "e5f6a7b8-c9d0-4e9f-0a3b-4c5d6e7f8a9b",
            "username": "Chen Siyu",
            "company_id": "1daa344b-0efd-4427-81de-3ba3b6d444e1",
            "position": '["Document Controller"]',
            "org_category": 1,
            "perm_list": '["project:read", "document:upload"]',
            "supervisor_id": "b2c3d4e5-f6a7-4b6c-9d0e-1f2a3b4c5d6e",
            "qq": "567890123",
            "wechat": "chensy",
            "id_card": "120104199505195678",
            "email": "chensiyu@tjeco.com",
            "phone": "13505678901",
        },
    ]
    
    with engine.connect() as conn:
        for user in users_data:
            sql = text("""
                UPDATE users SET
                    company = :company_id,
                    position = :position,
                    org_category = :org_category,
                    perm_list = :perm_list,
                    supervisor_id = :supervisor_id,
                    qq = :qq,
                    wechat = :wechat,
                    id_card = :id_card,
                    email = :email,
                    phone = :phone,
                    username = :username
                WHERE id = :id
            """)
            conn.execute(sql, user)
            print(f"Updated: {user['username']}")
        
        conn.commit()
    
    print("\nDone! All users updated.")
    
    # Verify
    print("\nUser list:")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, username, phone, permission_level, company FROM users ORDER BY permission_level DESC"))
        for row in result:
            print(f"  {row.username} | Phone: {row.phone} | Level: {row.permission_level} | Company: {row.company}")

if __name__ == "__main__":
    main()
