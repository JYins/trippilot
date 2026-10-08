# TripPilot Gradio demo 部署说明

## 部署到 Hugging Face Spaces

1. 登录 [huggingface.co](https://huggingface.co/)，点击 **New Space**。SDK 选择
   **Gradio**，硬件选择免费的 **CPU basic**。
2. 把 `demo/gradio_app.py` 重命名为 `app.py`，放到 Space 仓库根目录；把
   `demo/requirements.txt` 重命名为 `requirements.txt`，也放到根目录。Space 的
   Gradio 构建流程从根目录寻找 `app.py` 和 `requirements.txt`，放在 `demo/`
   子目录不会被默认入口和依赖安装流程识别。其余 TripPilot 源码和录制数据保持原目录
   一起提交。
3. 执行 `git push` 后，Space 会自动构建并运行。构建完成后打开 Space 链接即可体验。

demo 默认使用 DeterministicStub，不需要 API key；地图和天气读取录制数据；座舱状态是
纯软件模拟，不连接真实车辆。默认演示链路没有外部调用，也不加载在线模型，免费档从休眠中
唤醒时需要做的工作较少；例外：首次触发偏好记忆时会懒加载 BGE embedding 模型
（可能需要联网下载一次）。

## 本地运行

本机若配置了代理，请先按本机环境清理无效代理变量，再执行：

```bash
.venv/bin/pip install -r demo/requirements.txt
.venv/bin/python demo/gradio_app.py
```
