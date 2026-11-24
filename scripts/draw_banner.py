"""Draw the README banner, one SVG per GitHub theme, from one template.

    python3 scripts/draw_banner.py     # writes docs/assets/banner-light.svg and banner-dark.svg

The README picks one with <picture> and prefers-color-scheme. The colours are GitHub's own (Primer).
"""

import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "assets"

THEMES = {
    "light": dict(bg1="#ffffff", bg2="#f6f8fa", line="#d0d7de", ink="#1f2328", muted="#59636e", accent="#0969da",
                  card="#ffffff", card2="#eef1f4", bar="#d0d7de", val="#8c959f", flag="#cf222e", ok="#1a7f37"),
    "dark": dict(bg1="#0d1117", bg2="#161b22", line="#30363d", ink="#e6edf3", muted="#9198a1", accent="#4493f8",
                 card="#1c2128", card2="#151b23", bar="#3d444d", val="#656c76", flag="#f85149", ok="#3fb950"),
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"


def rows(c: dict) -> str:
    out = []
    for i in range(6):
        y = 74 + i * 30
        value = c["flag"] if i == 2 else c["val"]
        out.append(f'<rect x="22" y="{y}" width="{92 - (i % 3) * 14}" height="10" rx="5" fill="{c["bar"]}"/>')
        out.append(f'<rect x="132" y="{y}" width="38" height="10" rx="5" fill="{value}"/>')
    out.append(f'<path d="M180 {74 + 60 + 10} l7 -11 l7 11 z" fill="{c["flag"]}"/>')      # the flagged row's arrow
    return "".join(out)


def svg(c: dict) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="400" viewBox="0 0 1600 400" role="img" aria-label="mirobody-gen: synthetic health records with row-level ground truth">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c["bg1"]}"/><stop offset="1" stop-color="{c["bg2"]}"/></linearGradient>
<linearGradient id="trace" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{c["accent"]}"/><stop offset="0.75" stop-color="{c["accent"]}" stop-opacity="0.55"/><stop offset="1" stop-color="{c["accent"]}" stop-opacity="0"/></linearGradient>
</defs>
<rect x="1" y="1" width="1598" height="398" rx="28" fill="url(#bg)" stroke="{c["line"]}" stroke-width="2"/>
<g transform="translate(150 58) rotate(7 110 140)" opacity="0.9"><rect width="220" height="280" rx="16" fill="{c["card2"]}" stroke="{c["line"]}" stroke-width="2"/></g>
<g transform="translate(130 58) rotate(-4 110 140)">
<rect width="220" height="280" rx="16" fill="{c["card"]}" stroke="{c["line"]}" stroke-width="2"/>
<rect x="22" y="28" width="118" height="16" rx="8" fill="{c["accent"]}"/>
{rows(c)}
</g>
<g transform="translate(322 300)"><circle r="30" fill="{c["ok"]}"/><path d="M-13 1 l9 9 l17 -19" fill="none" stroke="#ffffff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></g>
<text x="440" y="196" font-family="{FONT}" font-size="104" font-weight="700" fill="{c["ink"]}" letter-spacing="-2">mirobody<tspan fill="{c["accent"]}">-gen</tspan></text>
<text x="444" y="258" font-family="{FONT}" font-size="36" fill="{c["muted"]}">Synthetic health records with row-level ground truth</text>
<polyline points="444,320 800,320 826,320 842,292 860,350 878,262 896,320 930,320 1520,320" fill="none" stroke="url(#trace)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
'''


if __name__ == "__main__":
    for name, colours in THEMES.items():
        (OUT / f"banner-{name}.svg").write_text(svg(colours), encoding="utf-8")
        print(OUT / f"banner-{name}.svg")
