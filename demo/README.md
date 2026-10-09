# TripPilot Gradio demo 部署说明

## 部署到 Hugging Face Spaces

1. 在仓库根目录运行打包脚本：

   ```bash
   .venv/bin/python demo/build_space.py
   ```

   脚本会校验 `demo/requirements.txt` 中固定的 Gradio 版本，清理旧的
   `space_bundle/`，再生成可直接部署的完整目录；secret、运行状态和旧部署配置不会复制进去。
2. 登录 [huggingface.co](https://huggingface.co/)，新建 Gradio Space，硬件选择免费的
   **CPU basic**。
3. 把 `space_bundle/` 里的全部内容推送到 Space 仓库。根目录已经包含 `app.py`、完整的
   `requirements.txt` 和带 Space 配置的 `README.md`，不需要再手工复制或改名。
4. 推送后 Space 会自动构建并运行；构建完成后打开 Space 链接即可体验。

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
