# 发布检查

版本目前为 0.1.0a1。先确保默认 dry-run 和 Alpha 状态说明仍在。不要发布任何本机凭据、私有课程信息或运行状态。

```sh
python -m pytest -q
ruff check autoexam tests scripts
ruff format --check autoexam tests scripts
python scripts/check_public_tree.py
python -m build
python -m twine check dist/*
```

同时检查 wheel/sdist 内部成员：仅包含源码、模板、许可和必要文档。使用新的环境安装 wheel，在仓库外执行 `exampilot init` 和 `exampilot demo`；不能依赖源码目录或原账号。

源码 ZIP 应从已审核的 Git commit 用 `git archive` 生成，不压缩整个工作目录。可另交付 Git bundle 保留可恢复历史。生成 SHA-256 校验文件。只有完成这些检查后才打 Alpha tag。

GitHub Release 使用 Alpha 标识。真实接口、模型权限、通知与 macOS 部署的验证分别记录，不以 CI 绿色代替真实平台验收。CI 执行合成测试，不使用生产账号/Key。更新内容需说明已验证范围。

本项目没有自动上传 PyPI 的工作流；PyPI 发布需要单独确认账号/项目名称和发布授权。

