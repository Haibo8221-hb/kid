# 事故记录：AI Ask 无响应（429 → 空回复）— 2026-11-03 修复

## 时间线（CST）

| 时间 | 事件 |
|---|---|
| 10-31 04:00 前后 | 用户报告 AI ask 报「LLM错误 429」 |
| 10-31 04:12 | 诊断：上游 qwen token-plan（token-plan.maas.qianwenaiapi.com）返回 `insufficient_quota`，**月度配额耗尽**，11-13 16:00 UTC 才重置。非限流抖动 |
| 10-31 04:26 | 修复①：平台 LLM 切换到 GLM coding plan（`open.bigmodel.cn/api/coding/paas/v4`，chat=`glm-5.2`，OCR=`glm-4.5v`）。旧配置备份 `data/llm_config.json.bak-qwen-20261031` |
| 10-31 04:30 | 验证 `/api/chat`（非流式）通过 ✅ —— **但漏了前端实际使用的 `/api/chat/stream`** |
| 11-02 18:35 / 11-03 03:05 | Jemma 两次提问（角斗士作业点评，2339 字符）均得到 **0 字回复**（SSE 响应仅 22 字节 = 只有 done 事件） |
| 11-03 03:50 | 复现并定位根因②（见下） |
| 11-03 03:54 | 修复②上线，用原题端到端验证通过（2049 字流式回复）✅ |

## 根因

### 根因①：上游配额耗尽（429）
qwen token-plan 为订阅制月配额，耗尽后所有模型（flash 也一样）返回 429 `insufficient_quota`。

### 根因②：GLM 5.x 推理模型在流式下烧光 max_tokens
- 前端 AI ask 走的是 **`/api/chat/stream`（SSE 流式）**，与非流式 `/api/chat` 是两条代码路径
- GLM 5.x 系是推理模型：流式输出先发 `delta.reasoning_content`（思考），再发 `delta.content`（正文），**思考计入 max_tokens**
- 用 Jemma 原题实测：`reasoning_tokens=2998/3000`，正文 0 字。简单问题（如"你好"）思考短、有正文残留，造成"看起来修好了"的假象
- 关闭思考（`thinking: {"type":"disabled"}`）后同一请求：正文 2011 字正常输出

## 修复内容（api_server.py）

1. **`_llm_extra_params(base_url)`**：对 bigmodel / z.ai 域名自动注入 `thinking: {"type":"disabled"}`，三个调用点（非流式 chat / 流式 stream / 兜底）统一生效。按域名注入是为了不影响未来切回 qwen 等其他供应商
2. **流式空回复降级兜底**：`chat_stream` 的 `generate()` 里，若流结束 `full_reply` 为空，自动改走一次非流式请求并补发 token —— 任何供应商流式格式再出幺蛾子，孩子也不会看到空白
3. **`CHAT_MAX_TOKENS` 3000 → 8192**（env 可覆盖）：GLM 端点实测接受 16384；3000 在思考未被关闭的极端情况下会被 reasoning 耗尽
4. **数据清理**：删除 `chat_jemma.json` 中 6 条空 assistant 记录及重复提问（备份 `data/chat_jemma.json.bak-empty-20261103`）；Diya 无此问题
5. OCR 链：`OCR_MODELS=glm-4.5v`（`/etc/plan-api.env`）。实测 glm-4.6/4.6v 返回空 content、4.7-flash 超时，**只有 4.5v 可用**

## 验证矩阵

| 项目 | 结果 |
|---|---|
| `/api/chat` 非流式（diya/jemma） | PASS |
| `/api/chat/stream` 流式（diya/jemma，复杂推理题） | PASS |
| Jemma 角斗士原题流式重放 | PASS（2049 字，510 个 SSE 事件，正确入库） |
| `/api/ocr` 图片识别 | PASS（glm-4.5v 正确转写） |
| Socratic 模式：代写作业请求 → 拒绝+引导 | PASS |
| Socratic 模式：概念讲解 → 直接回答 | PASS |

## 经验教训

1. **验证必须打前端真实调用的端点**（看 nginx 日志 `/www/wwwlogs/grow-consultant.log` 确认 UA 与路径），本次只测非流式造成漏网
2. **SSE 响应 200 但 ~22 字节 = 只有 done 事件 = 空回复**，是这类故障的指纹
3. 推理模型（GLM 5.x / 各家 reasoning 系）做对话产品必须处理思考 token：要么关思考，要么解析 `reasoning_content`，要么给足 max_tokens
4. 订阅制 API（token-plan / coding plan）的"配额耗尽"表现为 429，别当成限流重试

## 运维备忘

- LLM 配置优先级：`data/llm_config.json`（UI 保存，最高）> 进程 env > `/var/lib/plan-api/config.yaml`（legacy 兜底）。改 llm_config.json **无需重启**（每次请求现读）；改 `OCR_MODELS` env **需重启** plan-api（import 时读取）
- GLM key 位置：`/root/.hermes/.env` 的 `GLM_API_KEY`（与 Hermes agent 共用同一订阅池，注意用量）
- qwen token-plan 月配额 11-14 重置后如需切回双供应商分流，恢复备份即可
