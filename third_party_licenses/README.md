# Runtime license notices

These unmodified upstream texts accompany the bundled Python/Tcl/Tk runtime and
platform-dependent compiler runtime libraries. Application code is licensed
separately under MIT. A notice here does not imply that every platform bundles
every library. Build-specific versions and package notices are recorded in the
application's `ThirdPartyNotices/manifest.json`.

Windows packages also include the actual Python installation's complete
`LICENSE.txt` under `ThirdPartyNotices/python-runtime/`. It contains additional
conditions for the Microsoft and other runtimes in that binary build; the
upstream CPython source license below is not a substitute for that file.

- [Python 3.12 LICENSE](https://github.com/python/cpython/blob/3.12/LICENSE)
- [Tcl 8.6.13 terms](https://github.com/tcltk/tcl/blob/core-8-6-13/license.terms)
- [Tk 8.6.13 terms](https://github.com/tcltk/tk/blob/core-8-6-13/license.terms)
- [TkDND terms](https://github.com/petasis/tkdnd/blob/master/license.terms)
- [GCC GPLv3 text](https://github.com/gcc-mirror/gcc/blob/master/COPYING3)
- [GCC Runtime Library Exception](https://github.com/gcc-mirror/gcc/blob/master/COPYING.RUNTIME)
- [OpenSSL 3.0 license](https://github.com/openssl/openssl/blob/openssl-3.0/LICENSE.txt)

The GCC runtime exception applies to the compiler runtime libraries covered by
that exception; it does not relicense other GPL components. The unused Python
`readline` extension is excluded from the application builds.
