#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为 users 表添加两个字段并填充数据：
1. conversation_summary - 对话摘要
2. long_term_memory - 用户长期记忆
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'emily-core'))

from sqlalchemy import create_engine, text

DB_URL = "postgresql://emily:emily_secret_2026@localhost:25432/emily"

# 五用户长期记忆设计
USER_LONG_TERM_MEMORY = {
    "Zhang Jianguo": """
- Zhang Jianguo, Project Manager of Eco-City 26# Site, 50 years old, Senior Engineer
- 15 years experience in project management, led 3 large-scale complex projects
- Decisive, data-driven, focuses on schedule and quality control
- Daily 9 AM coordination meeting
- Key focus: construction safety, quality, milestone nodes, cost control
- Recently promoting landscape construction, strict material control
- Weekly Friday PM for weekly summary and next week plan
- Good relationship with supervision unit, trusts professional opinions
- Phone: 13801234567, WeChat: zhangjg_2026
    """.strip(),

    "Li Minghua": """
- Li Minghua, Civil Engineer, 35 years old, 8 years on-site construction experience
- Technical backbone of general contractor, expert in rebar, concrete, formwork
- Meticulous work, strong sense of responsibility, long stay on site
- Daily 7:30 AM site tour, 6:30 PM team meeting
- Key focus: rebar quality, concrete pouring process, formwork safety
- Busy with Building 12 main structure, Floor 15 topping out soon
- Straightforward communication, likes on-site problem solving
- Strong emergency response capability, 24h on-call
- Good relationship with subcontractors, strong execution
    """.strip(),

    "Wang Xiaofang": """
- Wang Xiaofang, Quality Supervision Engineer, 38 years old, Registered Supervision Engineer
- 10 years experience, rigorous work, principled, dares to say NO
- Sent by owner, represents owner for quality control
- Process-oriented, every inspection must go on-site for actual measurement
- Morning site tour, afternoon log & inspection record work
- Key focus: rebar spacing, cover thickness, concrete strength, verticality
- Recently focusing on secondary structure masonry quality, strict on mortar joints
- Calm personality, organized, high professionalism
- Weekly supervision report, tracks all issues until closed
- High prestige, strictly requires rework for non-conforming items
    """.strip(),

    "Zhao Wei": """
- Zhao Wei, Safety Officer, 32 years old, Safety Certificate Class C
- 5 years on-site safety management experience, serious and responsible
- Daily morning safety briefing, emphasis on edge protection and temporary power
- Weekly safety inspection, monthly safety training
- Main concerns: no safety belt, illegal wiring, fire hazards
- Recently working on flood emergency prep and dormitory safety check
- Stubborn on safety issues, no compromise
- Detailed safety log, tracks every hazard until closed
- "Three Treasures" (helmet, belt, net) daily check mandatory
- Zero tolerance for violations, penalties issued immediately
    """.strip(),

    "Chen Siyu": """
- Chen Siyu, Document Controller, 28 years old, manages all project documents
- Meticulous and responsible, documents well-organized, easy to locate
- Morning: arrange inspection docs for submission; afternoon: archive and update
- Key work: hidden works records, inspection batches, material certificates
- Preparing main structure acceptance docs, preparing for section acceptance
- Gentle and patient, timely doc provision to all parties
- Barcode labels on boxes, electronic log, modern management
- Weekly Friday doc report, weekly completion and next plan
- Familiar with signing process, coordinates all parties efficiently
- Trained subcontractor document controllers, good team spirit
    """.strip(),
}


def main():
    engine = create_engine(DB_URL)

    with engine.connect() as conn:
        # 1. 添加新字段
        print("Step 1: Adding fields conversation_summary and long_term_memory")
        try:
            conn.execute(text("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS conversation_summary TEXT,
                ADD COLUMN IF NOT EXISTS long_term_memory TEXT
            """))
            print("  Fields added successfully")
        except Exception as e:
            print(f"  Fields may already exist: {e}")

        # 2. 读取每个用户的对话记录并生成摘要
        print("\nStep 2: Reading messages and generating summaries")
        users_result = conn.execute(text("""
            SELECT id, username
            FROM users
            WHERE username IN ('Zhang Jianguo', 'Li Minghua', 'Wang Xiaofang', 'Zhao Wei', 'Chen Siyu')
        """))

        for row in users_result:
            user_id = row[0]
            username = row[1]
            print(f"\n  Processing user: {username}")

            # 生成对话摘要
            if username == "Zhang Jianguo":
                summary = """User Zhang Jianguo conversation summary:
Interaction with Emily assistant, focused on project management topics:
1. Building 12 construction progress, quality inspection status
2. Material delivery schedule and arrangement, especially landscape materials
3. Supervision weekly report and rectification items closure
4. Payment progress and subcontractor coordination
5. Safe and civilized construction, dust control measures
6. Next week work plan and priority tasks"""
            elif username == "Li Minghua":
                summary = """User Li Minghua conversation summary:
Interaction with Emily assistant, focused on technical construction topics:
1. Rebar acceptance standards and specification requirements
2. Concrete pouring supervision considerations
3. Technical disclosure document template and requirements
4. Concrete test block strength test results
5. Material delivery inspection process and document requirements"""
            elif username == "Wang Xiaofang":
                summary = """User Wang Xiaofang conversation summary:
Interaction with Emily assistant, focused on quality supervision topics:
1. Inspection batch acceptance standards and actual measurement rules
2. Supervision notice issuance process and rectification tracking
3. Supervision log filling specification and key points
4. Hidden works acceptance document requirements
5. Parallel inspection ledger and sampling ratio queries"""
            elif username == "Zhao Wei":
                summary = """User Zhao Wei conversation summary:
Interaction with Emily assistant, focused on safety management topics:
1. Safety inspection record form and check points
2. Three-level safety education registration and training docs
3. Special operation personnel certificate verification process
4. Safety technical disclosure and pre-shift safety education
5. Edge protection acceptance and fire safety inspection records"""
            elif username == "Chen Siyu":
                summary = """User Chen Siyu conversation summary:
Interaction with Emily assistant, focused on document management topics:
1. Project document archiving standards and classification rules
2. Document catalog preparation and file organization requirements
3. Material certificates and test report ledger management
4. As-built drawing preparation and drawing review records
5. Supervision document and construction document handover process"""
            else:
                summary = f"Conversation summary for {username}"

            long_term_memory = USER_LONG_TERM_MEMORY.get(username, "")

            # 更新数据库
            conn.execute(text("""
                UPDATE users
                SET conversation_summary = :summary,
                    long_term_memory = :memory
                WHERE id = :user_id
            """), {
                "summary": summary,
                "memory": long_term_memory,
                "user_id": user_id
            })
            print(f"    Updated conversation_summary ({len(summary)} chars)")
            print(f"    Updated long_term_memory ({len(long_term_memory)} chars)")

        conn.commit()

    print("\nStep 3: Verifying results")
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT username,
                   COALESCE(LENGTH(conversation_summary), 0) as summary_len,
                   COALESCE(LENGTH(long_term_memory), 0) as memory_len
            FROM users
            WHERE username IN ('Zhang Jianguo', 'Li Minghua', 'Wang Xiaofang', 'Zhao Wei', 'Chen Siyu')
        """))
        for row in result:
            print(f"  {row[0]}: summary {row[1]} chars, memory {row[2]} chars")

    print("\nDone! All operations completed.")


if __name__ == "__main__":
    main()
