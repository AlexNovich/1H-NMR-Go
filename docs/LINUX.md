# Run NMR-go from source on Linux

Use Python 3.11 and run these commands from the source directory. For Debian 12
ARM64, use `requirements/linux-aarch64.lock`. For x86-64 Linux, use
`requirements/linux-x86_64.lock`.

Install your distribution's Python venv and Qt X11 runtime libraries if needed.
On Ubuntu 24.04, the Qt packages used in CI are:

```sh
sudo apt-get install libegl1 libgl1 libopengl0 libx11-xcb1 libxkbcommon0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
  libxcb-render-util0 libxcb-shape0 libxcb-shm0 libxcb-sync1 \
  libxcb-xfixes0 libxcb-xinerama0 libxcb-xinput0 libxcb-xkb1
```

Example for x86-64:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements/linux-x86_64.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/nmr-go
```

Installation downloads Python packages. Once installed, NMR-go opens local
files and runs offline. The ARM64 setup has been exercised on Debian 12.
See the [release checks](https://github.com/AlexNovich/1H-NMR-Go/actions) for the
current Ubuntu x86-64 result.
