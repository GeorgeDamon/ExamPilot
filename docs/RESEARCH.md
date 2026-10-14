# Phase 1 调查记录（2026-10-14）

状态：源码及许可证调查 PASS；当前课程 PC 列表实测 PASS；真实作答/提交兼容性 PARTIAL。

## 参考项目与版本依据

| 项目 | 读取范围与版本依据 | 许可证 | 可利用的内容 | 不直接引入的原因 |
|---|---|---|---|---|
| [d4renk/renk-CxKitty](https://github.com/d4renk/renk-CxKitty) | main 分支 GitHub 官方源码压缩包；SHA-256 `279b5c938074500f960e26a68a01e4ac243cc6625f85cf77e1545593274f27e8`。git clone 两次传输中断后改用 codeload。未宣称这个分支压缩包是 commit 固定快照。 | GPL-3.0；本项目采用 GPL-3.0-only，保留完整 LICENSE。 | classes.py 指定课程手机列表；exam.py 题目 ID/类型/选项/表单和答题卡结构；会话保存思路。 | 原依赖限制 Python <3.12，包含旧 TUI、题库、OCR/OpenCV 等；默认逻辑跳过承诺书/重考须知、上传人脸结果、生成安全相关参数。不能整包运行或导入。 |
| [DanMo661/Xuexitong-mcp](https://github.com/DanMo661/Xuexitong-mcp) | 只读检查 commit `ebce2cc735bc7acc45fac7ccf32bcefe1a6b2a54` 的 client.py、server.py、pyproject.toml、LICENSE 等。 | MIT，Copyright 2026 Wu Jiale。 | 普通账户登录接口、会话/连接复用、只读课程定位参考。 | fetch_exams 是全校考试安排；无完整指定课程自动交卷链路。Cookie 存储丢失域/路径，失败探测容错偏宽，POST 被纳入通用重试，均不能原样用于可靠交卷。 |

没有执行参考项目代码。没有复制人脸/OCR/题库模块。协议字段作为参考，正式提交通过正常浏览器操作，不伪造其签名和风控字段。全新模块分离平台、AI、状态、通知和调度。未运行依赖 CVE 全量审计，不能将源码检查表述为完整安全认证。

## 已从源码核对的接口（不等于当前平台实测）

CxKitty `cxapi/classes.py`：

- `https://mooc1-api.chaoxing.com/exam/phone/task-list`，courseId/classId/cpi。
- `ul.nav li[data]` 提供 `taskrefId` 和平台生成的入口参数。

CxKitty `cxapi/exam.py`：

- `/exam-ans/exam/phone/task-exam`：封面。
- `/exam-ans/exam/phone/start`：进入/开始，可能启动倒计时。
- `/exam-ans/exam/test/reVersionTestStartNew`：单题。
- `/exam-ans/exam/phone/preview`：整卷预览。
- `/exam-ans/exam/phone/loadAnswerStatic`：答题卡。
- `/exam-ans/exam/test/reVersionSubmitTestNew`：临时保存/正式提交。
- 题型编号 0=单选、1=多选；`questionWrap.singleQuesId.ans-cc-exam`、`questionId`、`type{ID}`、`answerList.radioList[name]` 与 `cc` 是源代码解析依据。

本版没有直接调用手机端开始/提交接口。没有添加跳过承诺、忽略活体、强制 redo、monitorStatus 等源项目控制字段。源码可读性尚可，但原题干解析基于 children 位置切片，可能丢失结构变化后的文本；新解析按已知元数据节点移除，未知/重复/缺失字段失败停止。

## 此前授权课程的结构观察（公开值为合成示例）

公开示例 ID 是合成值；实际账户/课程标识不随仓库发布。

在正常登录后，去掉签名的课程 URL 返回“无效的参数”；用户完整网址有效。通过课程菜单“考试”，真实 PC iframe 路径为：

`https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list`

当前 iframe ID `frame_content-ks`。考试行使用 `ul li` → `[onclick]` → `.right-content` → `p.overHidden2`/`p.status`。两条已完成/过期记录的点击处理是 `viewExamAnswer(考试ID,作答记录ID)`。公开合成结构样本在 `tests/fixtures/live_list_sanitized.html`。只解析考试 ID，不执行从页面读取的任意 JavaScript。

有始未交/正在进行考试时使用何种 PC handler、是否有分页以及实际 PC 题目表单，当前没有实测。源码中的手机试卷选择器不能据此宣称适用于当前电脑端试卷。

## DeepSeek 官方参数核查

2026-10-14 检查官方 [首次调用](https://api-docs.deepseek.com/quick_start)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 和 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

官方文档支持 `deepseek-flash`、`thinking.type=disabled`、`response_format.type=json_object`；JSON 模式需明确 JSON 指令并合理设置 max_tokens，可能有空输出。实现检查 finish_reason，拒绝截断/异常封装，有限修复与补答。

用户随后明确授权将 Key 写入私有 `.env`。官方实际 API 参数探测 PASS；20 道合成算术题一次批量请求完整返回，20/20 与已知算术控制答案一致，约 2.117 秒。此结果只验证这些合成题，不能证明课程考试准确率或稳定延迟。证据见 `live-ai-smoke.json`。多模态是可选项，本次没有实现；图片存在即阻止自动作答。

