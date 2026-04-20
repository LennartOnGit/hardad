#!/usr/bin/env bash
#
# spdx-render.sh — strip file-level entries from an SPDX SBOM and render a Mermaid SVG.
#
# Why: SBOMs of installed virtualenvs include thousands of file nodes that blow past
# Mermaid's text-size limit. This script keeps only Package-level nodes/relationships.
#
# Requires: jq, spdx-to-mermaid, and mmdc (or npx as a fallback).
#
# Usage: spdx-render.sh <sbom.spdx.json> [-o <output-dir>] [--clean] [--no-render]

set -euo pipefail

INPUT=""
OUTDIR="/Users/leo/Code/hardad/docs/mermaid"
CLEAN=0
RENDER=1
MERMAID_CONFIG="${MERMAID_CONFIG:-$HOME/.local/mmdc/mermaid-config.json}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output-dir) OUTDIR="$2"; shift 2 ;;
    --clean)         CLEAN=1; shift ;;
    --no-render)     RENDER=0; shift ;;
    -h|--help)       sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)              echo "unknown flag: $1" >&2; exit 2 ;;
    *)
      [[ -z "$INPUT" ]] && INPUT="$1" || { echo "unexpected arg: $1" >&2; exit 2; }
      shift ;;
  esac
done

[[ -n "$INPUT" && -f "$INPUT" ]] || { echo "usage: $0 <sbom.spdx.json> [-o dir] [--clean] [--no-render]" >&2; exit 2; }

INPUT_ABS="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
BASENAME="$(basename "$INPUT" .spdx.json)"
[[ "$BASENAME" == "$(basename "$INPUT")" ]] && BASENAME="${BASENAME%.json}"
OUTDIR="${OUTDIR:-$(dirname "$INPUT_ABS")}"
mkdir -p "$OUTDIR"

FILTERED="$OUTDIR/${BASENAME}.pkgs-only.spdx.json"
MMD="$OUTDIR/${BASENAME}.mmd"
SVG="$OUTDIR/${BASENAME}.svg"

