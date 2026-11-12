#!/bin/sh
# 切换单机部署的收费形态（只改 .env.single 里的三个变量并重启 phase2-api）。
#
#   ./switch-mode.sh free        # 完全免费测试：dev-bypass，不扣款、不消耗名额
#   ./switch-mode.sh quota 500   # 先到先得 500 个免费名额，用完后按标准价收费（通联）
#   ./switch-mode.sh paid        # 全部按标准价收费（通联），没有免费名额
#
# 切换为 quota / paid 前确认：域名已备案、443 可从公网访问、通联回调地址可达，
# 否则下单会停在待支付状态。
set -e
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
if [ -f "$SCRIPT_DIR/.env.single" ]; then
  ROOT="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../../.env.single" ]; then
  ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
else
  echo "找不到 .env.single（请在项目根目录或 infra/staging 下运行）" >&2
  exit 1
fi
cd "$ROOT"
ENV_FILE=.env.single
MODE="$1"
N="${2:-0}"

setenv() {
  key="$1"; val="$2"
  if grep -q "^$key=" "$ENV_FILE"; then
    sed -i "s|^$key=.*|$key=$val|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

case "$MODE" in
  free)
    setenv PHASE2_PAYMENT_MODE dev-bypass
    setenv PHASE2_FREE_QUOTA_TOTAL 0
    echo "→ 完全免费测试：dev-bypass，不扣款，名额计数器不动"
    ;;
  quota)
    [ "$N" -gt 0 ] 2>/dev/null || { echo "用法: $0 quota <名额数>" >&2; exit 2; }
    setenv PHASE2_PAYMENT_MODE allinpay
    setenv PHASE2_FREE_QUOTA_TOTAL "$N"
    echo "→ 前 $N 单免费，之后按标准价收费（通联 W06S）"
    ;;
  paid)
    setenv PHASE2_PAYMENT_MODE allinpay
    setenv PHASE2_FREE_QUOTA_TOTAL 0
    echo "→ 全部按标准价收费（通联 W06S）"
    ;;
  *)
    echo "用法: $0 free | quota <名额数> | paid" >&2
    exit 2
    ;;
esac

docker compose --env-file "$ENV_FILE" -f infra/docker-compose.single.yml up -d phase2-api >/dev/null
sleep 8
echo "--- 当前生效配置 ---"
curl -sSk -m 10 https://127.0.0.1/api/v2/config | python3 -c '
import sys, json
d = json.load(sys.stdin)["data"]
print("payment_mode :", d["payment_mode"])
print("prices(分)   :", d.get("price_tiers"))
print("free_quota   :", d["free_quota"])
'
