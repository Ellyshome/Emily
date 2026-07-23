# 硅基流动位图识别技能

## 简介

这是一个基于硅基流动API的位图识别技能，使用先进的AI模型将图片中的文字转化为可编辑文本。

## 功能特点

- 使用硅基流动的先进AI模型进行文字识别
- 支持批量处理多张图片
- 高准确率的文字识别和排版
- 支持多种图片格式（JPG、PNG、BMP等）
- 保持文档结构和排版格式

## 安装

1. 确保已安装Python 3.7或更高版本

2. 安装依赖包：

```bash
pip install -r requirements.txt
```

3. 配置API密钥：

   - 方法1：在`config.json`文件中设置`api_key`
   - 方法2：使用命令行参数`--api-key`提供

## 使用方法

### 基本用法

```bash
python main.py --images "path/to/images/*.jpg"
```

### 指定输出格式

```bash
# 输出为Markdown格式
python main.py --images "path/to/images/*.jpg" --output markdown

# 输出为JSON格式
python main.py --images "path/to/images/*.jpg" --output json
```

### 指定输出目录

```bash
python main.py --images "path/to/images/*.jpg" --output-dir ./results
```

### 提供API密钥

```bash
python main.py --images "path/to/images/*.jpg" --api-key YOUR_API_KEY
```

## 示例

### 识别单张图片

```bash
python main.py --images "test.jpg"
```

### 批量识别图片

```bash
python main.py --images "images/*.jpg" --output markdown --output-dir ./output
```

## 配置选项

在`config.json`文件中可以设置以下选项：

- `api_key`：硅基流动API密钥
- `api_url`：API请求地址
- `model`：使用的模型名称

## 注意事项

- 需要有效的硅基流动API密钥
- 图片大小建议不超过10MB
- 网络连接需要稳定
- 批量处理大量图片时可能需要较长时间

## 故障排除

1. **API密钥错误**：确保提供了正确的硅基流动API密钥

2. **网络错误**：检查网络连接是否稳定

3. **图片格式错误**：确保图片格式为JPG、PNG、BMP等常见格式

4. **内存错误**：处理大量图片时可能需要增加系统内存

## 性能说明

- **识别准确率**：95%以上（针对清晰的印刷体）
- **处理速度**：单张图片约2-5秒（取决于网络速度）
- **批量处理**：支持同时处理多张图片
- **支持格式**：JPG、PNG、BMP、TIFF等常见图片格式
