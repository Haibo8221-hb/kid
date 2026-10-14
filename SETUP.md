# SETUP.md —— 子女教育工作台（kid-main）部署与多电脑协同指南

本文件固化了从零到上线的完整步骤。所有值都已填入实际约定，照抄即可。

> 适用场景：多台 Windows 电脑协同开发 → 推送 GitHub → 自动部署到阿里云 ECS（112.74.40.8，宝塔/nginx 托管）。

---

## 0. 已确定的固定值

| 项 | 值 |
|---|---|
| GitHub 仓库 | `git@github.com:Haibo8221-hb/kid.git` |
| 分支 | `main` |
| ECS IP | `112.74.40.8` |
| ECS SSH 端口 | `443`（华为代理只放行 CONNECT 到 443，22 被拒） |
| ECS 用户 | `root` |
| 站点根目录 SITE_ROOT | `/www/wwwroot/kid` |
| 后端服务名 SERVICE | 宝塔建站时起的服务名（例：`plan-api`，见 §3） |

---

## 1. 本机一次性配置（每台电脑做一次）

### 1.1 生成 GitHub 登录用密钥（用于本机 `git push`）
```powershell
ssh-keygen -t ed25519 -C "Haibo8221-hb@github"
```
一路回车。然后把公钥加到 GitHub：复制 `~/.ssh/id_ed25519.pub` 内容 →
GitHub → Settings → SSH and GPG keys → New SSH key。

### 1.2 配置代理（华为笔记本环境）
PowerShell：
```powershell
[Environment]::SetEnvironmentVariable("HTTPS_PROXY", "http://h00603061:Jul_2026@proxyuk.huawei.com:8080", "User")
[Environment]::SetEnvironmentVariable("HTTP_PROXY",  "http://h00603061:Jul_2026@proxyuk.huawei.com:8080", "User")
git config --global http.proxy  http://h00603061:Jul_2026@proxyuk.huawei.com:8080
git config --global https.proxy http://h00603061:Jul_2026@proxyuk.huawei.com:8080
git config --global http.schannelCheckRevoke false   # 绕过公司代理证书吊销检查
```
> GitHub 用 HTTPS 远程推送：`git remote set-url origin https://github.com/Haibo8221-hb/kid.git`
> 推送密码填 **GitHub Personal Access Token**（Settings → Developer settings → PAT，勾 `repo`），不是登录密码。

### 1.3 配置 SSH 走代理到 ECS（用于 `ssh root@112.74.40.8` / rsync）
写入 `C:\Users\你的用户名\.ssh\config`：
```
Host 112.74.40.8
  HostName 112.74.40.8
  User root
  Port 443
  IdentityFile ~/.ssh/id_ed25519
  ProxyCommand "C:\Program Files\Git\mingw64\bin\connect.exe" -H http://h00603061:Jul_2026@proxyuk.huawei.com:8080 %h %p
```
> 注意：`%h %p` 是**空格**分隔（connect.exe 要求），不能写 `%h:%p`。
> 之后 `ssh root@112.74.40.8` 自动走代理，无需任何参数。

---

## 2. 阿里云 ECS 一次性配置

### 2.1 让 sshd 监听 443（阿里云控制台 VNC 或云助手执行）
```bash
grep -q "^Port 443" /etc/ssh/sshd_config || echo -e "Port 22\nPort 443" >> /etc/ssh/sshd_config
systemctl restart ssh
ss -tlnp | grep -E ':443|:22'
```
控制台 **安全组 → 入方向** 放行 TCP 443。

### 2.2 把本机公钥装进 ECS（本机 PowerShell）
```powershell
cat ~/.ssh/id_ed25519.pub | ssh root@112.74.40.8 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```
验证：`ssh root@112.74.40.8` 应能直接进 `root@...:~#`。

### 2.3 安装 rsync（若未装）
```bash
which rsync || (apt-get update && apt-get install -y rsync)   # Debian/Ubuntu
```

---

## 3. 宝塔建站 + 后端服务

> 应用入口：`api_server.py` 里 `app = Flask(__name__)`，直接运行默认监听 `127.0.0.1:5000`。
> 依赖：`flask` + `httpx` + `gunicorn`（运行用）+ `pyyaml`（部分功能），已写入仓库 `requirements.txt`。

### 3.1 添加站点
**网站 → 添加站点**：站点名/域名填 `kid` → 根目录自动为 `/www/wwwroot/kid`（与 SITE_ROOT 一致）。

