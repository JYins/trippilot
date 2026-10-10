# 部署到 Hugging Face Gradio Space

当前部署方式是免费的 Hugging Face Gradio Space，硬件使用 **CPU basic**。部署内容不直接从
项目根目录挑文件，而是由 `demo/build_space.py` 生成到 `space_bundle/`；发布时把这个目录里的
全部内容推送到 Space 仓库。

早期评估过付费 Docker Space，但因为收费已经放弃。背景和取舍见
`decisions/20261008-deploy-gradio-free.md`。

## 创建和更新 Space

1. 在项目根目录生成部署包：

   ```bash
   .venv/bin/python demo/build_space.py
   ```

2. 登录 Hugging Face，新建 Space，SDK 选择 **Gradio**，硬件选择免费的 **CPU basic**。
3. 把 `space_bundle/` 里的全部内容推送到 Space 仓库。部署包根目录已经有 `app.py`、完整的
   `requirements.txt` 和带 Space 配置的 `README.md`，不用手工复制或改名。
4. 推送后等待 Space 自动构建并运行，完成后打开 Space 链接做一次演示检查。

不需要配置任何 secret 或 API key。默认 LLM 是 `DeterministicStub`；地图和天气读取录制数据，
不代表实时数据；座舱状态是纯软件模拟，不连接真实车辆。偏好记忆使用 Qdrant 本地文件模式，
Space 重建后数据可能丢失，但不影响最小演示链路。

## 发布前检查

在项目根目录执行：

```bash
.venv/bin/pytest -q
.venv/bin/python demo/build_space.py
```

确认 `space_bundle/` 已重新生成，并检查其中没有 `.env*`、`*.db`、根目录 `Dockerfile`、
`.dockerignore` 或 `vercel.json` 等排除项。然后本地启动 Gradio 做冒烟检查：

```bash
.venv/bin/python demo/gradio_app.py
```

打开终端提示的本地地址，确认页面可加载、预置场景能运行，并且页面明确显示地图天气是录制数据、
座舱状态是软件模拟。
