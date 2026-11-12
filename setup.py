"""Build ``nanodirt._quickjs`` (thirdparty/quickjs-ng) and ``nanodirt._mp3`` (thirdparty/minimp3)."""

import sys
import sysconfig

from setuptools import Extension, setup

# The stable ABI (one wheel per platform) needs CPython without free threading.
_stable_abi = sys.implementation.name == "cpython" and not sysconfig.get_config_var(
    "Py_GIL_DISABLED"
)

QJS = "thirdparty/quickjs-ng"
if sys.platform == "win32":  # MSVC; quickjs-ng needs C11 and its atomics
    args = ["/std:c11", "/experimental:c11atomics", "/D_CRT_SECURE_NO_WARNINGS"]
else:
    args = ["-Werror=incompatible-pointer-types"]
if _stable_abi:
    args.append("-DPy_LIMITED_API=0x030A0000")

setup(
    ext_modules=[
        Extension(
            "nanodirt._quickjs",
            sources=[
                "csrc/_quickjs.c",
                f"{QJS}/quickjs.c",
                f"{QJS}/libregexp.c",
                f"{QJS}/libunicode.c",
                f"{QJS}/dtoa.c",
            ],
            include_dirs=[QJS],
            extra_compile_args=args,
            py_limited_api=_stable_abi,
        ),
        Extension(
            "nanodirt._mp3",
            sources=["csrc/_mp3.c"],
            include_dirs=["thirdparty/minimp3"],
            extra_compile_args=args,
            py_limited_api=_stable_abi,
        ),
    ],
    options={"bdist_wheel": {"py_limited_api": "cp310"}} if _stable_abi else {},
)
