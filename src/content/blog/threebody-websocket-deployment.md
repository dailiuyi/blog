---
title: "Java 没有断，WebSocket 为什么一直重连"
description: "一次公网部署故障复盘：REST 正常、进程没重启，WebSocket 却持续 403，子路由刷新也跟着 404。"
published: 2026-08-21T18:40:00+08:00
category: Engineering
tags: [Java, WebSocket, Nginx, Vue, 部署]
featured: true
readingTime: "8 min"
series: "Three Body Lab"
seriesOrder: 2
---

Three Body Lab 部署到公网后，首页能打开，REST 接口也正常。可一点击开始实验，页面就一直显示“正在重连”。实验详情页第一次能进去，刷新又变成 Spring 的 Whitelabel 404。

一开始很容易怀疑 Java 服务扛不住实时推送，或者服务器资源不够。实际检查下来，Java 进程没有退出，systemd 的 `NRestarts` 是 0，也没有 OOM。实验接口持续返回成功，只有 NGINX 日志里的 WebSocket 握手一直是 403。

两个现象看起来都像“服务断了”，原因其实分开。

## 端口在代理时丢了

站点跑在公网 8721 端口。浏览器发起 WebSocket 时，`Origin` 是：

```text
http://39.108.101.149:8721
```

原来的 NGINX 配置使用：

```nginx
proxy_set_header Host $host;
```

`$host` 没把这个非标准端口继续传给后端。Spring 收到的 Host 不带 `:8721`，与浏览器 Origin 对不上，同源校验直接拒绝握手。前端只知道连接失败，于是按退避策略继续重连，看起来很像 Java 服务反复断开。

我用同一个实验和同一个 Origin 做了两次握手。Host 不带端口时返回 403，带上 `:8721` 后返回 101。对照结果出来后，就没必要改模拟算法、降低发布频率或扩容了。

修复时把 WebSocket 路径单独列出来，Host 改用保留端口的 `$http_host`，并补齐转发 Host、端口和协议头。关键配置是：

```nginx
location /ws/ {
    proxy_pass http://127.0.0.1:18721;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $threebody_connection_upgrade;
}
```

## 刷新 404 是另一条链路

前端使用 Vue History 路由。浏览器在应用内进入 `/experiments/{id}` 时，由前端路由接管，不会找后端。刷新以后，请求直接落到 NGINX 和 Spring；服务器没有这个页面映射，就返回 Whitelabel 404。

如果把所有 404 都回退到 `index.html`，页面刷新是好了，但不存在的 API 和静态资源也可能返回一份 HTML，排错会更麻烦。

实际配置把路由分开处理：`/api/` 和 `/assets/` 保留真实 404，`/ws/` 走升级代理，普通前端路径才回退到 Vue 入口。实验页和报告页可以直接访问，错误的接口地址仍然会明确报错。

## 上线时没有重启 Java

这次改动只在部署层。服务器上的旧 NGINX 配置先备份，仓库里保存同一份正式配置。上传后比较 SHA-256，执行 `nginx -t`，通过才平滑 reload。Java 进程和实验数据都没动。

验收没有停在“首页 200”。我创建了一次临时实验，用浏览器同源 Origin 建立公网 WebSocket，确认状态变成 Open，并实际收到一条 `SNAPSHOT`。NGINX access log 里记录了 101；实验页和报告页直接请求都是 200；不存在的 API 与静态资源仍是 404。测试实验随后取消并删除。

之前的部署检查只看了根页面和 REST，还确认过配置里写了 Upgrade，于是误以为 WebSocket 已经可用。以后这类站点的验收项得拆开：HTTP 页面、REST、WebSocket 101、至少一条业务消息、前端子路由刷新。少一个，都只能说明一部分链路活着。
