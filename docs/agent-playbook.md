# 记笔记 Agent 维护手册

面向知识库的主人：怎样让每个项目里的 AI Agent 持续写出能串成思路线条的笔记，而不是流水账。

## 1. 每个项目配置一次

**一个项目一个身份。** 给每个项目的 Agent 单独签发密钥，并限定只能写自己的项目：

```sh
scripts/agent-key.sh add footstep-viz --projects footstep-viz
```

密钥只显示一次。之后这个 Agent 的所有写入都会被服务端记为 `agent:footstep-viz`，它自己填的作者名不起作用；写别的项目会被拒绝，读不受限。停用某个 Agent 用 `scripts/agent-key.sh revoke <name>`，不影响其他 Agent。

**把 MCP 配置放在项目里，密钥放在环境变量里。** 例如项目根目录的 `.mcp.json`：

```json
{
  "mcpServers": {
    "research-hub": {
      "type": "http",
      "url": "https://mcp.<domain>/mcp",
      "headers": { "Authorization": "Bearer ${RESEARCH_HUB_KEY}" }
    }
  }
}
```

**把常驻指令写进项目的 `CLAUDE.md` / `AGENTS.md`。** 只在全局配了 MCP 而项目里没有指令时，Agent 记不记、怎么记全看当次对话怎么提。把下面这段贴进去，改掉项目名：

```markdown
## 研究笔记（Research Hub）

本项目的研究记录写入 Research Hub，项目 slug 为 `footstep-viz`。

**开工时**：调用 `get_project_context` 读总文档和近期笔记，调用 `list_threads` 看现有线索。

**发生以下事件时，立刻用 `append_timeline_note` 记一条**（一条笔记只记一件事）：
- 做了决定：`kind=decision`，写清选了什么、放弃了什么、为什么。
- 得到结果：`kind=result`，写清数字、对应的提交号或文件路径、复现命令。
- 走不通或放弃：`kind=negative`，写清试了什么、卡在哪、为什么不再继续。
- 提出假设或方案：`kind=idea`；定下接下来要试什么：`kind=plan`；讨论记录：`kind=meeting`。

**每条笔记都要**：
- 标题直接写结论，不写"实验记录""进展更新"这类标题。
- 填 `thread`。延续已有工作时沿用 `list_threads` 里的线索名，不要另起近义的新名字。
- 用 `follows` 指向它承接的上一条笔记；推翻了之前的结论时用 `supersedes` 指向被推翻的那条。
- 补记过去的事要填 `event_date`。
- 原始日志不要贴进正文，用 `ingest_raw_artifact` 存，正文里只写结论和路径。

**不要改写旧笔记。** 结论变了就新写一条并 `supersedes` 旧的。

**更新总文档**（`update_living_doc`）的时机：结论变了、做了决定，或 `list_undistilled_notes` 积压超过 5 条。
- 只改相关章节：传 `section` 和新的章节内容，并带上 `get_project_context` 返回的 `living_commit` 作为 `base_commit`。
- `source_notes` 列出这次并入的笔记路径，正文里用 `[[路径|标签]]` 引用证据。
- 被拒绝并提示文档已变更时，重新读取后再合并，不要强行覆盖。

**收工时**：确认当天的决定和结果都已记录；有积压就更新总文档。
```

## 2. 什么样的笔记算好

| | 差 | 好 |
| :--- | :--- | :--- |
| 标题 | 11-20 实验记录 | BEM 求解器在 2 kHz 以上发散，改用自适应网格 |
| 粒度 | 一天的事写成一条 | 一个决定、一个结果各一条 |
| 证据 | "效果提升明显" | "SNR 从 12 dB 到 16 dB，提交 `a1b2c3d`，`python eval.py --set B`" |
| 负结果 | 不记 | 单独一条 `negative`，写清为什么放弃 |
| 关系 | 靠时间先后猜 | `thread` + `follows` / `supersedes` |

线索按"在回答哪个问题"命名（`bem-convergence`），不要按日期或人命名。一个项目同时活跃的线索保持在个位数；太多说明拆得过细，或者同一件事起了几个名字。

## 3. 你自己每周做什么

打开首页大约十分钟：

1. **看"近 7 天"摘要**：每条决策和负结果你都应该认得。有不认得的，点进去看，这是发现 Agent 自行拐弯的最快办法。
2. **看"待并入总文档"**：某个项目积压多了，就让它的 Agent 做一次蒸馏；积压长期不降，说明指令里更新总文档的条件没被执行。
3. **看线索列表**：合并近义的线索名（直接在网页上编辑笔记的 `thread` 字段）。
4. **看总文档的 Commit 演进**：大改动的 diff 过一眼。想在合并前审阅的大改，让 Agent 用 `mode=pr`。

## 4. 出问题时

- **Agent 不记笔记**：先确认项目里有上面那段指令，再确认它调用的是带自己密钥的 MCP 服务。
- **作者显示 `agent:hub-api` 或自报的名字**：这个 Agent 还在用共享的 `AGENT_API_KEY`，给它换成独立密钥。全部迁完后可以把 `AGENT_API_KEY` 换成只有你自己知道的值。
- **总文档更新被拒（409）**：这是并发保护在起作用，让 Agent 重新读取后再合并。
- **写入被拒（403）**：Agent 在写别的项目。确实需要的话，重新签发密钥并在 `--projects` 里加上那个项目。
