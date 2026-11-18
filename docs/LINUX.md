# Install and run NMR-go on Linux

NMR-go runs from source on Linux. These steps cover Ubuntu 24.04 on Intel or
AMD (x86-64) and Debian 12 on Raspberry Pi (ARM64).

## 1. Get the source

Open a terminal. If you have Git, run:

```sh
cd ~
git clone https://github.com/AlexNovich/1H-NMR-Go.git
cd 1H-NMR-Go
```

You can also choose **Code → Download ZIP** on the GitHub page. Extract the ZIP,
place the extracted folder in your home folder, and rename it to `1H-NMR-Go`.
Then run `cd ~/1H-NMR-Go` in your terminal. You should see `pyproject.toml` if
you run `ls` in this folder.

## 2. Install the system libraries

On Ubuntu or Debian, run:

```sh
sudo apt-get update
sudo apt-get install libegl1 libgl1 libopengl0 libx11-xcb1 libxkbcommon0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
  libxcb-render-util0 libxcb-shape0 libxcb-shm0 libxcb-sync1 \
  libxcb-xfixes0 libxcb-xinerama0 libxcb-xinput0 libxcb-xkb1
```

## 3. Install NMR-go

Check your Python version with `python3.11 --version`. If it is available,
create a virtual environment (a folder for NMR-go's Python packages):

```sh
python3.11 -m venv .venv
```

On Debian 12, `sudo apt-get install python3.11 python3.11-venv` supplies both
Python 3.11 and its virtual environment module. On a system with another
default Python version, such as Ubuntu 24.04, you can use uv to create the
environment with Python 3.11. [Install uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
uv venv --python 3.11 --seed .venv
```

Run `uname -m` to check your architecture. For `x86_64`, use the first command
below. For `aarch64`, use the second. Both select the list of required packages
for your computer.

```sh
LOCK=requirements/linux-x86_64.lock
```

```sh
LOCK=requirements/linux-aarch64.lock
```

In that same terminal, install NMR-go:

```sh
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r "$LOCK"
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

## 4. Open the app

From the NMR-go folder, run:

```sh
.venv/bin/nmr-go
```

The window opens with a demo spectrum. To try the included real example,
choose **File → Open ZIP** and open `examples/bruker-1h-example.zip`.

To start NMR-go again later, open a terminal and run:

```sh
cd ~/1H-NMR-Go
.venv/bin/nmr-go
```

Installation downloads Python packages. Once installed, NMR-go opens local
files and runs offline. [See the release checks](https://github.com/AlexNovich/1H-NMR-Go/actions).
