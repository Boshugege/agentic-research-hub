# 外部配置步骤

## Cloudflare Tunnel

1. 在 Cloudflare Zero Trust 的 Tunnels 页面打开当前 tunnel 的 Public Hostnames。
2. 新增三个 hostname，分别指向 `http://127.0.0.1:18081`、`http://127.0.0.1:18082`、`http://127.0.0.1:18083`。
3. 给 notes 和 git 应用配置邮箱或 GitHub OAuth 登录，给 mcp 应用配置 Service Auth。
4. 将服务端的 `AGENT_API_KEY` 与 Cloudflare service token 分别放在调用方环境变量和 Access 客户端配置中。两个值不要写入仓库。

## Forgejo PR 与 GitHub 私有镜像

1. 在 Forgejo Web UI 完成首次管理员设置，创建私有仓库 `research-vault`。
2. 创建一个只允许仓库读写的 API token，把 token、仓库 owner 和仓库地址写入 personal 的 EnvironmentFile。
3. 为 `%h/research-vault` 设置 Forgejo remote，确保 API 创建的 `agent/<project>/<timestamp>` 分支可被 Forgejo 看到。
4. 在 Forgejo 仓库设置中添加 GitHub 私有仓库的 Push Mirror。镜像凭据保存在 Forgejo 的 secret store，不写入本项目。
