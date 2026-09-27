"""Bundle the GIRepository 3.0 typelib provided by the Linux builder image."""

from PyInstaller.utils.hooks.gi import get_gi_typelibs


binaries, datas, hiddenimports = get_gi_typelibs("GIRepository", "3.0")
