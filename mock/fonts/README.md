# Device fonts for the fit check

NotoSans and NotoSerif TTFs, exactly as the firmware rasterizes them, copied
from `crosspoint-reader/lib/EpdFont/builtinFonts/source/` at firmware commit
`3d9d9e56`. Licensed under the SIL Open Font License 1.1 (`OFL.txt` in each
folder). `mock/render_dogear.py` reads them from here; `tests/test_vendored.py`
checks they are byte-identical to the firmware's while a firmware checkout sits
beside this repository.
