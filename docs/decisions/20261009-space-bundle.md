# 决策：生成可直接推送的 HF Space 部署包

## 背景

现有 Gradio demo 已决定部署到免费的 Hugging Face Space，但部署时还要人工把
`demo/gradio_app.py` 和依赖文件复制、改名到 Space 根目录。这个步骤容易误用只有 Gradio
的 `demo/requirements.txt`，也容易漏掉项目源码或覆盖掉 Space README 的 YAML
frontmatter。本次只改部署组装方式，不改变 Gradio 壳和 agent 主线。本决策取代
`docs/decisions/20261008-gradio-demo-shell.md` 里“部署时手工把两个 demo 文件改名放到根目录”
的做法，免费的 Gradio 部署路线本身不变。

## 候选方案

1. 写 Python 标准库脚本生成独立部署目录：能在复制前检查文件、重复运行时清理旧目录，
   代价是仓库里多维护一个短脚本。
2. 写 shell 脚本调用 `cp`、`find` 和 `rm`：代码可能更短，但排除规则依赖命令行差异，
   错误处理和跨环境行为不如 Python 直白。
3. 继续在部署说明里列手工改名步骤：不用写代码，但无法避免漏文件和选错依赖。

## 选择

选择方案 1。脚本命名为 `demo/build_space.py`，使用 Python 标准库，输出固定为仓库根目录的
`space_bundle/`。每次运行先删除旧输出，再重新组装。

## 为什么

仓库本身是 Python 项目，标准库 `pathlib` 和 `shutil` 足以完成检查、清理和复制，不需要新增
依赖，也比 shell 的平台细节更容易读懂。`space_bundle/` 放在根目录，名字直接表达它是待推送
产物；把它加入 `.gitignore` 并在复制时排除自身，可避免递归复制和误提交生成物。

部署包保留 `docs/`、`tests/`、`eval/`、`web/` 等项目源码，只排除 `.venv`、`.git`、
`qdrant_data`、`__pycache__`、`.pytest_cache`、`.agents`、`.codex`、部署目录自身、散落的
`.pyc`，以及以 `:memory:` 开头的异常会话文件。任意层级的 `.env*` 可能包含 secret，任意层级
的 `*.db` 是运行时状态产物，例如记忆模块可能生成的 `audit.db`，两者也不进入部署包。
根目录的 `Dockerfile`、`.dockerignore` 和 `vercel.json` 分别属于已经放弃的付费 Docker 路线
和无关的 Vercel 配置，因此从部署包顶层排除，避免查看 Space 仓库的人误以为仍要维护这些
路线。HF 实际按 README frontmatter 的 `sdk` 字段选择构建方式，排除这些文件只是清理旧配置，
不是修复构建方式错误。根目录的 `:memory:.ses` 看起来是记忆存储把内存会话名误写成了文件，
没有项目源码价值，因此删除。`.gitignore` 使用 `:memory:*`，只挡住这个不正常前缀；没有使用
`*.ses` 或 `*memory*`，避免误伤合法会话文件或名称中正常包含 memory 的代码和文档。

脚本把 `demo/gradio_app.py` 复制为根目录 `app.py`，并明确使用仓库根
`requirements.txt`；后者包含 Gradio 和 agent 的完整运行依赖。部署 README 由脚本写入，
frontmatter 固定包含 `title`、`sdk: gradio`、`sdk_version` 和 `app_file: app.py`。
`sdk_version` 在打包时从 `demo/requirements.txt` 的 `gradio==X.Y.Z` 解析，格式不符就明确失败，
避免依赖 pin 和 README 各维护一份版本号后发生漂移。正文选择简短说明 demo 与诚实边界，不复用
较长的项目 README；这样 Space 首页信息够用，也不会让部署元数据在复制项目 README 时丢失。
代价是项目 README 更新后，部署正文不会自动同步，但这段文字刻意只保留稳定信息。

## 对 eval 的影响

不改变 `trippilot/`、`eval/`、`fixtures/`、`tests/` 或任何 agent 行为，现有 trajectory
评测指标不变。本次回归检查限定为三项手工检查：脚本能重复生成部署包；部署包顶层文件与排除
项符合预期，且 README 版本号与依赖 pin 一致；`demo/build_space.py` 能通过 Python 编译检查，
之后再做一次实际启动验证。编译检查不针对部署包执行，避免在里面留下 `__pycache__`。
打包脚本是部署组装脚本，不进入主线测试链，因此有意不配自动化
测试；以上手工检查和实际启动就是它的回归方式，不是遗漏。
