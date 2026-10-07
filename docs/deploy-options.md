# Demo 部署双方案

这两条路线都不需改 TripPilot 的 Agent 主线。区别在于：前端和 FastAPI 放在一起，还是把前端单独放到 Vercel。

## 方案 A：Hugging Face Spaces Docker 一体部署

仓库里的 `Dockerfile` 可以直接作为 Docker Space 的构建入口：

- 基础镜像是 Python 3.11，与 `pyproject.toml` 里的 `requires-python = ">=3.11"` 一致。
- `pip install --no-cache-dir .` 安装的都是 PyPI 可解析的常规依赖。`sentence-transformers` 会间接带入 PyTorch，所以镜像不会小，Spaces 首次构建也会比纯 FastAPI 项目慢。
- 容器暴露 7860 端口，Uvicorn 也监听 `0.0.0.0:7860`，与 Docker Spaces 的默认端口对齐。
- FastAPI 把 `web/` 挂在根路径，打开 Space 就是控制台。`/health` 在静态挂载之前定义，健康检查不会被首页抢走。
- 不配置 `TRIPPILOT_LLM_*` 时，`make_llm()` 会从 `EnvLLMClient` 回退到 `DeterministicStub`。地图和天气默认读取 `fixtures/recorded/`，因此基础 demo 不需要 secret。
- `.dockerignore` 已排除 `.git`、`.venv`、`__pycache__` 等本地产物，不会把它们一起塞进构建上下文。

有两个代价需要提前讲清。

BGE 模型 `BAAI/bge-small-zh-v1.5` 在偏好记忆代码首次需要它时才由 `SentenceTransformer` 加载，不会在构建镜像时下载。容器可以先启动，但第一次触发偏好记忆时会现场下载约 100 MB，这次请求会慢。使用 `DeterministicStub` 的核心对话路径不依赖真实 LLM key。

Qdrant 使用容器内的本地文件模式。Space 重建后数据会丢失，所以演示里的“记住偏好”只能当作当前会话内的效果。如果要跨重建保留，需要另外配置持久化存储；具体操作见 `docs/spaces-deploy.md`。

## 方案 B：Vercel 纯静态前端

`web/` 可以作为静态页部署到 Vercel，再把 API base URL 配成 Spaces 后端地址。`vercel.json` 由任务 A 另行创建，本文只记两端怎么配合。

这不是把整个 TripPilot 搬到 Vercel。当前项目没有做 serverless 改造，Vercel 不能运行这个常驻 FastAPI 服务；后端仍然必须放在 Spaces 或其他能运行容器的宿主上。选 Vercel 只是换了前端打开的地方。

`web/index.html` 的 API 地址已支持通过 `?api=https://<space>.hf.space` 配置（不带参数则走同源），仓库根目录的 `vercel.json` 把 `web/` 作为静态输出。但这只解决了客户端这一半：浏览器跨域请求还需要 Spaces 后端允许 Vercel 页面的 origin，而当前 FastAPI 没有配置 CORS。由于本单冻结了 `trippilot/`，CORS 是选 Vercel 后必须另行完成和回归的部署项，不能把它当作已打通。

## 诚实对比

| 维度 | Spaces Docker 一体部署 | Vercel 静态 + 独立后端 |
| --- | --- | --- |
| 费用 | 最小 demo 可从免费硬件起步；持久存储或更高配置可能产生费用。 | 静态前端可从免费方案起步，但后端宿主仍要单独计算；不会因为上了 Vercel 就省掉 Spaces。平台额度会调整，执行前需再核对。 |
| 冷启动 / 延迟 | 一个站点、一次请求；Space 休眠后仍有唤醒延迟，BGE 首次下载还会让记忆请求变慢。 | 静态页本身打开较快，但 API 仍要等后端唤醒，并多一段跨域网络。 |
| 运维负担 | 一个 Space 、一套发布，故障面最小。 | 前后端两个部署，要同时管 API 地址、跨域、版本和两边的状态。 |
| 国内访问 | 速度一般，是否能用要拿真实网络实测，不先下结论。 | 同样不能保证国内访问快；而且页面打开后还要访问境外后端。 |
| 演示可靠性 | 同源请求，链路短，更适合面试时打开就演示。仍需在发布前做冷启动实测。 | 前端打开与后端健康是两件事；任一端配置或网络异常都会让 demo 只剩页面壳。 |

## 怎么选

建议先用 Spaces 做一体部署，把发布链路压到最短；如果实测后确定需要单独的静态入口，再加 Vercel。这是推荐顺序，不是替用户拍板。

真正执行部署需要用户自己的 Hugging Face / Vercel 账号，不在本单范围内。最终走哪条路，等用户选定后再操作。
