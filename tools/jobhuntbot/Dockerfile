# JobHuntBot 看板容器（零依赖 Node 静态服务器）
# 数据与代码通过 compose volume 挂载宿主机 tools/jobhuntbot/dashboard，
# 本镜像仅提供 node 运行环境；未挂卷时以镜像内副本启动。
FROM node:20-alpine
WORKDIR /app
COPY dashboard/ ./dashboard/
WORKDIR /app/dashboard
EXPOSE 8420
CMD ["node", "server.js"]
