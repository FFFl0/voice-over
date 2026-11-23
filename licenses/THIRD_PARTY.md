# Third-party components

VoiceOver Studio is built with the open-source components listed below. Full license texts
are in this folder; the license files of every Python package included in the program are in
`packages/` (collected automatically at build time).

## Included in the program

| Component | License | Where to get the source |
|---|---|---|
| Qt 6 / PySide6 / Shiboken6 | LGPL-3.0 (`LGPL-3.0.txt`, `GPL-3.0.txt`) | https://code.qt.io/ · https://download.qt.io/official_releases/QtForPython/ |
| FFmpeg (`ffmpeg/bin/ffmpeg.exe`) | LGPL-2.1 or later (`LGPL-2.1.txt`, `ffmpeg/`) | see `ffmpeg/SOURCE.txt` |
| NumPy | BSD-3-Clause and others | https://github.com/numpy/numpy |
| python-soundfile; libsndfile | BSD-3-Clause; LGPL-2.1 | https://github.com/bastibe/python-soundfile · https://github.com/libsndfile/libsndfile |
| python-sounddevice; PortAudio | MIT | https://github.com/spatialaudio/python-sounddevice |
| Requests, urllib3, idna, charset-normalizer | Apache-2.0, MIT, BSD-3-Clause, MIT | PyPI |
| certifi | MPL-2.0 (`MPL-2.0.txt`) | https://github.com/certifi/python-certifi |
| spylls | MIT | https://github.com/zverok/spylls |
| faster-whisper | MIT | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | MIT | https://github.com/OpenNMT/CTranslate2 |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| Hugging Face Hub, Tokenizers | Apache-2.0 (`Apache-2.0.txt`) | https://github.com/huggingface |
| PyTorch | BSD-3-Clause | https://github.com/pytorch/pytorch |
| Demucs | MIT | https://github.com/facebookresearch/demucs |
| Manrope font | SIL Open Font License 1.1 (`OFL-1.1-Manrope.txt`) | https://github.com/sharanda/manrope |

Qt and FFmpeg are used as separate dynamic libraries / a separate program. You may replace
them with your own builds of compatible versions: the Qt libraries are the `Qt6*.dll` files
in the program folder, FFmpeg is `ffmpeg/bin/ffmpeg.exe` (or set your own path in
Settings → FFmpeg).

## Downloaded on first use (not included in the program)

These files are downloaded to `%APPDATA%\VoiceOverStudio` when the feature is first used.
They are used unmodified.

### Speaker model — WeSpeaker voxceleb-resnet34-LM

- Source: https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM
- License: Creative Commons Attribution 4.0 International (`CC-BY-4.0.txt`). Per the
  WeSpeaker documentation, models trained on VoxCeleb follow the license of the VoxCeleb dataset.
- Authors: the WeSpeaker project (wenet-e2e), https://github.com/wenet-e2e/wespeaker —
  H. Wang et al., "WeSpeaker: A Research and Production oriented Speaker Embedding Learning
  Toolkit", arXiv:2210.17016.
- Training data: VoxCeleb1/VoxCeleb2 — A. Nagrani, J. S. Chung, A. Zisserman et al.,
  Visual Geometry Group, University of Oxford.
- The model is used unmodified.

### Speech recognition model — Whisper large-v3-turbo (CTranslate2 conversion)

- Whisper by OpenAI, MIT License; CTranslate2 conversion for faster-whisper, MIT License.

### Voice separation model — Demucs htdemucs

- Demucs by Meta AI Research (A. Défossez), https://github.com/facebookresearch/demucs, MIT License.

### Spelling dictionaries — LibreOffice/dictionaries

The full notice of each dictionary (`README_*.txt`) is downloaded together with it and shown in
*About → Third-party components*.

- **Russian (ru_RU)** — Copyright (c) 1997-2008, Alexander I. Lebedev. Redistribution and use
  are permitted under the conditions in `README_ru_RU.txt`; the dictionary is provided "as is",
  without warranty (full disclaimer in `README_ru_RU.txt`).
- **English (en_US)** — from SCOWL, Copyright 2000-2018 by Kevin Atkinson, and other sources:
  Ispell affix file (Geoff Kuenning, BSD license), VarCon (Benjamin Titze), WordNet 1.6
  (Princeton University), the UK Advanced Cryptics Dictionary (J Ross Beresford), and public-domain word lists (Moby, 12Dicts, ENABLE). The complete notices,
  including the UK Advanced Cryptics Dictionary notice verbatim, are in `README_en_US.txt`.
- **Ukrainian (uk_UA)** — Mozilla Public License 1.1 (`MPL-1.1.txt`). Andriy Rysin, 2007–2017;
  based on the dict_uk project (VESUM), https://github.com/brown-uk/dict_uk. Source code of the
  dictionary: https://github.com/LibreOffice/dictionaries/tree/master/uk_UA
