import base64
import requests
import sys
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

# =========================
# 配置
# =========================

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

# =========================
# 加载配置
# =========================
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# =========================
# 图片转 base64
# =========================
def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# =========================
# OCR 识别
# =========================
def ocr_image(image_path):

    config = load_config()

    api_key = config.get("api_key")
    api_url = config.get(
        "api_url", "https://api.siliconflow.cn/v1/chat/completions"
    )
    model = config.get("model", "Qwen/Qwen3-VL-8B-Thinking")
    print(f"  -> 使用模型: {model}")

    if not api_key:
        raise Exception("未在 config.json 中设置 API 密钥")

    # 图片转base64
    try:
        img_base64 = image_to_base64(image_path)
    except Exception as e:
        raise Exception(f"图片转换失败: {str(e)}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    data = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "这是一张教材书页的照片，请识别并原样抄写图片中所有文字，要求如下：\n"
                            "\n"
                            "【识别要求】\n"
                            "1. 不要用代码块（```）包裹输出内容；\n"
                            "2. 按照从上到下、从左到右的顺序输出；\n"
                            "3. 不论文字位于何处（正文、色块、边框、表格、页眉、页脚、绿色背景框等），一律原样收录，不得遗漏；\n"
                            "4. 表格内容按行输出，单元格之间用 | 分隔；\n"
                            "5. 不要添加任何额外的 Markdown 格式、标题符号（#）或加粗（**）；\n"
                            "6. 不对内容做任何归类、总结或结构调整，只需忠实抄写原文；\n"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_base64}"
                        },
                    },
                ],
            }
        ],
        "max_tokens": 4096,
    }

    # API请求
    try:
        print(f"  -> 发送API请求...")
        response = requests.post(api_url, headers=headers, json=data, timeout=300)
        
        # 检查响应状态
        if response.status_code != 200:
            raise Exception(f"API请求失败，状态码: {response.status_code}, 响应: {response.text[:500]}")

        result = response.json()

        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        else:
            raise Exception(f"API 响应错误：{result}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"网络请求失败: {str(e)}")
    except Exception as e:
        raise Exception(f"API处理失败: {str(e)}")


# =========================
# 从识别文本中提取页码
# =========================
def extract_page_number(text):
    """从文本中查找页码标注，支持多种格式，优先检查开头和末尾"""
    
    # 如果还是没找到，尝试从最后一行提取纯数字
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    if lines:
        # 检查最后3行，看是否有单独的数字
        for line in lines[-3:]:
            # 匹配单独的数字（可能带有空白字符）
            match = re.match(r"^\s*(\d{1,3})\s*$", line)
            if match:
                page_num = int(match.group(1))
                if 1 <= page_num <= 999:
                    print(f"  -> 从最后一行识别到页码: {page_num}")
                    return page_num
    
    print(f"  -> 未识别到页码")
    return None


# =========================
# 处理单个图片
# =========================
def process_image(image_path):

    try:
        print(f"正在 OCR 识别...")
        text = ocr_image(image_path)
        
        # 提取页码
        page_number = extract_page_number(text)
        
        # 调试信息：显示提取到的页码
        if page_number:
            print(f"  -> 识别到页码 {page_number}")
        else:
            print(f"  -> 未识别到页码")
        
        # 创建结果
        page_results = [{
            "file": image_path,
            "success": True,
            "text": text.strip(),
            "page_number": page_number,
        }]

        return page_results

    except Exception as e:
        print(f"  -> 识别失败: {str(e)}")
        return [{
            "file": image_path,
            "success": False,
            "error": str(e),
            "page_number": None,
        }]


# =========================
# 从文本生成文件名
# =========================
def generate_filename_from_text(text, extension, base_dir=None, source_filename=None):

    if base_dir is None:
        base_dir = os.getcwd()

    if not extension.startswith("."):
        extension = "." + extension

    # 获取来源文件名（不含扩展名）
    if source_filename:
        source_name = os.path.splitext(os.path.basename(source_filename))[0]
    else:
        source_name = "unknown"
    
    # 构建文件名：[页码：XXX]_来源文件名
    page_num = extract_page_number(text)
    if page_num:
        page_info = f"[页码：{page_num}]"
    else:
        page_info = "[页码：未知]"
    
    filename = f"{page_info}_{source_name}{extension}"
    
    # 清理文件名中的非法字符（保留方括号和冒号）
    illegal_chars = r'[<>"/\\|?*]'
    filename = re.sub(illegal_chars, "_", filename)
    
    filepath = os.path.join(base_dir, filename)

    return filepath


# =========================
# 保存文本
# =========================
def save_text_to_file(text, filepath):
    """保存文本到文件，如果文件已存在则生成新文件名"""
    # 检查文件是否存在
    if os.path.exists(filepath):
        # 生成新文件名
        base_dir = os.path.dirname(filepath)
        filename = os.path.basename(filepath)
        name, ext = os.path.splitext(filename)
        
        # 尝试添加数字后缀
        counter = 1
        new_filepath = filepath
        while os.path.exists(new_filepath):
            new_filename = f"{name}_{counter}{ext}"
            new_filepath = os.path.join(base_dir, new_filename)
            counter += 1
            # 防止无限循环
            if counter > 100:
                break
        
        filepath = new_filepath
        print(f"  -> 文件已存在，使用新文件名: {os.path.basename(filepath)}")
    
    # 保存文件
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)
    
    return filepath


