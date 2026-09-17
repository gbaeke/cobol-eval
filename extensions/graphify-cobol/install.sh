#!/bin/bash
# Install the COBOL extractor into a graphify checkout.
#
#   ./install.sh [path-to-graphify-checkout]     (default: ../../gf)
#
# Two parts: the extractor module itself, and a small patch wiring it into
# graphify's extension table, dispatch map and language registry. Idempotent —
# re-running on an already-patched checkout is a no-op.
set -eu
cd "$(dirname "$(readlink -f "$0")")"
HERE=$PWD
GF="${1:-$HERE/../../gf}"

[ -d "$GF/graphify/extractors" ] || { echo "not a graphify checkout: $GF" >&2; exit 1; }

cp "$HERE/cobol.py" "$GF/graphify/extractors/cobol.py"
echo "installed extractors/cobol.py"

cd "$GF"
if git apply --check --reverse "$HERE/wiring.patch" 2>/dev/null; then
  echo "wiring already applied"
elif git apply "$HERE/wiring.patch" 2>/dev/null; then
  echo "wiring applied"
else
  echo "wiring.patch did not apply — graphify has moved on since it was cut." >&2
  echo "Re-add by hand: .cbl/.CBL/.cob/.COB/.cpy/.CPY/.cobol to CODE_EXTENSIONS" >&2
  echo "in detect.py, to _DISPATCH in extract.py, and 'cobol' to" >&2
  echo "LANGUAGE_EXTRACTORS in extractors/__init__.py." >&2
  exit 1
fi
