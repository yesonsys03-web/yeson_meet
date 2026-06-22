#!/usr/bin/env bash
#
# Create a STABLE self-signed code-signing identity for local macOS builds.
#
# Why: the client app is unsigned, so macOS TCC keys Screen Recording permission
# to the binary's cdhash. Every rebuild changes the hash, so the permission is
# lost and must be re-granted (the "ghost permission" loop). Signing the app with
# a *stable* identity makes TCC key on the certificate instead of the hash, so the
# grant survives rebuilds. This is for in-house LAN use only — it does NOT make
# Gatekeeper happy (other Macs still need a one-time right-click > Open).
#
# Run ONCE per machine:  bash apps/desktop/scripts/create-selfsigned-signing-cert.sh
# Then build signed:     pnpm --dir apps/desktop tauri:build:signed
#
# A trust dialog will ask for your login password (to trust the cert for code
# signing). On the FIRST signed build, a "codesign wants to sign using key in
# your keychain" dialog appears — click "Always Allow" once.
#
set -euo pipefail

CERT_NAME="Yeson Meet Self Signed"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

if security find-identity -v -p codesigning | grep -qF "$CERT_NAME"; then
  echo "Identity already exists: \"$CERT_NAME\""
  security find-identity -v -p codesigning | grep -F "$CERT_NAME"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = $CERT_NAME
[v3]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF

# 10-year self-signed cert with the codeSigning extended key usage.
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$WORK/key.pem" -out "$WORK/cert.pem" -config "$WORK/cert.cnf"

# Bundle key + cert into a PKCS#12 for keychain import. `-legacy` forces the old
# RC2/3DES encoding that Apple's `security` tool can read; OpenSSL 3's default
# (AES/PBKDF2) fails import with "MAC verification failed".
openssl pkcs12 -export -legacy -inkey "$WORK/key.pem" -in "$WORK/cert.pem" \
  -name "$CERT_NAME" -out "$WORK/cert.p12" -passout pass:yeson

# Import the identity; pre-authorize codesign + security to use the private key.
security import "$WORK/cert.p12" -k "$KEYCHAIN" -P yeson \
  -T /usr/bin/codesign -T /usr/bin/security

# Trust the cert for code signing so codesign accepts it. User-domain trust
# (no -d) is flaky (SecTrustSettingsSetTrustSettings error 13); the System
# keychain via sudo is reliable. Prompts for your admin password.
sudo security add-trusted-cert -d -r trustRoot -p codeSign \
  -k /Library/Keychains/System.keychain "$WORK/cert.pem"

echo
echo "Created code-signing identity:"
security find-identity -v -p codesigning | grep -F "$CERT_NAME"
echo
echo "Next: pnpm --dir apps/desktop tauri:build:signed"
echo "(On the first build, click \"Always Allow\" on the codesign keychain prompt.)"
