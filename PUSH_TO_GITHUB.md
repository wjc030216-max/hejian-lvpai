# 推送到 GitHub 指南（含安全清理）

> 适用：Windows PowerShell。
> 重要：不要把旧桂智旅仓库历史、`deploy-config/`、`.env.local.real`、证书或数据库文件推送到任何远程仓库。

## 一、准备

1. 注册/登录 GitHub。
2. 网页上新建一个**空仓库**：
   - Repository name：例如 `hejian-lvpai`
   - 可见性：Private（强烈建议，因为这是内部项目）
   - **不要勾选**「Add a README / .gitignore / license」，保持空仓库。
3. 记下仓库地址，形如：`https://github.com/<你的账号>/hejian-lvpai.git`

## 二、本地打包一个干净的提交

打开 PowerShell，逐条执行（把仓库地址换成你自己的）：

```powershell
cd G:\贺见旅拍

# 1) 确认当前在项目目录，且看到 git 信息
git status

# 2) 新建一个“没有历史”的分支（丢弃旧的一期提交历史，工作区文件原样保留）
git checkout --orphan github-main

# 3) 把当前所有文件加入暂存区（.gitignore 会自动排除 .env.*、dist、tls、cert 等）
git add -A

# 4) 生成第一个干净提交
git commit -m "init: 贺见旅拍 AI 旅拍小程序"
```

说明：

- `--orphan` 会创建一条从零开始的分支，旧的 `4329fcf` 提交（含私钥/DB）不会进入新分支。
- `git add -A` 只添加当前工作区的文件；一期已被删除的文件不会回来。
- 提交前可以再检查一次，确保没有敏感文件：

```powershell
git ls-files | Select-String -Pattern "\.pem$|\.key$|\.db$|\.env$|/cert/|/tls/|/dist/"
```

如果这个命令有输出，先处理掉那些文件再继续。

## 三、关联 GitHub 并推送

```powershell
# 5) 添加 GitHub 远程（不影响原来的内网 origin）
git remote add github https://github.com/<你的账号>/hejian-lvpai.git

# 6) 推送到 GitHub 的 main 分支
git push github github-main:main
```

如果 GitHub 仓库默认分支是 `master`，把最后一条改成：

```powershell
git push github github-main:master
```

推送成功后，打开 GitHub 仓库页面就能看到 `README.md`。

## 四、以后日常更新怎么推

以后每次改完代码，切回工作分支提交并推到 GitHub：

```powershell
git checkout github-main   # 或你常用的开发分支
git add -A
git commit -m "更新说明"
git push github github-main:main
```

## 五、可选：保留内网 origin 双推

本地可以同时保留两个远程：

- 不添加旧桂智旅的远程仓库，避免误推送代码或配置。
- `github` → GitHub

推内网用 `git push origin <分支>`，推 GitHub 用 `git push github <分支>:main`，互不影响。

## 六、如果你坚持要保留旧历史

必须先彻底清除历史中的敏感文件再推公开仓库，建议用：

- `git filter-repo`（推荐）：`git filter-repo --path server/pay/cert --path server/pay/instance --path server/auth/instance --invert-paths`
- 或 BFG Repo-Cleaner

清理后强制推送 `git push --force github github-main:main`。**不清理就推公开仓库会泄露商户私钥，绝对不要这么做。**
