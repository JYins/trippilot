# 决策：修正 Hugging Face Spaces 部署说明

## 背景

审计发现 `docs/spaces-deploy.md` 仍把 Docker Space 写成当前部署方式，还要求保留根目录
`Dockerfile` 并用 `docker build`、`docker run` 做发布检查。这与已经生效的两项决定冲突：
`20261008-deploy-gradio-free.md` 因 Docker Space 收费而选择免费 Gradio Space，
`20261009-space-bundle.md` 则要求用 `demo/build_space.py` 生成 `space_bundle/`，并刻意排除旧
Docker 配置。由于部署包会原样包含 `docs/`，旧说明也会进入 Space 仓库并继续误导维护者。

## 候选方案

1. 整篇重写为 Gradio 部署指引：现行步骤能独立照做，代价是以后部署流程变化时要同步维护本文和 `demo/README.md`。
2. 只在文首加“已过时”提示并保留 Docker 原文：改动最小，代价是过期命令仍在部署包中，读者可能跳过提示后误用。
3. 删除本文，只链接到 `demo/README.md`：没有重复说明，代价是从 `docs/` 查部署方法时必须跨目录寻找上下文。

## 选择

选择方案 1：把本文改成简短的 Gradio Space 工作说明，并以 `demo/README.md` 的步骤为准对齐
SDK、免费 CPU basic、部署包生成和推送方式。

## 为什么

这份文件会随部署包公开，单独打开时也必须准确。完整重写能移除仍可复制执行的过期 Docker 命令，
同时保留创建 Space、发布前检查和诚实边界这些实际需要的信息。代价是与 `demo/README.md` 有少量
重复；这里接受重复，并把内容压短，降低以后同步维护的成本。`docs/deploy-options.md` 是当时的双方案
历史对比，本次不改，避免把修正文档扩成清理全部历史材料。

## 对 eval 的影响

本次只修改部署文档并新增本决策记录，不改 `trippilot/`、`eval/`、`fixtures/`、`tests/`，不改变
agent 行为、评测维度或指标。回归只检查文档不再把 Docker 写成当前路线，且工作区只出现这两个
指定文件的变更。
