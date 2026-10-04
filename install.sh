#!/bin/bash
# 去气口工具 macOS 一键安装
# 用法（终端）：curl -fsSL https://raw.githubusercontent.com/lilanqiao/qiqikou-tool/main/install.sh | bash
# 自动识别 Apple 芯片 / Intel，只下载对应的一个安装包，装进「应用程序」，并解除"无法验证开发者"拦截。
set -euo pipefail

REPO="lilanqiao/qiqikou-tool"
APP="去气口工具.app"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

if [ "$(uname -s)" != "Darwin" ]; then
  red "这个脚本只用于 Mac。Windows 请在 PowerShell 里运行："
  echo "  irm https://raw.githubusercontent.com/$REPO/main/install.ps1 | iex"
  exit 1
fi

major=$(sw_vers -productVersion | cut -d. -f1)
if [ "$major" -lt 11 ]; then
  red "需要 macOS 11 或更新，当前是 $(sw_vers -productVersion)"; exit 1
fi

# 终端被 Rosetta 转译时 uname -m 会报 x86_64，要再问一下内核才知道真实芯片
arch=$(uname -m)
if [ "$arch" = "arm64" ] || [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
  kind="AppleSilicon"; label="Apple 芯片"
else
  kind="Intel"; label="Intel 芯片"
fi

echo
printf '\033[33m== 去气口工具 安装 ==\033[0m\n'
echo "检测到系统：macOS $(sw_vers -productVersion)（${label}），只下载对应的 Mac 版"

# 用"最新版"固定下载地址，不走 GitHub API（API 未登录每小时限 60 次，共用 IP 时容易 403）
url="https://github.com/$REPO/releases/latest/download/QiQiKou-Mac-${kind}.dmg"

tmp=$(mktemp -d)
mnt="$tmp/mnt"
cleanup() { hdiutil detach "$mnt" -quiet 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT

echo "下载：$(basename "$url")"
curl -fL --progress-bar -o "$tmp/app.dmg" "$url"

echo "安装中…"
mkdir -p "$mnt"
hdiutil attach "$tmp/app.dmg" -nobrowse -quiet -mountpoint "$mnt"

# 「应用程序」没有写权限（非管理员账号）时，装到用户自己的应用程序文件夹
dest="/Applications"
[ -w "$dest" ] || { dest="$HOME/Applications"; mkdir -p "$dest"; }

pkill -f "$dest/$APP/Contents/MacOS/" 2>/dev/null || true
rm -rf "$dest/$APP"
cp -R "$mnt/$APP" "$dest/"
xattr -cr "$dest/$APP"     # 去掉"从网上下载"标记，第一次打开不会被拦截

echo
green "安装完成！在「启动台」或「${dest}」里打开「去气口工具」即可"
