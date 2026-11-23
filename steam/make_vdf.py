"""Создать файлы сборки SteamPipe (app_build / depot_build) в build/steam/.

    python steam/make_vdf.py APP_ID DEPOT_ID [описание сборки]

APP_ID и DEPOT_ID — из Steamworks (Приложение → «Техническая информация» и «Депо»).
Содержимое берётся из dist/VoiceOverStudio (её собирает build.bat steam)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "dist" / "VoiceOverStudio"
OUT = ROOT / "build" / "steam"


def q(s) -> str:
    """Значение VDF в кавычках. Пути — с прямыми косыми: обратные steamcmd может
    принять за экранирование, а Windows понимает оба вида."""
    if isinstance(s, Path):
        s = s.as_posix()
    return '"' + str(s).replace('"', "'") + '"'


def app_build(app_id: str, depot_id: str, desc: str) -> str:
    return "\n".join([
        q("AppBuild"), "{",
        f"\t{q('AppID')} {q(app_id)}",
        f"\t{q('Desc')} {q(desc)}",
        f"\t{q('ContentRoot')} {q(CONTENT)}",
        f"\t{q('BuildOutput')} {q(OUT / 'output')}",
        # SetLive пустой: сборка загружается, а выпускает её человек в Steamworks
        f"\t{q('SetLive')} {q('')}",
        f"\t{q('Preview')} {q('0')}",
        f"\t{q('Depots')}", "\t{",
        f"\t\t{q(depot_id)} {q(f'depot_build_{depot_id}.vdf')}",
        "\t}", "}", ""])


def depot_build(depot_id: str) -> str:
    return "\n".join([
        q("DepotBuild"), "{",
        f"\t{q('DepotID')} {q(depot_id)}",
        f"\t{q('ContentRoot')} {q(CONTENT)}",
        f"\t{q('FileMapping')}", "\t{",
        f"\t\t{q('LocalPath')} {q('*')}",
        f"\t\t{q('DepotPath')} {q('.')}",
        f"\t\t{q('Recursive')} {q('1')}",
        "\t}",
        # отладочные и временные файлы в Steam не нужны
        f"\t{q('FileExclusion')} {q('*.pdb')}",
        f"\t{q('FileExclusion')} {q('*.log')}",
        "}", ""])


def main(argv: list[str]) -> Path:
    if len(argv) < 3 or not argv[1].isdigit() or not argv[2].isdigit():
        raise SystemExit("Использование: python steam/make_vdf.py APP_ID DEPOT_ID [описание]")
    app_id, depot_id = argv[1], argv[2]
    desc = argv[3] if len(argv) > 3 else _version()
    if not (CONTENT / "VoiceOverStudio.exe").is_file():
        print("Внимание: нет", CONTENT / "VoiceOverStudio.exe", "— сначала build.bat steam")
    if not (CONTENT / "_internal" / "voiceover" / "store_edition").is_file():
        print("Внимание: это не сборка для Steam (нет метки store_edition) — нужен build.bat steam")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"app_build_{app_id}.vdf").write_text(app_build(app_id, depot_id, desc), encoding="utf-8")
    (OUT / f"depot_build_{depot_id}.vdf").write_text(depot_build(depot_id), encoding="utf-8")
    print(OUT / f"app_build_{app_id}.vdf")
    return OUT / f"app_build_{app_id}.vdf"


def _version() -> str:
    sys.path.insert(0, str(ROOT))
    from voiceover import __version__
    return f"VoiceOver Studio {__version__}"


if __name__ == "__main__":
    main(sys.argv)