# =========================
# 主程序
# =========================
if __name__ == "__main__":

    if len(sys.argv) < 2:
        print("使用方法:")
        print("python main.py img1.jpg img2.jpg img3.png [extension] [output_dir]")
        print("或")
        print("python main.py folder_path [extension] [output_dir]")
        print("extension: 可选，例如 txt 或 md")
        print("output_dir: 可选，指定输出目录")
        sys.exit(1)

    # 判断最后一个参数是不是输出目录
    output_dir = None
    if len(sys.argv) > 2 and os.path.isdir(sys.argv[-1]):
        output_dir = sys.argv[-1]
        sys.argv = sys.argv[:-1]

    # 判断最后一个参数是不是扩展名
    image_extensions = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif")

    if sys.argv[-1].lower().endswith(image_extensions):
        image_paths = sys.argv[1:]
        output_extension = None
    else:
        image_paths = sys.argv[1:-1]
        output_extension = sys.argv[-1]

    # 处理文件夹
    expanded_image_paths = []
    for path in image_paths:
        if os.path.isdir(path):
            # 遍历文件夹中的所有图片
            for root, _, files in os.walk(path):
                for file in files:
                    if file.lower().endswith(image_extensions):
                        expanded_image_paths.append(os.path.join(root, file))
        else:
            expanded_image_paths.append(path)

    # 检查文件是否存在
    for p in expanded_image_paths:
        if not os.path.exists(p):
            print(f"文件不存在：{p}")
            sys.exit(1)

    image_paths = expanded_image_paths

    print(f"开始 OCR 识别，共 {len(image_paths)} 张图片")

    if output_extension:
        print(f"输出文件格式：{output_extension}")

    if output_dir:
        print(f"输出目录：{output_dir}")

    print("-" * 50)

    results = []

    # 逐个识别，逐个保存
    for i, image_path in enumerate(image_paths, 1):
        print(f"\n正在处理 ({i}/{len(image_paths)}): {image_path}")
        
        try:
            page_results = process_image(image_path)
            
            for result in page_results:
                results.append(result)
                
                if result["success"]:
                    status = f"[页码：{result['page_number']}]" if result.get("page_number") is not None else "[无页码]"
                    print(f"完成：{result['file']}  {status}")
                    
                    # 立即保存识别结果
                    if output_extension:
                        try:
                            # 使用指定的输出目录或默认目录
                            base_dir = output_dir if output_dir else os.path.dirname(result["file"])
                            filepath = generate_filename_from_text(
                                result["text"],
                                output_extension,
                                base_dir=base_dir,
                                source_filename=result["file"],
                            )
                            saved_filepath = save_text_to_file(result["text"], filepath)
                            print(f"已保存到：{saved_filepath}")
                        except Exception as e:
                            print(f"保存文件失败：{str(e)}")
                else:
                    print(f"\n【文件：{result['file']}】识别失败：{result['error']}")
                    
        except Exception as e:
            print(f"处理文件 {image_path} 时出错：{str(e)}")
            results.append({
                "file": image_path,
                "success": False,
                "error": str(e),
                "page_number": None,
            })

    # 按页码排序：有页码的按页码升序，无页码的追加到末尾（保持原始输入顺序）
    input_order = {p: i for i, p in enumerate(image_paths)}
    results_with_page = [r for r in results if r.get("page_number") is not None]
    results_no_page   = [r for r in results if r.get("page_number") is None]
    results_with_page.sort(key=lambda r: r["page_number"])
    results_no_page.sort(key=lambda r: input_order.get(r["file"], 0))
    sorted_results = results_with_page + results_no_page

    print("\n" + "=" * 50)
    print("识别结果汇总（已按页码排序）：")
    print("=" * 50)

    for result in sorted_results:
        if result["success"]:
            page_info = f"页码：{result['page_number']}" if result["page_number"] is not None else "无页码"
            print(f"\n【文件：{result['file']} | {page_info}】")
        else:
            print(f"\n【文件：{result['file']}】识别失败：{result['error']}")

    print("\n全部任务完成")