### 3.2 起后端（宝塔「Python 项目」管理器，推荐）
1. 软件商店安装 **Python 项目**（Python 项目管理器）；并安装一个 Python 3.x 版本。
2. **添加项目**：
   - 项目名称：`kid-api`（这串就是后面 Secrets 的 `SERVICE` 值，建议就叫 `kid-api`）
   - 项目路径：`/www/wwwroot/kid`
   - 启动方式：**gunicorn**
   - 启动命令：`gunicorn -w 4 -b 127.0.0.1:5000 api_server:app`
   - 端口：`5000`
   - 勾选 **安装模块依赖**（自动 `pip install -r requirements.txt`）
3. 启动后点「设置 → 环境变量」加上（关键，否则数据写错目录、跨域被拦、登录用默认密码）：
   ```
   DATA_DIR=/www/wwwroot/kid/data
   ALLOWED_ORIGINS=*            # 生产改成你的真实域名，如 https://kid.你的域名.com
   HERMES_HOME=/www/wwwroot/kid/.hermes
   CHAT_MAX_HISTORY=20
   APP_PASSWORD=<你的访问密码>    # 应用层登录密码（默认 haibo2026，务必修改！）
   # APP_SECRET=<随机串>         # 可选；留空则从密码派生（改密码=吊销全部会话）
   ```

### 3.3 备选：supervisor 直接托管（宝塔管理器不顺手时用）
把仓库里的 `deploy/supervisor-kid-api.conf` 传到 ECS：
```bash
cp /www/wwwroot/kid/deploy/supervisor-kid-api.conf /etc/supervisor/conf.d/kid-api.conf
supervisorctl reread && supervisorctl update && supervisorctl restart kid-api
```
此时 `SERVICE=kid-api`。

### 3.4 确认 SERVICE 名（用于 Secrets）
ECS 上执行：
```bash
supervisorctl status
```
输出里 `kid-api  RUNNING ...` 的 `kid-api` 就是 `SERVICE` 的确切值（宝塔管理器起的名字可能带前缀，以这里显示为准）。

### 3.5 nginx 反代 /api/
站点 → 设置 → 配置文件，在 `server{}` 内加：
```nginx
location /api/ {
    proxy_pass http://127.0.0.1:5000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

### 3.6 安全屏蔽
把仓库里的 `nginx-git-protect.conf` 内容贴进站点 nginx 配置 `server{}` 内（防 `.git`/`.env` 被公网下载），保存并重载。

### 3.7 权限与目录
```bash
mkdir -p /www/wwwroot/kid/data /www/wwwroot/kid/logs /www/wwwroot/kid/.hermes
chown -R www:www /www/wwwroot/kid
```

---

## 4. GitHub Secrets（Settings → Secrets and variables → Actions → New repository secret）

逐条添加（每条 Name → 值）：

| Name | Value |
|---|---|
| `ECS_HOST` | `112.74.40.8` |
| `ECS_PORT` | `443` |
| `ECS_USER` | `root` |
| `ECS_SSH_KEY` | 本机 `~/.ssh/id_ed25519` **私钥全文**（含 BEGIN/END 共 7 行） |
| `SITE_ROOT` | `/www/wwwroot/kid` |
| `SERVICE` | 第 3 步起的后端服务名（如 `plan-api`） |

> 取私钥全文：`Get-Content ~/.ssh/id_ed25519 | Set-Clipboard`，再粘贴到 Secret 框。
> **不再需要** GitHub Deploy Key（方案 B 下 ECS 不主动拉 GitHub）。

---

## 5. 触发部署

- **首次或改完代码**：本机 `git push -u origin main`（或后续 `git push`）。
- **重跑最近一次**：GitHub → Actions → 对应 run → Re-run jobs。
- **手动应急**（不走 Actions，本机直推）：`bash deploy.sh`（需本机 SSH 代理已配）。

部署流程：Actions checkout → rsync 推到 `/www/wwwroot/kid` → 修权限 `www:www` → `supervisorctl restart <SERVICE>`。

---

## 6. 验证与回滚

- 浏览器打开站点域名，确认页面/接口正常。
- 回滚：`git revert <commit> && git push` 即自动回退到上一版。

---

## 7. 日常多电脑协同

- 其他电脑：装好 §1 配置后 `git clone https://github.com/Haibo8221-hb/kid.git`。
- 开工前拉最新：`git pull origin main`（或仓库根目录 `aliyun-deploy-kit/sync.bat`）。
- 改完提交并上线：`git add -A && git commit -m "..." && git push`。
