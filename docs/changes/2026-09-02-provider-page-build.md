+++
id = "2026-09-02-provider-page-build"
type = "build"
release_bump = "none"
status = "verified"
+++

# 更新供应商页静态资源

## 目标

让发布目录引用本次供应商页和消息提示改动对应的最新 Vite 资源。

## 现状

重新构建后资源内容哈希变化，发布目录需要同步替换旧资源引用。

## 设计范围

- 仅更新 `proxy_static/dist` 的构建产物及入口引用。

## 非目标

不修改源代码、接口、配置或运行端口。

## 兼容性

静态资源内容与已验证源代码一致，无接口影响；版本号不变。

## 风险

资源引用不一致会导致页面加载旧文件；通过构建和完整门禁验证规避。

## 测试计划

运行 Vite 构建、完整 pre-push 门禁和 `git diff --check`。

## 实际改动

- 替换 `proxy_static/dist/static/assets` 的 JS 哈希文件。
- 更新 `proxy_static/dist/index.html` 的脚本引用。

## 验证结果

- `npm run build --prefix proxy_static`：通过。
- `'' | & .venv\\Scripts\\python.exe scripts/team_policy.py pre-push`：通过，Python 单测 521/521、全部 JavaScript 测试通过、构建和语法检查通过。
- `git diff --check`：通过。

## PR

pending
