# 部署到 Hugging Face Spaces

TripPilot 使用 Docker Space 提供 FastAPI 接口和 `web/` 控制台。默认链路不需要任何 secret 或 API key：没有配置 `TRIPPILOT_LLM_*` 时，服务自动使用 `DeterministicStub`。

## 创建 Space

1. 登录 Hugging Face，选择 **New Space**。
2. 填写 Space 名称，SDK 选择 **Docker**，可见性按需要选择。
3. 创建后，将本仓库内容提交到 Space 的 Git 仓库。仓库根目录必须保留 `Dockerfile`、`pyproject.toml`、`trippilot/`、`fixtures/` 和 `web/`。
4. 等待镜像构建完成。容器会在 `0.0.0.0:7860` 启动 Uvicorn，Space 页面会直接打开体验控制台。

不需要在 Space Settings 中添加 secret。地图和天气工具读取仓库内的录制数据，不代表实时数据；座舱状态由页面注入，为纯软件模拟。

## 数据位置

偏好记忆使用 Qdrant 本地文件模式，默认写入容器用户目录下的 `.trippilot/memory`，不连接外部 Qdrant 服务。普通 Space 的容器文件会随重建而丢失；如需跨重建保留偏好，应先为 Space 配置持久化存储，再单独调整数据目录。本最小体验不依赖持久化偏好也能运行。

## 发布前检查

在本地仓库执行：

```bash
.venv/bin/pytest -q
docker build -t trippilot-demo .
docker run --rm -p 7860:7860 trippilot-demo
```

打开 `http://localhost:7860/`，再检查 `http://localhost:7860/health` 返回 `status: ok`。确认页面常驻显示“座舱状态·模拟 / 地图天气·录制数据 / 无需真实车辆”。
