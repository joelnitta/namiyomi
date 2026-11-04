# Building and distributing namiyomi

This guide describes version 0.0.1. Downloadable applications are available from [GitHub Releases](https://github.com/yoneoka-katsuhiro/namiyomi/releases); they include Python and do not require a separate Python installation.

## Platform and signing status

| Package | Target | Signing / validation boundary |
| --- | --- | --- |
| `namiyomi-0.0.1-macOS-arm64-development.zip` | Apple Silicon; macOS 15+ | Ad-hoc signed development application. No Developer ID signature or Apple notarization. Not built for Intel Macs. |
| `namiyomi-0.0.1-Windows-x64.zip` | Windows 10/11 x64 | Experimental, not Authenticode signed. Windows Server 2022 is used for automated build/startup checks; see the corresponding [Actions run](https://github.com/yoneoka-katsuhiro/namiyomi/actions). Full hands-on testing on a physical Windows PC is not complete. |

A successful build, unit test run, or startup check does not establish that every GUI operation works on another computer. In particular, the Windows package still needs hands-on import, assembly, editing, Undo, saving, reopening, recovery, and FASTA-export testing on Windows 10/11.

GitHub hosting and the MIT License do not require Apple Developer Program membership. Developer ID signing and Apple notarization are separate distribution steps. An ad-hoc signature passing `codesign --verify` is **not** evidence of Gatekeeper acceptance. Unsigned Windows executables may also trigger operating-system security warnings. Do not disable security protections as part of installation or testing.

## Get the source

```sh
git clone https://github.com/yoneoka-katsuhiro/namiyomi.git
cd namiyomi
```

Use Python 3.12 with a working Tk 8.6 installation. Match the Python architecture to the application target: arm64 on macOS, x64 on Windows. Check that `python -m tkinter` opens its test window before continuing. Building from source requires an internet connection to install dependencies; normal application use and sequence processing do not.

### macOS source environment

Run these commands from the repository directory in a terminal, using an arm64 Python 3.12 installation:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt
python -m tkinter
python app.py
```

Use **File > Import Previous Project** to open `demo/Demo.json`. Keep all four AB1 files beside it. The demo contains synthetic sequences and trace signals, not biological or instrument data.

### Windows source environment

Run these commands from the repository directory in PowerShell, with x64 Python 3.12 installed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m tkinter
.\.venv\Scripts\python.exe app.py
```

Using the environment's executable directly avoids requiring activation of a PowerShell script. No administrator privileges are needed by these commands.

## Tests and startup checks

From a prepared environment, run:

```sh
python -m unittest discover -q
python app.py --smoke-test smoke-report.json
```

On Windows, replace `python` with `.\.venv\Scripts\python.exe` if the environment is not activated. The startup check creates a temporary editor session and writes a JSON report; it does not open user AB1 files or projects. It checks a narrow startup path, not scientific accuracy or the complete interactive workflow.

The automated workflow uses the synthetic test suite. Never commit private chromatograms, user projects, crash reports containing personal paths, credentials, or private signing material to a public repository.

## Build the macOS ZIP

Requirements: Apple Silicon, macOS 15+, the prepared Python environment, and Apple's command-line developer tools (`clang` and the AppKit framework). Close any running application before choosing to replace its installed copy.

To build without installing or replacing an application:

```sh
source .venv/bin/activate
NAMIYOMI_INSTALL=0 SANGER_PYTHON="$PWD/.venv/bin/python" ./build_macos.command
```

The default path builds the native menu helper, packages the application with PyInstaller, collects third-party notices, applies an ad-hoc signature, verifies it, and writes `release/namiyomi-0.0.1-macOS-arm64-development.zip`. It does not upload the app to Apple.

`NAMIYOMI_INSTALL=1` also installs the application in the current user's `Applications` folder. `SANGER_APP_DIR` can name a different installation folder. The script refuses to replace a running app or an unrelated application. Previous generated application/archive copies are moved to Trash rather than silently overwritten. Only temporary intermediates created by that invocation are removed automatically.

### Optional Developer ID signing and notarization

These steps require the publisher's own Apple Developer Program membership, a usable **Developer ID Application** certificate with its matching private key in the local Keychain, and an authenticated `notarytool` Keychain profile. Do not put passwords, private keys, certificate exports, or API secrets in this repository.

See Apple's [Developer ID overview](https://developer.apple.com/developer-id/), [certificate instructions](https://developer.apple.com/help/account/certificates/create-developer-id-certificates), and [notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow). PyInstaller documents its [macOS signing and hardened-runtime support](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing).

With the identity and profile already configured locally:

```sh
NAMIYOMI_INSTALL=0 \
NAMIYOMI_CODESIGN_IDENTITY='Developer ID Application: YOUR NAME (TEAMID)' \
NAMIYOMI_NOTARY_PROFILE='YOUR_EXISTING_KEYCHAIN_PROFILE' \
NAMIYOMI_NOTARIZE=1 \
./build_macos.command
```

The identity and profile names are placeholders, not credentials. The script requires a unique valid Developer ID Application identity, resolving either its exact name or its SHA-1 fingerprint. It signs nested code, enables hardened runtime, seals the outer bundle with a secure timestamp, and checks Apple's certificate requirements.

Notarization uploads the application bundle to Apple, not AB1 files or projects. The script waits for the result, retrieves the service log, and requires a matching accepted submission without unresolved log issues. It then staples and validates the ticket, verifies the signature, and assesses the app with Gatekeeper. The final ZIP is extracted and assessed again before a `-notarized.zip` is emitted. Signing without notarization is labelled `-signed-unnotarized.zip`.

Treat upload success or an `Accepted` result alone as insufficient release evidence. Review the service log and test the downloaded, extracted package on a second Mac. The published version 0.0.1 development package has **not** completed this Developer ID/notarization path.

## Build the Windows ZIP

Build natively on x64 Windows with Python 3.12 and the pinned dependencies in `requirements-build.txt`. A Windows executable is not produced by running the macOS build or renaming its output.

```powershell
.\build_windows.ps1 -Python ".\.venv\Scripts\python.exe"
```

The script runs tests and a source startup check, builds a windowed application with PyInstaller, collects third-party notices, and writes `release/namiyomi-0.0.1-Windows-x64.zip`. It then extracts the actual ZIP and runs the packaged executable's startup check. It also writes source/binary check reports and a SHA-256 checksum beside the ZIP. It refuses to overwrite an existing release archive.

The ZIP contains a `namiyomi` directory with `namiyomi.exe`, `_internal`, the synthetic `demo` folder, license/citation files, and `START_HERE.txt`. Keep the complete folder together. The macOS package similarly includes `namiyomi.app`, the demo, notices, and introductory instructions in its top-level `namiyomi` folder. The build script does not request administrator rights or change PowerShell execution policy; follow your system's policy if script execution is restricted.

Automated packaging is defined in the repository's [Actions workflows](https://github.com/yoneoka-katsuhiro/namiyomi/actions). Check the run logs and reports for the exact commit being distributed. Automated startup checks on a Windows Server runner must not be described as full validation on a researcher's Windows PC. The current executable is not Authenticode signed.

## Release checks

Before publishing a package:

1. Check the version, target architecture, signing label, unit test results, and actual archive contents.
2. Extract the ZIP into a clean folder. Start that extracted application, not a development copy.
3. Open the synthetic demo. Test AB1 import, manual assembly, source and consensus edits, Undo/Redo, Find, project saving/reopening, and FASTA export.
4. Verify that original AB1 files remain unchanged and that exported sequence identifiers are unique.
5. Include the MIT License, citation metadata, and notices for the dependencies actually bundled.
6. Publish a SHA-256 checksum and clearly state checks not yet completed. Keep Windows experimental until hands-on testing supports a stronger claim.

Project JSON files refer to original AB1 files instead of embedding them. When moving a project between computers, copy those AB1 files too and retain the relative folder structure. Do not place personal sample data in a public release without permission.
