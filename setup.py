"""Build ``nanodirt._quickjs``: quickjs-ng (thirdparty/quickjs-ng) with the binding in csrc/."""

import sys
import sysconfig

from setuptools import Extension, setup

# The stable ABI (one wheel per platform) needs CPython without free threading.
_stable_abi = sys.implementation.name == "cpython" and not sysconfig.get_config_var(
    "Py_GIL_DISABLED"
)

QJS = "thirdparty/quickjs-ng"
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
        )
    ],
    options={"bdist_wheel": {"py_limited_api": "cp310"}} if _stable_abi else {},
)
