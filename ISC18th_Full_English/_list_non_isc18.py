#!/usr/bin/env python3
"""List papers that are not in ISC18 conference template format."""
from __future__ import annotations

import json
import re
from pathlib import Path

BOOK = Path(r"i:\Booklet ENG\ISC18th_Full_English")
MAIN = BOOK / "main"
CR = BOOK / "_build_from_tex" / "compile_results.json"
META = BOOK / "papers_metadata.json"


def read_text(p: Path) -> str:
    data = p.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1256", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def resolve_tex(pid: str) -> Path | None:
    folder = MAIN / pid
    pref = folder / f"{pid}.tex"
    if pref.exists():
        return pref
    if not folder.exists():
        return None
    texs = [p for p in folder.rglob("*.tex") if "booklet" not in p.name.lower()]
    return texs[0] if texs else None


def is_isc18(tex: str) -> bool:
    t = tex
    has_sty = bool(
        re.search(r"ISC18-english\.sty|\\input\{[^}]*ISC18", t, re.I)
        or re.search(r"\\usepackage\{[^}]*ISC18", t, re.I)
    )
    has_banner = bool(re.search(r"isc18E|Images/isc18", t, re.I))
    has_fancy = "fancyhdr" in t or r"\fancyhead" in t
    has_conf_header = bool(
        re.search(r"Article authors and affiliations|Article title header", t, re.I)
    )
    # Strong ISC18 look
    if has_sty and (has_banner or has_conf_header or has_fancy):
        return True
    if has_sty:
        return True
    if has_conf_header and has_banner:
        return True
    return False


def main() -> None:
    meta = {
        r["paper_id"]: r
        for r in json.loads(META.read_text(encoding="utf-8"))
        if r.get("status") in {"ok", "pdf_only"}
    }
    cr = json.loads(CR.read_text(encoding="utf-8")) if CR.exists() else {}

    not_fmt = []
    isc18_ok = []
    for pid, r in sorted(meta.items()):
        tex_path = resolve_tex(pid)
        method = (cr.get(pid) or {}).get("method", "?")
        info = {
            "paper_id": pid,
            "title": (r.get("title") or "")[:70],
            "toc_author": r.get("toc_author") or r.get("first_author") or "",
            "method": method,
            "status": r.get("status"),
            "has_sty_meta": r.get("has_sty"),
        }
        if tex_path is None:
            info["reason"] = "no TeX (PDF-only)"
            not_fmt.append(info)
            continue
        tex = read_text(tex_path)
        info["tex"] = str(tex_path.relative_to(BOOK)) if tex_path.is_relative_to(BOOK) else str(tex_path)
        if method.endswith("isc18stamp") or "author_pdf" in method or "pdf_fallback" in method:
            info["reason"] = f"included via author/submission PDF ({method})"
            not_fmt.append(info)
            continue
        if not is_isc18(tex):
            reasons = []
            if not re.search(r"ISC18", tex, re.I):
                reasons.append("no ISC18 sty/input")
            if not re.search(r"isc18E|Images/isc18", tex, re.I):
                reasons.append("no conference banner")
            if "fancyhdr" not in tex and r"\fancyhead" not in tex:
                reasons.append("no fancyhdr")
            info["reason"] = "; ".join(reasons) or "non-ISC18 TeX structure"
            not_fmt.append(info)
        else:
            isc18_ok.append(pid)

    print(f"ISC18-format TeX papers: {len(isc18_ok)}")
    print(f"Flagged: {len(not_fmt)}\n")

    # Split: PDF-included may still have ISC18 TeX source
    print("=" * 60)
    print("A) TeX source is NOT ISC18 conference template")
    print("=" * 60)
    a = [x for x in not_fmt if "no ISC18" in x["reason"] or x["reason"].startswith("non-ISC18")]
    for i, x in enumerate(a, 1):
        print(f"{i:2}. {x['paper_id']} — {x['toc_author'] or '(missing author)'}")
        print(f"    {x['title']}")
    print()
    print("=" * 60)
    print("B) In booklet via author/submission PDF (not TeX compile)")
    print("=" * 60)
    b = [x for x in not_fmt if "PDF" in x["reason"] or "pdf" in x["reason"]]
    for i, x in enumerate(b, 1):
        tex_path = resolve_tex(x["paper_id"])
        tex_isc = False
        if tex_path:
            tex_isc = is_isc18(read_text(tex_path))
        note = "TeX source IS ISC18" if tex_isc else ("TeX source also non-ISC18" if tex_path else "no TeX")
        print(f"{i:2}. {x['paper_id']} — {x['toc_author'] or '(missing author)'} [{note}]")
        print(f"    {x['title']}")

    out = BOOK / "non_isc18_format_papers.json"
    out.write_text(json.dumps(not_fmt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
