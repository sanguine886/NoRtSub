# NoRtSub

通过邮箱验证码无密码登录 ChatGPT，获取 Session 并转换为 sub2api 格式。

## 快速开始

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
# 编辑 config.yaml 填写代理地址
python web.py
```

浏览器访问 `http://localhost:5000`，输入 Key 即可。

## 命令行

```bash
python main.py                    # 交互式输入
python main.py --keys-file=keys.txt  # 从文件读取
```

## 功能

- 无密码 OTP 登录，无需浏览器
- Web UI 实时日志 + 批量处理
- Exchange API 多域名自动回退
- 自动转换 sub2api 格式，支持合并导出

## 配置

```yaml
proxy: "http://127.0.0.1:7890"    # 代理（必须）
output_dir: "data/sessions"        # 输出目录
# exchange_api_url: "https://plus3.yhmoai.online"  # 可选
```

## auth_core

`utils/auth_core.*` 提供设备指纹和 sentinel token 生成，是登录 OpenAI 的必需模块。

支持平台：Windows (.pyd)、macOS (.so)、Linux x86_64/aarch64 (.so)

首次运行需初始化许可证数据到 `data/data.db`。

## 免责声明

仅供学习研究，请勿用于商业或非法用途。
