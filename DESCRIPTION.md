# nanobot 项目架构说明

## 1. 项目定位
`nanobot` 是一个轻量级个人 AI 助手框架，核心思路是：
- 用统一的 `AgentLoop` 处理推理和工具调用
- 用异步消息总线解耦“渠道接入”和“智能体核心”
- 用可配置 Provider（基于 LiteLLM）支持多模型
- 用工作区文件（`AGENTS.md`/`SOUL.md`/`USER.md`/`memory/`）驱动可定制行为

核心技术栈：
- Python 3.11+
- Typer（CLI）
- LiteLLM（多模型统一调用）
- asyncio（并发与消息处理）
- 可选 Node.js bridge（WhatsApp）

## 2. 顶层目录结构

```text
.
├── nanobot/                 # Python 主体代码
│   ├── agent/               # Agent 核心：上下文、循环、工具、子代理
│   ├── bus/                 # 消息事件与队列
│   ├── channels/            # Telegram/Discord/WhatsApp/Feishu 适配
│   ├── cli/                 # CLI 命令入口
│   ├── config/              # 配置模型与加载逻辑
│   ├── cron/                # 定时任务服务
│   ├── heartbeat/           # 心跳任务服务
│   ├── providers/           # LLM 与转录 Provider
│   ├── session/             # 会话持久化
│   └── skills/              # 内置技能文档
├── bridge/                  # Node.js WhatsApp bridge（Baileys + WebSocket）
├── tests/                   # 基础测试（当前以工具参数校验为主）
├── workspace/               # 示例工作区模板
├── Dockerfile               # 容器化部署（包含 Node 20）
└── pyproject.toml           # 包定义与依赖
```

## 3. 分层架构

### 3.1 接入层（CLI/Channels）
- CLI 入口：`nanobot/cli/commands.py`
  - `nanobot agent`：直接会话（单次或交互）
  - `nanobot gateway`：启动完整网关（Agent + Channels + Cron + Heartbeat）
  - `nanobot channels ...`：渠道状态与 WhatsApp bridge 登录
  - `nanobot cron ...`：任务增删改查与手动触发
- 渠道适配：`nanobot/channels/*.py`
  - 统一基类 `BaseChannel`
  - 各渠道将外部消息转为 `InboundMessage` 入总线
  - 出站消息由 `ChannelManager` 分发为平台 API 调用

### 3.2 消息与调度层（Bus/Manager）
- `MessageBus`（`nanobot/bus/queue.py`）
  - `inbound` 队列：渠道 -> Agent
  - `outbound` 队列：Agent -> 渠道
- `ChannelManager`（`nanobot/channels/manager.py`）
  - 初始化启用的渠道
  - 启动渠道监听
  - 统一消费 `outbound` 并路由到目标渠道

### 3.3 核心智能体层（Agent）
- `AgentLoop`（`nanobot/agent/loop.py`）是核心控制器：
  1. 消费入站消息
  2. 读取会话历史
  3. 构造上下文（系统提示词 + 历史 + 当前输入）
  4. 调用 LLM
  5. 执行工具调用（可多轮迭代）
  6. 写回会话并发布出站消息
- `ContextBuilder`（`nanobot/agent/context.py`）
  - 聚合身份信息、工作区引导文件、记忆、技能摘要
  - 支持图像输入转 base64 多模态内容
- `SubagentManager`（`nanobot/agent/subagent.py`）
  - 提供后台子代理执行复杂任务
  - 通过系统消息把结果“汇报”给主代理

### 3.4 能力层（Tools）
工具统一实现 `Tool` 抽象类并在 `ToolRegistry` 注册。
默认工具集（`AgentLoop._register_default_tools`）：
- 文件：`read_file` / `write_file` / `edit_file` / `list_dir`
- 执行：`exec`（带基础危险命令拦截与可选工作区限制）
- 网络：`web_search`（Brave API）/ `web_fetch`
- 通知：`message`（向渠道发送消息）
- 并发：`spawn`（启动子代理）
- 调度：`cron`（在网关中启用）

