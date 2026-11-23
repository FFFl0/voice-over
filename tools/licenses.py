"""Собрать файлы лицензий Python-пакетов, попавших в сборку (вызывается из voiceover.spec).

По списку модулей сборки (Analysis.pure) находятся пакеты PyPI, и их файлы
LICENSE/COPYING/NOTICE копируются в <out>/<пакет>-<версия>/. Рядом пишется
packages.txt: пакет, версия, лицензия из метаданных."""

from __future__ import annotations

import importlib.metadata as md
import re
import shutil
from pathlib import Path

_LIC = re.compile(r"(licen[cs]e|copying|notice|authors)", re.I)


def distributions_for(modules) -> list[md.Distribution]:
    tops = {m.split(".")[0] for m in modules}
    mapping = md.packages_distributions()
    names = sorted({d for t in tops for d in mapping.get(t, [])}, key=str.lower)
    out = []
    for n in names:
        try:
            out.append(md.distribution(n))
        except md.PackageNotFoundError:
            pass
    return out


def license_of(dist: md.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr
    lic = (meta.get("License") or "").strip()
    if lic and len(lic) < 80 and lic.upper() != "UNKNOWN":
        return lic
    cls = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return ", ".join(cls) or "see license files"


def collect(modules, out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rows = []
    for dist in distributions_for(modules):
        name, ver = dist.metadata["Name"], dist.version
        rows.append(f"{name}\t{ver}\t{license_of(dist)}")
        target = out / f"{name}-{ver}"
        for f in dist.files or []:
            if _LIC.search(f.name) and not str(f).endswith((".py", ".pyc")):
                src = Path(dist.locate_file(f))
                if src.is_file():
                    dst = target / f.name
                    if dst.exists():
                        dst = target / str(f).replace("/", "_")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
    (out / "packages.txt").write_text("Package\tVersion\tLicense\n" + "\n".join(rows) + "\n",
                                      encoding="utf-8")
    return out


if __name__ == "__main__":       # проверка: python tools/licenses.py voiceover
    import sys
    from modulefinder import ModuleFinder
    mf = ModuleFinder()
    mf.run_script(sys.argv[1] if len(sys.argv) > 1 else "run.py")
    print(collect(mf.modules.keys(), Path("build/licenses")))