# 1. strip file-level nodes
echo "→ filtering SPDX..."
jq '
  ([.files[]?.SPDXID] | map({(.): true}) | add // {}) as $fileIds
  | .files = []
  | .relationships = [
      (.relationships // [])[]
      | select(($fileIds[.spdxElementId]    // false) == false
            and ($fileIds[.relatedSpdxElement] // false) == false)
    ]
' "$INPUT_ABS" > "$FILTERED"
printf "  files removed: %d | relationships kept: %d | packages: %d\n" \
  "$(jq '(.files // []) | length' "$INPUT_ABS")" \
  "$(jq '(.relationships // []) | length' "$FILTERED")" \
  "$(jq '(.packages // []) | length' "$FILTERED")"

# 2. mermaid source
echo "→ generating mermaid..."
spdx-to-mermaid "$FILTERED" -o "$MMD"

# 2a. extend the existing legend node (added by spdx-to-mermaid) with emoji entries
python3 - "$MMD" <<'PYEOF'
import re, sys
path = sys.argv[1]
EXTRA = (
    '\n📄 License'
    '\n⬇️ Download'
    '\n🚛 Supplier'
    '\n💥 Originator'
    '\n⛔️ NOASSERTION / unset'
)
with open(path) as f: c = f.read()
new, n = re.subn(
    r'(legend\["[^"]*?)("\])',
    lambda m: m.group(1) + EXTRA + m.group(2),
    c, count=1,
)
if n == 0:
    print("  (no 'legend[...]' node found — emoji entries not merged)", file=sys.stderr)
with open(path, 'w') as f: f.write(new)
PYEOF

# 2b. strip noise lines from each node label (preserves the closing "])
# License/Download/Supplier/Originator are kept — they're transformed in the SVG post-processor below.
awk '
  /^(License Declared|License Comments|Files Analyzed|Verification|purl|Copyright|Homepage|Summary|Description): / {
    if (/"\]$/) print "\"]"
    next
  }
  { print }
' "$MMD" > "$MMD.tmp" && mv "$MMD.tmp" "$MMD"

printf "  %s (%d bytes, %d edges)\n" "$MMD" "$(wc -c <"$MMD")" "$(grep -c -- '-->' "$MMD" || true)"

# 3. svg
if [[ $RENDER -eq 1 ]]; then
  echo "→ rendering SVG..."
  if command -v mmdc >/dev/null 2>&1; then mmdc=(mmdc)
  else mmdc=(npx -y -p @mermaid-js/mermaid-cli mmdc); fi
  args=(-i "$MMD" -o "$SVG")
  [[ -f "$MERMAID_CONFIG" ]] && args=(-c "$MERMAID_CONFIG" "${args[@]}")
  "${mmdc[@]}" "${args[@]}"
  echo "  $SVG"

  # 3b. transform package node labels: replace field names with emoji, consolidate NOASSERTION lines
  echo "→ post-processing SVG..."
  python3 - "$SVG" <<'PYEOF'
import re, sys

SVG_PATH = sys.argv[1]
TARGETS = [
    ('License',    '📄'),
    ('Download',   '⬇️'),
    ('Supplier',   '🚛'),
    ('Originator', '💥'),
]
EMPTY_MARK = '⛔️'
TARGET_NAMES = {name for name, _ in TARGETS}
EMOJI = dict(TARGETS)

def is_empty(v):
    v = (v or '').strip()
    return not v or v.upper() == 'NOASSERTION'

def transform_p_inner(inner_html):
    """Rewrite the HTML inside a <p> tag for a Package node label."""
    parts = re.split(r'<br\s*/?>', inner_html)
    parts = [p.strip() for p in parts]
    if not parts or '[Package]' not in parts[0]:
        return inner_html   # not a package node; leave untouched

    kept, populated, empty = [], [], []
    for p in parts:
        if ':' in p:
            label, _, value = p.partition(':')
            if label.strip() in TARGET_NAMES:
                emoji = EMOJI[label.strip()]
                if is_empty(value):
                    empty.append(emoji)
                else:
                    populated.append(f"{emoji}: {value.strip()}")
                continue
        kept.append(p)

    new_lines = kept + populated
    if empty:
        new_lines.append(' '.join(f"{e}:{EMPTY_MARK}" for e in empty))
    return '<br />'.join(new_lines)

# Match each foreignObject's <p>...</p> content and rewrite it in place.
FO_P_RE = re.compile(
    r'(<foreignObject[^>]*>.*?<p>)(.*?)(</p>.*?</foreignObject>)',
    re.DOTALL
)

with open(SVG_PATH) as f:
    svg = f.read()

transformed = 0
def repl(m):
    global transformed
    prefix, inner, suffix = m.groups()
    new_inner = transform_p_inner(inner)
    if new_inner != inner:
        transformed += 1
    return prefix + new_inner + suffix

svg = FO_P_RE.sub(repl, svg)

# ---- resize each node's rect/foreignObject to fit its actual content ----
# Addresses two issues: (a) mermaid measures content with a uniform 200px cap
# so emoji rows overflow, (b) Package nodes all get the same height regardless
# of line count. We recompute both dimensions from the transformed label text.
LINE_HEIGHT = 24   # 16px font * 1.5
H_PAD = 40         # 20px each side — generous for emoji rendering variance
V_PAD = 20         # 10px top + 10px bottom — tight

def _char_px(ch):
    code = ord(ch)
    if code in (0xFE0F, 0xFE0E, 0x200D):                   # zero-width
        return 0
    if (0x1F300 <= code <= 0x1F9FF                         # emoji
        or 0x2600 <= code <= 0x27BF
        or 0x1F000 <= code <= 0x1F2FF):
        return 22
    if code > 127:                                         # other non-ASCII
        return 16
    if ch in 'iIl.,;:!':
        return 5
    if ch in 'WM':
        return 14
    return 9

def _estimate(p_content):
    lines = re.split(r'<br\s*/?>', p_content)
    lines = [re.sub(r'<[^>]+>', '', l).strip() for l in lines]
    if not lines:
        return (140, 50)
    max_w = max(sum(_char_px(c) for c in l) for l in lines)
    return (max(max_w + H_PAD, 120),
            max(len(lines) * LINE_HEIGHT + V_PAD, 50))

def _resize_node(block):
    p = re.search(r'<p>(.*?)</p>', block, re.DOTALL)
    if not p:
        return block
    rw, rh = _estimate(p.group(1))
    fw, fh = rw - H_PAD, rh - V_PAD
    block = re.sub(
        r'(<rect class="basic label-container"[^>]*?)x="[^"]+"\s+y="[^"]+"\s+width="[^"]+"\s+height="[^"]+"',
        f'\\g<1>x="{-rw/2:.3f}" y="{-rh/2:.3f}" width="{rw:.3f}" height="{rh:.3f}"',
        block, count=1)
    block = re.sub(
        r'(<g class="label"[^>]*?transform=")translate\([^)]+\)',
        f'\\g<1>translate({-fw/2:.3f}, {-fh/2:.3f})',
        block, count=1)
    block = re.sub(
        r'(<foreignObject\s+)width="[^"]+"\s+height="[^"]+"',
        f'\\g<1>width="{fw:.3f}" height="{fh:.3f}"',
        block, count=1)
    return block

svg, resized = re.subn(
    r'(<g class="node[^"]*"[^>]*>)(.*?)(</g></g>)',
    lambda m: m.group(1) + _resize_node(m.group(2)) + m.group(3),
    svg, flags=re.DOTALL,
)
print(f"  resized {resized} nodes to fit content")

with open(SVG_PATH, 'w') as f:
    f.write(svg)

print(f"  transformed {transformed} package nodes")
PYEOF
fi

[[ $CLEAN -eq 1 ]] && rm -f "$FILTERED" "$MMD" && echo "  (intermediates removed)"
echo "done."