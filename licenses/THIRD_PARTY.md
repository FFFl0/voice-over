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

| Data | Source | License (as published by the authors) |
|---|---|---|
| Speech recognition model Whisper large-v3-turbo (CTranslate2) | Hugging Face | MIT |
| Voice separation model htdemucs | facebookresearch/demucs | MIT |
| Speaker model WeSpeaker voxceleb-resnet34-LM | Hugging Face `Wespeaker/wespeaker-voxceleb-resnet34-LM` | see the model card |
| Spelling dictionaries ru_RU, en_US, uk_UA | LibreOffice/dictionaries | see the README of each dictionary |
