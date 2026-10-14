# Contributing to ExamPilot

欢迎贡献正常平台兼容性、状态恢复、时间预算、合成测试和文档改进。贡献按项目 GPL-3.0-only 许可发布。

## 开发环境

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
PLAYWRIGHT_BROWSERS_PATH="$PWD/.browser" .venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q
.venv/bin/ruff check autoexam tests scripts
.venv/bin/ruff format --check autoexam tests scripts
.venv/bin/python scripts/check_public_tree.py
```

测试必须使用模拟服务器/合成题目，不依赖真实账号或 API Key。CI 不应对正式平台发出请求，也不应申请生产凭据。

新增平台解析请提供去除账号、签名和真实题目内容的合成 HTML。分别报告源码参考、合成验证、真实只读观察和受监督读写实测，不把一种结果推成另一种结果。

PR 请解释具体问题、改变后的行为和验证结果。不要提交 `.env`、`config.yaml`、`session.json`、数据库、日志、生成 plist 或本机浏览器。不要加入验证码破解、人脸伪造、绕过承诺/客户端限制或无界轮询。

## 发布

遵循 [发布指南](docs/RELEASE.md)。构建产物必须检查内容与凭据隔离，版本应在 pyproject.toml 和 autoexam/__init__.py 一致。尚未验证的能力必须保留 Alpha 标识。

