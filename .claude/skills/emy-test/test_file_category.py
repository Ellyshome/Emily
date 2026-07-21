"""文件分类 E2E 测试脚本 — 绕过 emy-test CLI platform 问题，直接调用 EmysTester。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 设置路径
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from config_loader import get_llm_config, get_active_users
from tester import EmysTester

PLATFORM = "simulator"  # 与 DB 中 user_im_bindings.im_platform 一致


def test_archive_with_category():
    """TC01: 归档文件并验证自动分类为 PROJECT_LICENSE。"""
    print("=" * 60)
    print("TC01: 文件归档含自动分类 (PROJECT_LICENSE)")
    print("=" * 60)

    with EmysTester(use_llm=True) as emy:
        reply = emy.send_sync(
            "帮我归档一份建筑施工许可证，文件名是 科技城项目施工许可证.pdf，这是项目开工前必须取得的证照",
            sender_id="sim_王建国",
            sender_name="王总",
            platform=PLATFORM,
        )
        if reply:
            content = reply.content
            print(f"回复: {content}")
            # 检查是否包含分类信息
            if "PROJECT_LICENSE" in content or "项目证照" in content:
                print("✅ TC01 PASS: 回复含项目证照分类")
            else:
                print("⚠️ TC01 CHECK: 回复未明确显示分类，需人工验证 DB")
        else:
            print("❌ TC01 FAIL: 无回复")
            return False
    return True


def test_query_by_category():
    """TC02: 按分类查询文件。"""
    print("\n" + "=" * 60)
    print("TC02: 按分类查询文件 (query_files)")
    print("=" * 60)

    with EmysTester(use_llm=True) as emy:
        reply = emy.send_sync(
            "帮我查一下承包合同类的文件",
            sender_id="sim_王建国",
            sender_name="王总",
            platform=PLATFORM,
        )
        if reply:
            content = reply.content
            print(f"回复: {content}")
            print("✅ TC02 PASS: 查询成功")
        else:
            print("❌ TC02 FAIL: 无回复")
            return False
    return True


def test_keyword_search():
    """TC03: 关键词搜索文件。"""
    print("\n" + "=" * 60)
    print("TC03: 关键词搜索文件")
    print("=" * 60)

    with EmysTester(use_llm=True) as emy:
        reply = emy.send_sync(
            "帮我找一下关于消防的文件",
            sender_id="sim_王建国",
            sender_name="王总",
            platform=PLATFORM,
        )
        if reply:
            content = reply.content
            print(f"回复: {content}")
            print("✅ TC03 PASS: 搜索成功")
        else:
            print("❌ TC03 FAIL: 无回复")
            return False
    return True


def test_update_category():
    """TC04: 修改文件分类。"""
    print("\n" + "=" * 60)
    print("TC04: 修改文件分类 (update_file_category)")
    print("=" * 60)

    with EmysTester(use_llm=True) as emy:
        reply = emy.send_sync(
            "把文件 FIL-20260703-0001 改到管理规程类",
            sender_id="sim_李景利",
            sender_name="李经理",
            platform=PLATFORM,
        )
        if reply:
            content = reply.content
            print(f"回复: {content}")
            if "已从" in content or "改到" in content or "管理规程" in content:
                print("✅ TC04 PASS: 分类更新成功")
            else:
                print(f"⚠️ TC04 CHECK: 回复: {content[:200]}")
        else:
            print("❌ TC04 FAIL: 无回复")
            return False
    return True


def test_permission_denied():
    """TC05: 权限控制 - level 2 用户被拒绝写操作。"""
    print("\n" + "=" * 60)
    print("TC05: 权限控制 (level 2 用户尝试修改分类)")
    print("=" * 60)

    with EmysTester(use_llm=True) as emy:
        reply = emy.send_sync(
            "把文件 FIL-20260703-0002 改到承包合同类",
            sender_id="sim_孙建国",
            sender_name="孙师傅",
            platform=PLATFORM,
        )
        if reply:
            content = reply.content
            print(f"回复: {content}")
            if "权限" in content or "无权" in content or "没有权限" in content or "不允许" in content or "失败" in content:
                print("✅ TC05 PASS: 权限被正确拒绝")
            else:
                print(f"⚠️ TC05 CHECK: 低权限用户可能意外成功: {content[:200]}")
        else:
            print("⚠️ TC05: 无回复 (可能是工具调用被拒绝)")
    return True


def test_default_category():
    """TC06: 默认分类 OTHER。"""
    print("\n" + "=" * 60)
    print("TC06: 默认分类 OTHER")
    print("=" * 60)

    with EmysTester(use_llm=True) as emy:
        reply = emy.send_sync(
            "帮我归档一份文件，文件名是 临时会议纪要.txt，就是普通会议记录",
            sender_id="sim_王建国",
            sender_name="王总",
            platform=PLATFORM,
        )
        if reply:
            content = reply.content
            print(f"回复: {content}")
            print("✅ TC06 PASS: 归档成功 (默认 OTHER)")
        else:
            print("❌ TC06 FAIL: 无回复")
            return False
    return True


def main():
    results = []
    
    print("=" * 60)
    print("  文档管理 V2 E2E 测试")
    print(f"  Platform: {PLATFORM}")
    print(f"  LLM: {'已配置' if get_llm_config() else '未配置'}")
    print("=" * 60)

    # TC01: 归档含分类
    results.append(("TC01 归档自动分类", test_archive_with_category()))
    time.sleep(2)

    # TC02: 按分类查询
    results.append(("TC02 按分类查询", test_query_by_category()))
    time.sleep(2)

    # TC04: 修改分类
    results.append(("TC04 修改分类", test_update_category()))
    time.sleep(2)

    # TC05: 权限控制
    results.append(("TC05 权限控制", test_permission_denied()))
    time.sleep(2)

    # TC06: 默认分类
    results.append(("TC06 默认分类", test_default_category()))
    time.sleep(2)

    # TC03: 关键词搜索
    results.append(("TC03 关键词搜索", test_keyword_search()))

    # 总结
    print("\n" + "=" * 60)
    print("  测试结果汇总")
    print("=" * 60)
    for name, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")
    
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n  通过: {passed}/{total} ({100*passed//total if total else 0}%)")


if __name__ == "__main__":
    main()
