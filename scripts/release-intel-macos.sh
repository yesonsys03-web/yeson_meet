#!/usr/bin/env bash
# release-intel-macos.sh — 인텔(x86_64) macOS 릴리스 자산을 이 인텔 Mac에서 빌드해
# 기존 GitHub 릴리스에 추가한다. GitHub Actions 러너는 Apple Silicon이라 CI가
# darwin-aarch64만 만들기 때문에, 인텔용 dmg + 자동 업데이트 아티팩트는 여기서 채운다.
#
# 하는 일:
#   1) 클라이언트·서버 콘솔을 --bundles app 으로 빌드(Kaspersky의 bundle_dmg.sh
#      "Resource busy" 회피) + 업데이터 서명 아티팩트(.app.tar.gz/.sig) 생성
#   2) makehybrid + UDZO 로 인텔 dmg 2종 생성(마운트 없이 — 로컬 AV 간섭 회피)
#   3) dmg 2종 + 인텔 업데이터 아티팩트(아키텍처 접미사 _x64)를 릴리스에 업로드
#   4) latest-client.json / latest-server.json 에 darwin-x86_64 항목 병합·재업로드
#      → 인텔 Mac도 자동 업데이트 대상이 된다(계획 초기엔 제외였으나 2026-07-09 포함)
#
# 전제:
#   - 이 스크립트는 인텔 Mac에서만 실행(arch: x86_64)
#   - 업데이터 개인키가 ~/.tauri/yeson_meet_updater.key 에 있고 비밀번호는 없음
#     (CI 시크릿과 동일한 키. 분실 시 자동 업데이트 체인 단절)
#   - gh 로그인 완료, 대상 릴리스(vX.Y.Z)가 이미 존재(먼저 CI가 만든 뒤 실행)
#   - jq, python3 사용 가능
#
# 사용법:  scripts/release-intel-macos.sh v1.2.1
set -euo pipefail

TAG="${1:-}"
if [[ -z "$TAG" ]]; then
  echo "사용법: $0 <release-tag>   예) $0 v1.2.1" >&2
  exit 1
fi
VERSION="${TAG#v}"
REPO="yesonsys03-web/yeson_meet"

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "이 스크립트는 인텔(x86_64) Mac에서 실행해야 합니다. 현재: $(uname -m)" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEY="$HOME/.tauri/yeson_meet_updater.key"
if [[ ! -f "$KEY" ]]; then
  echo "업데이터 개인키가 없습니다: $KEY" >&2
  exit 1
fi
export TAURI_SIGNING_PRIVATE_KEY; TAURI_SIGNING_PRIVATE_KEY="$(cat "$KEY")"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""

STAGE_OUT="$(mktemp -d)"
trap 'rm -rf "$STAGE_OUT"' EXIT

make_dmg() { # $1=.app path  $2=out dmg  $3=volname
  local APP="$1" OUT="$2" VOL="$3" STAGE TMP
  STAGE="$(mktemp -d)"; TMP="$(mktemp -u).dmg"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil makehybrid -hfs -hfs-volume-name "$VOL" -o "$TMP" "$STAGE" >/dev/null
  hdiutil convert "$TMP" -format UDZO -o "$OUT" >/dev/null
  rm -rf "$STAGE" "$TMP"
  echo "  dmg: $OUT"
}

# $1=app dir  $2=product  $3=dmg 파일명(태그 버전 포함)
build_one() {
  local APPDIR="$1" PRODUCT="$2" DMGNAME="$3"
  echo "=== build $PRODUCT (intel) ==="
  ( cd "$ROOT/$APPDIR" && pnpm exec tauri build --bundles app )
  local BUNDLE="$ROOT/$APPDIR/src-tauri/target/release/bundle/macos"
  make_dmg "$BUNDLE/$PRODUCT.app" "$STAGE_OUT/$DMGNAME" "$PRODUCT"
  # 인텔 업데이터 아티팩트는 CI의 aarch64용($PRODUCT.app.tar.gz)과 이름이 겹치므로
  # _x64 접미사를 붙여 복사(릴리스에서 덮어쓰기 방지).
  cp "$BUNDLE/$PRODUCT.app.tar.gz"     "$STAGE_OUT/${PRODUCT}_x64.app.tar.gz"
  cp "$BUNDLE/$PRODUCT.app.tar.gz.sig" "$STAGE_OUT/${PRODUCT}_x64.app.tar.gz.sig"
}

# $1=manifest 파일명  $2=인텔 업데이터 파일명  $3=sig 경로
merge_manifest() {
  local MAN="$1" FN="$2" SIG="$3" DIR
  DIR="$(mktemp -d)"
  gh release download "$TAG" -R "$REPO" -p "$MAN" -D "$DIR" --clobber
  URL="https://github.com/$REPO/releases/download/$TAG/$FN" \
  SIGVAL="$(cat "$SIG")" \
  python3 - "$DIR/$MAN" <<'PY'
import json, os, sys
p = sys.argv[1]
d = json.load(open(p))
d.setdefault("platforms", {})["darwin-x86_64"] = {
    "signature": os.environ["SIGVAL"],
    "url": os.environ["URL"],
}
json.dump(d, open(p, "w"), indent=1)
print("  merged darwin-x86_64 ->", os.path.basename(p), list(d["platforms"].keys()))
PY
  gh release upload "$TAG" -R "$REPO" "$DIR/$MAN" --clobber
  rm -rf "$DIR"
}

build_one apps/desktop        yeson-meet           "yeson-meet_${VERSION}_x64.dmg"
build_one apps/server_desktop yeson-server-console "yeson-server-console_${VERSION}_x64.dmg"

echo "=== upload intel assets to $TAG ==="
gh release upload "$TAG" -R "$REPO" \
  "$STAGE_OUT/yeson-meet_${VERSION}_x64.dmg" \
  "$STAGE_OUT/yeson-server-console_${VERSION}_x64.dmg" \
  "$STAGE_OUT/yeson-meet_x64.app.tar.gz" "$STAGE_OUT/yeson-meet_x64.app.tar.gz.sig" \
  "$STAGE_OUT/yeson-server-console_x64.app.tar.gz" "$STAGE_OUT/yeson-server-console_x64.app.tar.gz.sig" \
  --clobber

echo "=== merge intel entry into update manifests ==="
merge_manifest latest-client.json yeson-meet_x64.app.tar.gz           "$STAGE_OUT/yeson-meet_x64.app.tar.gz.sig"
merge_manifest latest-server.json yeson-server-console_x64.app.tar.gz "$STAGE_OUT/yeson-server-console_x64.app.tar.gz.sig"

echo "DONE — 인텔 dmg + 업데이터 아티팩트 업로드 및 매니페스트(darwin-x86_64) 병합 완료: $TAG"
