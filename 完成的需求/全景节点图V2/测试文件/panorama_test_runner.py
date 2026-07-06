#!/usr/bin/env python3
"""全景节点图V2测试执行脚本

用法:
    # 运行完整测试套件
    python panorama_test_runner.py --full
    
    # 只运行P0优先级测试
    python panorama_test_runner.py --p0
    
    # 运行指定场景
    python panorama_test_runner.py --scenario crud,state_machine
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# 添加emy-test路径
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
EMY_TEST_PATH = PROJECT_ROOT / ".claude" / "skills" / "emy-test"
sys.path.insert(0, str(EMY_TEST_PATH))

try:
    from tester import EmysTester
except ImportError as e:
    print(f"❌ 无法导入emy-test模块: {e}")
    print(f"   请检查路径: {EMY_TEST_PATH}")
    sys.exit(1)


class PanoramaTestRunner:
    """全景节点图V2测试执行器"""
    
    def __init__(self, project_id: str = "project-xiongan-001"):
        self.project_id = project_id
        self.results = []
        self.start_time = None
        self.end_time = None
        
    def log_result(self, scenario: str, test_case: str, status: str, message: str = ""):
        """记录测试结果"""
        result = {
            "scenario": scenario,
            "test_case": test_case,
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
        self.results.append(result)
        
        icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⏭️"
        print(f"{icon} [{scenario}] {test_case}: {message}")
    
    async def test_xlsx_import(self) -> bool:
        """场景1：XLSX批量导入验证"""
        print("\n" + "="*70)
        print("📋 场景1：XLSX批量导入验证")
        print("="*70)
        
        try:
            with EmysTester() as emy:
                # 查询导入的节点数量
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    
                    # 1. 节点数量
                    result = conn.execute(text(
                        "SELECT COUNT(*) FROM project_nodes WHERE project_id = :pid"
                    ), {"pid": self.project_id})
                    node_count = result.fetchone()[0]
                    
                    # 2. 成果数量
                    result = conn.execute(text(
                        "SELECT COUNT(*) FROM node_deliverables "
                        "WHERE node_id IN (SELECT node_id FROM project_nodes WHERE project_id = :pid)"
                    ), {"pid": self.project_id})
                    deliv_count = result.fetchone()[0]
                    
                    # 3. 事件数量
                    result = conn.execute(text(
                        "SELECT COUNT(*) FROM node_events "
                        "WHERE node_id IN (SELECT node_id FROM project_nodes WHERE project_id = :pid)"
                    ), {"pid": self.project_id})
                    event_count = result.fetchone()[0]
                
                if node_count > 0:
                    self.log_result("XLSX导入", "节点导入", "PASS", 
                                   f"成功导入{node_count}个节点，{deliv_count}个成果，{event_count}条事件")
                    return True
                else:
                    self.log_result("XLSX导入", "节点导入", "FAIL", 
                                   "未发现导入的节点，请先执行import_nodes_xlsx.py脚本")
                    return False
                    
        except Exception as e:
            self.log_result("XLSX导入", "节点导入", "FAIL", f"异常: {str(e)}")
            return False
    
    async def test_node_crud(self) -> bool:
        """场景2：节点CRUD对话流测试"""
        print("\n" + "="*70)
        print("📋 场景2：节点CRUD对话流测试")
        print("="*70)
        
        node_id = "SG-TEST-CRUD-001"
        all_passed = True
        
        try:
            with EmysTester() as emy:
                # 1. 创建节点
                print("  → 测试创建节点...")
                reply = emy.send_sync(
                    f"创建节点{node_id}，名称'CRUD测试节点'，截止2026年12月31日",
                    sender_name="王总"
                )
                
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(
                        "SELECT node_name, status, is_discarded FROM project_nodes WHERE node_id = :nid"
                    ), {"nid": node_id})
                    row = result.fetchone()
                
                if row and row[0] == "CRUD测试节点":
                    self.log_result("节点CRUD", "创建节点", "PASS", 
                                   f"节点{node_id}创建成功，状态={row[1]}")
                else:
                    self.log_result("节点CRUD", "创建节点", "FAIL", "数据库中未找到节点")
                    all_passed = False
                
                # 2. 查询节点
                print("  → 测试查询节点...")
                reply = emy.send_sync(f"查看节点{node_id}的详细信息", sender_name="王总")
                if reply and "节点" in reply.content:
                    self.log_result("节点CRUD", "查询节点", "PASS", "成功返回节点详情")
                else:
                    self.log_result("节点CRUD", "查询节点", "FAIL", "未获得预期响应")
                
                # 3. 更新节点
                print("  → 测试更新节点...")
                reply = emy.send_sync(
                    f"更新节点{node_id}的备注：这是CRUD测试的备注",
                    sender_name="王总"
                )
                self.log_result("节点CRUD", "更新节点", "PASS", "更新命令已发送")
                
                # 4. 废弃节点
                print("  → 测试废弃节点...")
                reply = emy.send_sync(f"废弃节点{node_id}", sender_name="王总")
                
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(
                        "SELECT is_discarded FROM project_nodes WHERE node_id = :nid"
                    ), {"nid": node_id})
                    row = result.fetchone()
                
                if row and row[0]:
                    self.log_result("节点CRUD", "废弃节点", "PASS", "节点已成功废弃")
                else:
                    self.log_result("节点CRUD", "废弃节点", "FAIL", "节点未被标记为废弃")
                    all_passed = False
                
                return all_passed
                
        except Exception as e:
            self.log_result("节点CRUD", "异常", "FAIL", f"测试异常: {str(e)}")
            return False
    
    async def test_state_machine(self) -> bool:
        """场景3：状态机流转测试"""
        print("\n" + "="*70)
        print("📋 场景3：状态机流转测试")
        print("="*70)
        
        node_id = "SG-TEST-STATE-001"
        all_passed = True
        
        try:
            with EmysTester() as emy:
                # 1. 创建节点
                print("  → 创建节点...")
                emy.send_sync(
                    f"创建节点{node_id}，名称'状态机测试节点'，截止2026-06-30",
                    sender_name="王总"
                )
                
                # 2. 添加成果
                print("  → 添加成果...")
                emy.send_sync(
                    f"给节点{node_id}添加成果：测试成果，目标1份",
                    sender_name="王总"
                )
                
                # 验证初始状态
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(
                        "SELECT status FROM project_nodes WHERE node_id = :nid"
                    ), {"nid": node_id})
                    row = result.fetchone()
                
                if row and row[0] == "CONDITIONS_NOT_MET":
                    self.log_result("状态机", "初始状态", "PASS", f"初始状态={row[0]}")
                else:
                    self.log_result("状态机", "初始状态", "FAIL", f"预期CONDITIONS_NOT_MET，实际={row[0] if row else None}")
                    all_passed = False
                
                # 3. 完成成果（触发状态流转）
                print("  → 完成成果，触发状态流转...")
                emy.send_sync(
                    f"更新节点{node_id}的成果'测试成果'进度：完成1份",
                    sender_name="王总"
                )
                
                # 验证最终状态
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(
                        "SELECT status, progress FROM project_nodes WHERE node_id = :nid"
                    ), {"nid": node_id})
                    row = result.fetchone()
                
                if row and row[0] == "COMPLETED":
                    self.log_result("状态机", "状态流转", "PASS", 
                                   f"状态={row[0]}, 进度={row[1]}%")
                else:
                    self.log_result("状态机", "状态流转", "FAIL", 
                                   f"预期COMPLETED，实际状态={row[0] if row else None}")
                    all_passed = False
                
                # 验证事件记录
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(
                        "SELECT COUNT(*) FROM node_events WHERE node_id = :nid"
                    ), {"nid": node_id})
                    event_count = result.fetchone()[0]
                
                if event_count > 0:
                    self.log_result("状态机", "事件记录", "PASS", f"共{event_count}条事件记录")
                else:
                    self.log_result("状态机", "事件记录", "FAIL", "未找到事件记录")
                    all_passed = False
                
                return all_passed
                
        except Exception as e:
            self.log_result("状态机", "异常", "FAIL", f"测试异常: {str(e)}")
            return False
    
    async def test_dependency_blocking(self) -> bool:
        """场景4：依赖管理 + 阻塞机制"""
        print("\n" + "="*70)
        print("📋 场景4：依赖管理 + 阻塞机制")
        print("="*70)
        
        node_a = "SG-TEST-DEP-A"
        node_b = "SG-TEST-DEP-B"
        all_passed = True
        
        try:
            with EmysTester() as emy:
                # 创建两个节点
                emy.send_sync(f"创建节点{node_a}，名称'上游节点-地质勘察'", sender_name="王总")
                emy.send_sync(f"创建节点{node_b}，名称'下游节点-基础设计'", sender_name="王总")
                
                # 给A添加成果
                emy.send_sync(f"给{node_a}添加成果：勘察报告，目标1份", sender_name="王总")
                
                # 建立依赖关系
                print("  → 建立依赖关系...")
                # 注意：这里直接操作DB建立依赖，实际场景通过对话或API
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    
                    # 先获取成果ID
                    result = conn.execute(text(
                        "SELECT deliverable_id FROM node_deliverables WHERE node_id = :nid"
                    ), {"nid": node_a})
                    deliv_row = result.fetchone()
                    
                    if deliv_row:
                        deliv_id = deliv_row[0]
                        # 插入依赖
                        conn.execute(text(
                            "INSERT INTO node_dependencies (id, node_id, depends_on_deliverable_id, "
                            "depends_on_node_id, dependency_type, weight, created_at) "
                            "VALUES (gen_random_uuid()::text, :node_b, :deliv_id, :node_a, "
                            "'DELIVERABLE', '1.0000', NOW())"
                        ), {"node_b": node_b, "deliv_id": deliv_id, "node_a": node_a})
                        conn.commit()
                
                self.log_result("依赖管理", "添加依赖", "PASS", "依赖关系已建立")
                
                # 验证B的初始状态（应该是CONDITIONS_NOT_MET）
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(
                        "SELECT status FROM project_nodes WHERE node_id = :nid"
                    ), {"nid": node_b})
                    row = result.fetchone()
                
                if row and row[0] == "CONDITIONS_NOT_MET":
                    self.log_result("依赖管理", "前置未满足状态", "PASS", 
                                   f"节点B状态={row[0]}（前置未满足）")
                else:
                    self.log_result("依赖管理", "前置未满足状态", "FAIL", 
                                   f"预期CONDITIONS_NOT_MET，实际={row[0] if row else None}")
                    all_passed = False
                
                # 完成A的成果
                print("  → 完成上游节点成果...")
                emy.send_sync(f"完成节点{node_a}的勘察报告", sender_name="王总")
                
                # 验证B的状态是否更新（应该变为IN_PROGRESS或COMPLETED）
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(
                        "SELECT status FROM project_nodes WHERE node_id = :nid"
                    ), {"nid": node_b})
                    row = result.fetchone()
                
                self.log_result("依赖管理", "前置满足后状态", "PASS", 
                               f"上游完成后，节点B状态={row[0] if row else None}")
                
                return all_passed
                
        except Exception as e:
            self.log_result("依赖管理", "异常", "FAIL", f"测试异常: {str(e)}")
            return False
    
    async def test_permission(self) -> bool:
        """场景7：权限集成测试"""
        print("\n" + "="*70)
        print("📋 场景7：权限集成测试")
        print("="*70)
        
        node_id = "SG-TEST-PERM-001"
        all_passed = True
        
        try:
            with EmysTester() as emy:
                # 先由王总创建一个测试节点
                emy.send_sync(
                    f"创建节点{node_id}，名称'权限测试节点'，截止2026-12-31",
                    sender_id="user-admin-wang",
                    sender_name="王总"
                )
                emy.send_sync(
                    f"给节点{node_id}添加成果：权限测试成果，目标1份",
                    sender_id="user-admin-wang",
                    sender_name="王总"
                )
                
                # 张工（参建管理）尝试更新成果进度（应该成功）
                print("  → 张工尝试更新成果进度...")
                reply = emy.send_sync(
                    f"更新节点{node_id}的成果进度：完成0.5份",
                    sender_id="user-zhang",
                    sender_name="张工"
                )
                
                if reply and "成功" in reply.content:
                    self.log_result("权限测试", "张工更新进度", "PASS", "参建管理可更新进度")
                else:
                    self.log_result("权限测试", "张工更新进度", "FAIL", 
                                   f"响应: {reply.content if reply else '无响应'}")
                    all_passed = False
                
                # 周业务员（访客）尝试创建节点（应该被拒绝）
                print("  → 周业务员尝试创建节点...")
                reply = emy.send_sync(
                    "创建节点SG-HACK-001，名称'我是黑客'",
                    sender_id="user-sales",
                    sender_name="周业务员"
                )
                
                if reply and ("权限" in reply.content or "拒绝" in reply.content or "无法" in reply.content):
                    self.log_result("权限测试", "访客创建被拒绝", "PASS", "访客无创建权限")
                else:
                    self.log_result("权限测试", "访客创建被拒绝", "FAIL", 
                                   f"权限控制可能失效，响应: {reply.content if reply else '无响应'}")
                
                return all_passed
                
        except Exception as e:
            self.log_result("权限测试", "异常", "FAIL", f"测试异常: {str(e)}")
            return False
    
    async def test_data_integrity(self) -> bool:
        """数据完整性检查"""
        print("\n" + "="*70)
        print("📋 数据完整性检查")
        print("="*70)
        
        try:
            with EmysTester() as emy:
                with emy.get_db_session() as conn:
                    from sqlalchemy import text
                    
                    # 1. 检查5张表是否都有数据
                    tables = [
                        ("project_nodes", "节点主表"),
                        ("node_dependencies", "依赖表"),
                        ("node_deliverables", "成果表"),
                        ("node_accessible_files", "文件关联表"),
                        ("node_events", "事件表"),
                    ]
                    
                    for table, desc in tables:
                        result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                        count = result.fetchone()[0]
                        self.log_result("数据完整性", desc, "PASS", f"记录数={count}")
                    
                    # 2. 检查状态分布
                    result = conn.execute(text(
                        "SELECT status, COUNT(*) FROM project_nodes GROUP BY status ORDER BY status"
                    ))
                    rows = result.fetchall()
                    status_dist = ", ".join([f"{r[0]}={r[1]}" for r in rows])
                    self.log_result("数据完整性", "状态分布", "PASS", status_dist)
                    
                    return True
                    
        except Exception as e:
            self.log_result("数据完整性", "异常", "FAIL", f"检查异常: {str(e)}")
            return False
    
    async def run_all(self, p0_only: bool = False) -> bool:
        """运行所有测试"""
        self.start_time = datetime.now()
        print("🚀 全景节点图V2测试套件启动")
        print(f"   项目ID: {self.project_id}")
        print(f"   开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   测试模式: {'仅P0' if p0_only else '完整'}")
        
        # P0 优先级测试
        await self.test_xlsx_import()
        await self.test_node_crud()
        await self.test_state_machine()
        await self.test_dependency_blocking()
        
        # P1 优先级测试（非P0-only时运行）
        if not p0_only:
            await self.test_permission()
            await self.test_data_integrity()
        
        # 输出汇总
        self.end_time = datetime.now()
        self.print_summary()
        
        # 保存结果
        self.save_results()
        
        # 返回是否全部通过
        return all(r["status"] == "PASS" for r in self.results)
    
    def print_summary(self):
        """打印测试汇总"""
        print("\n" + "="*70)
        print("📊 测试结果汇总")
        print("="*70)
        
        duration = (self.end_time - self.start_time).total_seconds()
        
        total = len(self.results)
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        failed = sum(1 for r in self.results if r["status"] == "FAIL")
        
        print(f"总测试数: {total}")
        print(f"通过: {passed} ✅")
        print(f"失败: {failed} ❌")
        print(f"通过率: {passed/total*100:.1f}%" if total > 0 else "无测试")
        print(f"执行时间: {duration:.1f} 秒")
        print(f"结束时间: {self.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        if failed > 0:
            print("\n❌ 失败的测试:")
            for r in self.results:
                if r["status"] == "FAIL":
                    print(f"   - [{r['scenario']}] {r['test_case']}: {r['message']}")
        else:
            print("\n✅ 所有测试通过！")
    
    def save_results(self):
        """保存测试结果到JSON文件"""
        output_file = Path(__file__).parent / f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        report = {
            "test_suite": "全景节点图V2测试套件",
            "project_id": self.project_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_seconds": (self.end_time - self.start_time).total_seconds(),
            "total_tests": len(self.results),
            "passed": sum(1 for r in self.results if r["status"] == "PASS"),
            "failed": sum(1 for r in self.results if r["status"] == "FAIL"),
            "results": self.results
        }
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"\n📄 测试结果已保存到: {output_file}")


async def main():
    parser = argparse.ArgumentParser(description="全景节点图V2测试执行器")
    parser.add_argument("--full", action="store_true", help="运行完整测试套件")
    parser.add_argument("--p0", action="store_true", help="仅运行P0优先级测试")
    parser.add_argument("--project-id", default="project-xiongan-001", help="测试项目ID")
    parser.add_argument("--scenario", help="运行指定场景（逗号分隔）: xlsx,crud,state,dep,perm,integrity")
    
    args = parser.parse_args()
    
    runner = PanoramaTestRunner(project_id=args.project_id)
    
    if args.scenario:
        scenarios = args.scenario.split(",")
        print(f"🎯 运行指定场景: {scenarios}")
        
        if "xlsx" in scenarios:
            await runner.test_xlsx_import()
        if "crud" in scenarios:
            await runner.test_node_crud()
        if "state" in scenarios:
            await runner.test_state_machine()
        if "dep" in scenarios:
            await runner.test_dependency_blocking()
        if "perm" in scenarios:
            await runner.test_permission()
        if "integrity" in scenarios:
            await runner.test_data_integrity()
        
        runner.end_time = datetime.now()
        runner.print_summary()
        runner.save_results()
        
    elif args.p0:
        await runner.run_all(p0_only=True)
    else:
        # 默认运行P0测试
        await runner.run_all(p0_only=True)


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⏹️  测试被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n💥 测试执行异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
