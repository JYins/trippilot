# 决策：demo 部署双方案

## 背景

在手写最小控制台和 Spaces Docker 方向定下后，还需要回答一个具体问题：Vercel 能否让 demo 更好用。约束没变：FastAPI 后端需要常驻进程，前端不引入新框架，无 key 时仍必须能用 `DeterministicStub` 和录制数据跑通。

## 候选方案

1. Hugging Face Spaces Docker 一体部署：FastAPI、静态页和录制数据放在一个容器，代价是国内访问和休眠唤醒需要实测，镜像构建也受 PyTorch 体积影响。
2. Vercel 静态前端 + Spaces 后端：前端有独立入口，代价是 FastAPI 并不会因此搬到 Vercel，还要维护 API 地址、后端 CORS 和两套部署。

## 选择

推荐 Spaces 一体部署为首选，Vercel 纯静态前端为备选，最终由用户拍板。

## 为什么

Spaces 能直接使用现有 `Dockerfile`，7860 端口、FastAPI 静态挂载和无 key 回退已经对齐。前后端同源，演示时少一层配置和故障点，更符合“点开就能玩”的目标。

Vercel 的好处是静态前端可以单独发布，页面入口与后端解耦。但我们放弃把它当作默认路线：前端打开快不代表 API 已唤醒，FastAPI 仍需另外的宿主；双部署还会带来额外的版本和跨域维护成本。Spaces 国内访问速度一般，Vercel 也不保证快，两边都要在真实网络上实测，不用想象替代结果。

## 对 eval 的影响

延续 2026-10-07 决策里的“demo 可跑性”回归维度：`DeterministicStub` 下的完整对话和 trajectory 必须从部署入口跑通。若选 Spaces，发布前检查同源首页、`/turn` 和 `/health`；若加 Vercel，再加一条静态页跨域请求 Spaces API 的发布检查。这些不改现有轨迹断言，只扩展发布前的可跑性回归。
