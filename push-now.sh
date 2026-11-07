#!/bin/sh
# 网络恢复后一键推送 OkaWiFi 的两个未推送提交。
#
# 背景：本次会话里 github.com:443 的 git 通道被沙箱策略阻断
#   （代理对 github.com 返回 502 / 直连 000，而 api.github.com 正常 200），
#   两个提交已 commit 在本地，只是推不上去。
#
# 用法（在 Git Bash 里）：
#   sh push-now.sh
#
# 若仍报 CONNECT tunnel failed / Could not connect：
#   说明 github.com 还不通，先用下面的命令确认，再重试：
#     curl -s -o /dev/null -w "%{http_code}\n" --max-time 15 \
#       "https://github.com/liehuu/oka-wifi-tweak.git/info/refs?service=git-upload-pack"
#   期望 200 或 401（401 也算通，只是要认证）。
#   返回 000 就是不通。

set -u

REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO" || exit 1

echo "== 仓库 =="
pwd
echo

echo "== 待推送 =="
git log --oneline origin/main..HEAD
NCOMMIT=$(git rev-list --count origin/main..HEAD)
echo "共 $NCOMMIT 个提交"
echo

echo "== 探测 github.com =="
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
  "https://github.com/liehuu/oka-wifi-tweak.git/info/refs?service=git-upload-pack" 2>/dev/null)"
echo "info/refs -> ${CODE:-000}"
case "$CODE" in
  200|401) echo "✓ 通道可用，开始推送" ;;
  000|"")
      echo "✗ github.com 仍不可达（000）。"
      echo "  可选："
      echo "    1) 开代理后重跑本脚本"
      echo "    2) 用补丁方式在别处应用："
      echo "         git am ../patches/*.patch"
      exit 1
      ;;
  *)
      # ★ ${CODE} 必须带花括号：裸 $CODE 后紧跟全角标点时，
      #   macOS 的 bash 3.2 会把标点的首字节吞进变量名 → unbound variable
      echo "? 非预期状态码 ${CODE}，仍尝试推送"
      ;;
esac
echo

echo "== push =="
git push origin main
RC=$?
echo
if [ $RC -eq 0 ]; then
  echo "✓ 推送成功。CI 会自动跑三变体矩阵："
  echo "  https://github.com/liehuu/oka-wifi-tweak/actions"
  echo
  echo "  看日志："
  echo "    gh run list --limit 3"
  echo "    gh run watch"
else
  # ★ 同上：${RC} 带花括号，避免全角标点被吞进变量名
  echo "✗ 推送失败（exit ${RC}）。若提示认证问题，先修凭据："
  echo "    git config --system credential.helper manager"
  exit $RC
fi