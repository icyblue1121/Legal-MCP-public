# Spike: Langfuse 免登录内嵌（2026-06-05）

**分支：** `spike/langfuse-passwordless-embed`
**问题：** 能否让管理员从 Audit 页进入内嵌 Langfuse 而**不出现 Langfuse 登录页**？
**结论：✅ 鉴权机制可行**（同源反向代理 + 服务端注入会话 Cookie）。基路径路由是已知约束，二选一。

---

## 验证环境

- Langfuse v3 自托管已在 `127.0.0.1:3000` 运行（compose: `docker-compose.langfuse.yml`）。
- 运行容器有效凭据（`docker exec langfuse-web printenv`）：
  - `LANGFUSE_INIT_USER_EMAIL=admin@legal-mcp.local`
  - `LANGFUSE_INIT_USER_PASSWORD=change-me-admin-password`
  - `NEXTAUTH_SECRET=…`（已设）、`NEXTAUTH_URL=http://localhost:3000`
  - 项目 `legal-mcp-project`（`hasTraces:true`）。

## 实测结果

1. **程序化 NextAuth credentials 登录成立**：
   - `GET /api/auth/csrf` → 拿 `csrfToken` + csrf cookie。
   - `POST /api/auth/callback/credentials`（form: `csrfToken,email,password,json=true`）→ `Set-Cookie: next-auth.session-token=…`（JWE，`dir`+`A256GCM`，Path=/，HttpOnly，SameSite=Lax，约 30 天）。
2. **会话 Cookie 即鉴权门**：
   - `GET /api/auth/session` **带** cookie → 返回已认证用户 + org/project。
   - `GET /api/auth/session` **不带** cookie → 返回 `{}`（前端据此跳登录页）。
3. **按需重登可刷新会话**：重新走 csrf+credentials 即得新会话 → 过期可自动重取。
4. **Langfuse 是客户端渲染（Next.js）**：所有路径（`/`、`/project/<id>/traces`、`/project/<id>/sessions/<sid>`）裸 HTTP 都回 200 + `__NEXT_DATA__`；鉴权由浏览器调 `/api/auth/session` 在客户端判定，不是服务端重定向。

## 含义（确定可行的方案）

- **同源反向代理**把 `/admin/observability/*` 反代到 Langfuse；浏览器只跟管理后台同源通信。代理在"代理→Langfuse"每跳注入 `next-auth.session-token`（含浏览器发起的 `/api/auth/session`、tRPC 等 XHR），于是内嵌 UI 的 session 检查通过、**登录页不出现**。
- 会话由管理服务器用 init 凭据预登拿到、缓存、过期重登。入口本身受管理后台鉴权保护。
- 跨域 Cookie 限制不构成问题——cookie 在上游那跳注入，浏览器无需持有。

## 唯一剩余约束：基路径路由（非鉴权问题，二选一）

Langfuse 资源/接口路径是绝对的（`/api/auth/session`、`/_next/…`）。要挂在 `/admin/observability` 子路径下：

- **(A) 重建镜像**：`NEXT_PUBLIC_BASE_PATH=/admin/observability` + `NEXTAUTH_URL=…/admin/observability/api/auth`，干净挂子路径。代价：维护一个自构建镜像（预构建镜像不支持任意子路径，base path 内联进静态资源——[官方文档](https://langfuse.com/self-hosting/configuration/custom-base-path)）。
- **(B) 子监听器代理根路径**：管理侧用独立端口把 Langfuse 代理在其根路径（无需 base path，沿用预构建镜像），仍服务端注入会话；入口从 Audit 链过去。Cookie 按主机（`127.0.0.1`）非端口隔离，管理会话同主机可达便于复用鉴权。代价：多一个监听器、URL 不在 `/admin/` 下。

**建议：** 若团队愿维护自构建镜像 → (A) 体验最干净；否则 → (B) 改动最小、不碰 Langfuse 镜像。

## 兜底

会话注入若在某版本不稳：退 iframe 直链 + 文档说明需先登录一次；或后续用 Langfuse public API（Basic auth + 已有 key）自绘视图。

## 复现命令（要点）

```sh
BASE=http://127.0.0.1:3000; JAR=/tmp/lf.txt; rm -f $JAR
CSRF=$(curl -s -c $JAR $BASE/api/auth/csrf | python3 -c "import sys,json;print(json.load(sys.stdin)['csrfToken'])")
curl -s -i -b $JAR -c $JAR -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "csrfToken=$CSRF" --data-urlencode "email=admin@legal-mcp.local" \
  --data-urlencode "password=change-me-admin-password" --data-urlencode "json=true" \
  $BASE/api/auth/callback/credentials | grep -i set-cookie
curl -s -b $JAR $BASE/api/auth/session   # 带 cookie → 有 user；不带 → {}
```