### 3.5 基础服务层
- 配置：`nanobot/config/schema.py` + `loader.py`
  - Pydantic 配置模型
  - 支持 camelCase<->snake_case 转换和配置迁移
- Provider：`nanobot/providers/litellm_provider.py`
  - 统一多厂商模型调用
  - 根据模型/网关自动补前缀和 API 环境变量
- 会话：`nanobot/session/manager.py`
  - 按 `channel:chat_id` 维度持久化 JSONL
- 定时：`nanobot/cron/service.py`
  - 支持 `at/every/cron` 三种计划
  - 持久化至 `~/.nanobot/cron/jobs.json`
- 心跳：`nanobot/heartbeat/service.py`
  - 周期读取 `HEARTBEAT.md` 决定是否触发 Agent

## 4. 关键运行流程

### 4.1 网关模式（推荐）
`nanobot gateway` 启动后：
1. 读取配置并初始化 Provider/Bus/Session
2. 启动 `CronService` 与 `HeartbeatService`
3. 启动 `AgentLoop.run()`（消费 inbound）
4. 启动各渠道监听
5. 渠道收到消息 -> `InboundMessage`
6. Agent 处理并发布 `OutboundMessage`
7. `ChannelManager` 将响应发送回原渠道

### 4.2 直连 CLI 模式
`nanobot agent`：
- 不启动渠道和网关
- 直接调用 `AgentLoop.process_direct()`
- 适合本地调试和单人使用

### 4.3 子代理回传流程
1. 主代理通过 `spawn` 工具下发任务
2. `SubagentManager` 异步运行独立推理循环
3. 完成后注入 `channel=system` 消息回总线
4. 主代理将后台结果整理成自然语言回复用户

## 5. 多渠道适配说明
- Telegram：基于 `python-telegram-bot`，支持文本/图片/语音/文件，语音可走 Groq 转录。
- Discord：基于 Gateway WebSocket + REST API，支持附件下载和 typing 指示。
- Feishu：基于 `lark-oapi` 长连接（WebSocket），发送端使用交互卡片。
- WhatsApp：Python 侧连接 Node bridge；Node 侧用 `@whiskeysockets/baileys` 处理 WhatsApp Web 协议。

## 6. 配置与数据落盘
- 配置文件：`~/.nanobot/config.json`
- 工作区：默认 `~/.nanobot/workspace`
  - 引导文件：`AGENTS.md`、`SOUL.md`、`USER.md`
  - 记忆：`memory/MEMORY.md` 与 `memory/YYYY-MM-DD.md`
- 会话：`~/.nanobot/sessions/*.jsonl`
- 定时任务：`~/.nanobot/cron/jobs.json`
- 媒体缓存：`~/.nanobot/media/`

## 7. 扩展点
- 新渠道：实现 `BaseChannel` 并在 `ChannelManager` 注册。
- 新工具：实现 `Tool` 并在 `AgentLoop` 注册。
- 新模型后端：实现 `LLMProvider` 或扩展 LiteLLM 映射规则。
- 新技能：在 `workspace/skills/<name>/SKILL.md` 或 `nanobot/skills/` 增加技能文档。

## 8. 当前实现特点与注意点
- 架构偏“最小可运行核心”，模块边界清晰、便于二次开发。
- 安全防护是基础级（命令 denylist、可选工作区限制），高安全场景建议加强沙箱和审计。
- 测试覆盖目前较轻，主要集中在工具参数校验，建议补充端到端与渠道集成测试。
- `pyproject.toml` 中版本为 `0.1.3.post5`，`nanobot/__init__.py` 中为 `0.1.0`，存在版本标识不一致现象，建议统一。

## 9. 一句话总结
`nanobot` 采用“渠道接入 + 消息总线 + Agent循环 + 工具系统 + 调度服务”的轻量分层设计，代码体量小、可读性高，适合作为个人 AI 助手和研究型 Agent 框架的基础骨架。
