# ExamPilot · 考航

**面向 macOS 的开源超星学习通自动化项目：指定课程考试监控、DeepSeek AI 批量答题、任务恢复与通知。**

[English](README.en.md) · [快速开始](#快速开始) · [功能与验证状态](#功能与验证状态) · [使用指南](docs/USAGE.md) · [贡献指南](CONTRIBUTING.md) · [测试报告](docs/TEST_REPORT.md)

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/License-GPL--3.0--only-green)
![Stage](https://img.shields.io/badge/Stage-Alpha-orange)

ExamPilot 希望把学习通自动化中最容易出错的环节变得可检查：是否读到了指定课程的考试、题目与选项是否对应、AI 输出是否完整、提交到底有没有被服务端确认。项目适用于已获学校或课程允许的自动化场景，以及本地模拟、接口研究和受监督的练习考试。

**当前是 `v0.1.0a1` Alpha。默认 `dry_run=true`。模拟完整链路已经验证，真实登录、会话恢复、课程列表读取和 DeepSeek 合成题调用已有实测；当前 PC 试卷的完整读取、答案保存与正式交卷仍待验证。** 不应把这一版本视为已保证无人值守通过考试的成熟工具。

## 你能用它做什么

- 只监控你配置的课程考试入口，使用考试 ID 去重。
- 批量向 DeepSeek 官方 API 发送选择题，校验 JSON、题目 ID、单选/多选和合法选项。
- 持久化任务状态。提交响应丢失后先查询，不盲目重复交卷。
- 通过 macOS launchd 在配置窗口运行，提供单实例、手动停止和故障暂停。
- 使用 macOS 通知和可选 ntfy 手机通知。
- 不登录、不配置 API Key，就能用本机模拟服务器运行完整演示和测试。

项目不包含历史题库、第三方搜题平台、视频刷课、人脸伪造或验证码破解模块。遇到验证要求、未知页面结构、未读图片或复杂公式时，相关操作会停止并告警。

## 快速开始

推荐环境：macOS Apple Silicon、Python 3.11+。先在终端进入项目目录：

```sh
git clone https://github.com/GeorgeDamon/ExamPilot.git
cd ExamPilot
./install.sh
```

安装脚本会创建独立环境、安装官方 Chromium、执行本地测试并生成本机配置。**不带 `--schedule` 时不会注册定时任务，也不会登录或提交正式考试。**

先看一次完整模拟：

```sh
.venv/bin/exampilot demo
.venv/bin/exampilot demo --lost-response
```

`demo` 使用合成题目、模拟 AI 和仅监听 `127.0.0.1` 的 HTTP 服务，不访问学习通或 DeepSeek，不消耗 API 费用。第二个命令模拟“服务端已提交，但 HTTP 响应丢失”。

你也可以只安装 Python 命令行包：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/exampilot init
.venv/bin/exampilot demo
```

`exampilot` 是主命令，`autoexam` 保留为兼容别名。通过 wheel 安装时，默认在当前目录放配置/状态；可用 `EXAMPILOT_HOME` 指定目录。macOS 调度脚本需要完整源码包。

## 连接自己的学习通课程

编辑本机 `config.yaml`，填入课程网址中的三个数字字符串：

```yaml
monitor:
  target_course_id: '你的courseid'
  target_class_id: '你的clazzid'
  target_cpi: '你的cpi'
```

然后编辑本机 `.env`：

```text
DEEPSEEK_API_KEY=你的DeepSeek官方Key
AUTOEXAM_COURSE_URL=从学习通复制的完整课程网址
```

完整网址可能包含平台签名，它属于本机私有配置。不要上传 `.env`、Cookie 或真实课程网址。

```sh
chmod 600 .env
.venv/bin/exampilot login       # 正常浏览器登录，不保存密码
.venv/bin/exampilot list        # 只读课程考试列表
.venv/bin/exampilot ai-test     # 官方 API 小样本验证，消耗少量 token
./start.sh --once              # 单次检测，默认 dry-run
./status.sh
```

要测试真实读取，需要一场由用户正常进入的受监督练习考试。详情见 [使用指南](docs/USAGE.md)。不要直接关闭 dry-run；程序要求与当前代码和目标课程绑定的真实读取检查记录。

## 功能与验证状态

| 功能 | 当前状态 |
|---|---|
| 模拟监控 → 分页读取 → AI → 保存 → 提交 → 服务端确认 | 已通过本地 HTTP 和浏览器合成页面测试 |
| 正常登录、Cookie/存储状态保存、关闭重开恢复 | 已有一次真实账户实测；长期过期恢复仍需演练 |
| 指定课程 PC 考试列表 | 已有真实只读实测；已完成/过期状态有证据 |
| DeepSeek 官方调用、批量 JSON 答案 | 已有合成题真实调用实测；不等于课程答案正确 |
| 当前 PC 试卷全部题目和分页读取 | 待受监督练习试卷验证 |
| 当前 PC 答案保存、读回、正式提交 | 待真实兼容性验证 |
| 图片与复杂公式 | 本版未实现完整解析，停止自动作答 |
| 手机送达、实际睡眠唤醒和重启部署 | 待验证 |

模型答案只标为推测。结构校验不能证明答案正确，也不能识别所有语义上的多选漏选。只有服务端明确“已完成/已提交”才能记为 `SUBMITTED`；HTTP 200 或按钮点击不是确认。

## 定时运行

默认：每周三北京时间 07:59 启动检查，08:00—10:00 每 30 秒监控。窗口外不进入新考试；已经进入的工作继续执行有界处理。正式上线前应完成受监督练习验证。

```sh
./install.sh --schedule
./status.sh
./stop.sh
./uninstall.sh
```

launchd 不保证唤醒睡眠中的 Mac。运行时使用 `caffeinate` 防止空闲睡眠；接电、登录状态、合盖和唤醒计划需要按你的设备演练。安装脚本不会修改管理员电源计划。完整说明在 [使用指南](docs/USAGE.md)。

## 项目结构

```text
autoexam/               平台适配、AI Provider、状态机、存储、通知和 CLI
autoexam/resources/     wheel 内置的空白配置模板
tests/                  合成测试和根据观察结构构造的脱敏样本
scripts/                调度入口和公开内容检查
.github/                macOS / Python 3.11–3.13 CI、Issue/PR 模板
docs/                   使用、架构、参考调查、验证和发布指南
install.sh ...          安装、启动、停止、状态和卸载
LICENSE                 GPL-3.0-only
```

## 帮助项目完善

欢迎提交可复现的 Bug、合成 HTML 样本和改进建议，尤其是课程考试列表变化、分页、正常会话恢复、时间预算和提交状态确认。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)，不要在公开 Issue 上传真实试卷、账号或凭据。

如果 ExamPilot 对你有帮助，可以 Star 或分享仓库地址。相关关键词：**超星学习通 / Xuexitong / Chaoxing / 学习通自动化 / 考试监控 / AI 答题 / DeepSeek / Python / macOS / launchd / Playwright**。

项目不是超星官方产品。参考调查与致谢见 [NOTICE.md](NOTICE.md)；代码许可见 [LICENSE](LICENSE)。

