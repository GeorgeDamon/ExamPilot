# 使用与部署

## 1. 安装后先模拟

运行 `./install.sh`，或在虚拟环境 `pip install -e '.[test]'`。脚本安装官方 Chromium 和依赖，创建空白本机配置，运行测试、模拟及 plist 生成；仅 `./install.sh --schedule` 才注册 launchd。

`exampilot demo` 不访问真实服务。`exampilot demo --lost-response` 检查服务端提交后丢失响应的恢复策略。通过模拟不等于真实接口已兼容。

wheel 包包含 CLI 与模板；执行 `exampilot init` 创建 `.env` 和 `config.yaml`，不会覆盖已有文件。运行目录默认是当前目录，可由 `EXAMPILOT_HOME` 指定。需要 macOS 安装脚本时使用完整仓库。

## 2. 课程配置与正常登录

在 `config.yaml` 填入 courseid、clazzid、cpi 对应数字字符串。`.env` 可配置：

```text
DEEPSEEK_API_KEY=
AUTOEXAM_COURSE_URL=
AUTOEXAM_NTFY_URL=
AUTOEXAM_NTFY_TOKEN=
```

课程网址必须与目标 ID 一致，允许使用用户从平台复制的完整签名网址，不伪造签名。签名会失效，需正常重新获取。无签名课程 URL 曾在实测中被平台拒绝；不能假设只凭 ID 就能构造有效课程页面。

```sh
chmod 600 .env
.venv/bin/exampilot login
.venv/bin/exampilot list
.venv/bin/exampilot ai-test
```

在正常浏览器中完成登录、协议与验证，回到终端按回车保存会话。密码不保存，Cookie/存储状态在私有 `runtime/session.json`。会话失效时要求重新正常登录，不解除 CAPTCHA 或人脸限制。

环境变量优先于 `.env`；`.env` 只接受所列纯键值项，不执行 shell。DeepSeek Key 只发送官方 API。默认 deepseek-flash、非思考、JSON 输出，模型可改；改动后跑 `ai-test`。

## 3. 真实读取检查

保持 `dry_run=true`，在获准的练习试卷中人工正常进入考试，然后运行：

```sh
.venv/bin/exampilot probe --exam-id 练习考试ID
```

仅接受能够明确识别为正在作答的试卷。列表只显示“未交”时不能推断已经开考。程序不会为测试点击开始按钮，不填写、保存或交卷。未知 PC 表单或分页会停止；当前 PC 作答流程尚需适配实测。

完整题数、连续题号、题目 ID、选择题类型、选项、试卷身份和服务端剩余时间均通过后，生成与代码/课程绑定的本机验证记录。不要手工伪造记录。

## 4. 受监督保存与交卷

通过真实读取后，先由用户主动设置 `dry_run=false, auto_submit=false`，单次运行并核查答案保存/读回，再人工交卷。之后在获准的受监督练习中测试自动交卷及服务端确认，最后才评估无人值守部署。

当前代码不保证 PC 作答页兼容。未知按钮/多重匹配、签名过期、其他题型、未读图片/复杂公式、考试承诺书、考试码、人脸核验或专用客户端都会停止。

AI 返回合法选项也可能答错或漏选。日志只说“AI 推测答案”；不会把它写成平台确认的正确答案。

## 5. 状态与故障恢复

SQLite 仅保存 tasks/events 和时间，没有题目数据库。请求前记录 `SUBMITTING`；响应异常先查询，服务端明确完成才记 `SUBMITTED`。不明确则 `SUBMISSION_UNKNOWN` 并要求核对，不自动重交。

同一课程/班级/cpi/考试 ID 去重。崩溃重启不回退已确认提交。失败/人工处理/dry-run 的任务可在停止监控后明确重试：

```sh
.venv/bin/exampilot retry --exam-id 考试ID
```

此命令不能清除已提交或提交未知状态。连续请求失败/登录失败会暂停定时任务；修复后手动 `./start.sh` 或重新登录恢复。

## 6. 时间与睡眠

默认 Asia/Shanghai，周三 07:59 启动检查，08:00—10:00 监控，间隔 30 秒。启动时已存在可参加考试立即处理。10:00 后不进入新考试，已进入任务在有界截止时间内继续；重启仅恢复已知未完成任务。

```sh
./install.sh --schedule
launchctl print gui/$(id -u)/com.local.exampilot
```

plist 使用系统本地时间日历触发，并每 60 秒运行轻量窗口检查用于恢复；窗口外不请求平台或 AI。单实例锁覆盖浏览器生命周期。`caffeinate` 只防止运行期间空闲睡眠，不保证唤醒。

接通电源并保持登录，先在相同合盖/睡眠条件下演练。FileVault 冷启动后需要登录。可参考 [Apple 定时电源说明](https://support.apple.com/zh-cn/guide/mac-help/-mchl40376151/mac)，自行检查 `pmset -g sched`。安装脚本不替换现有管理员电源计划。实际唤醒和重启尚未验证。

## 7. 通知、日志与控制

macOS 通知默认开启。启用手机通知时配置 HTTPS ntfy 地址/可选 token，并设置 `notification.mobile=true`。发送失败与服务端提交状态分开记录；不会把调用成功说成手机已收到。

```sh
./start.sh --once
./start.sh
./status.sh
tail -n 100 runtime/autoexam.log
./stop.sh
./uninstall.sh
```

手动 run 可在窗口外调试，持续到停止。停止会暂停后续定时启动，并在有界调用结束后停止，不强杀或擅自交卷。卸载保留私有配置和状态；清除会话请自行删除 session.json。

主日志轮换，launchd stdout/stderr 需按部署环境管理保留空间。不要把原始日志或签名网址上传公开 Issue。

