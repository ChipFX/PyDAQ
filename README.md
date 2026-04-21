# PyDAQ

Copyright &copy; ChipFX  
Authors: PlasmaHH, ChipFX

PyDAQ is a command-line data acquisition tool for the Keysight/Agilent 34970A DAQ switch unit. It streams multi-channel measurements continuously to CSV files, displays live data in a rich terminal interface, and can synchronise the instrument clock with the host system.

---

## Hardware Support

**Main unit:** Keysight/Agilent 34970A

**Supported plug-in modules:**

| Model  | Type                | Channels |
|--------|---------------------|----------|
| 34901A | 20-channel MUX + 2  | 20 + 2   |
| 34902A | 16-channel MUX      | 16       |
| 34903A | 20-channel Actuator | 20       |
| 34907A | Digital I/O         | 5        |

The three module slots are addressed as 100, 200, and 300. Channel numbers within each slot start at 1, so slot 200 channel 3 is addressed as channel 203.

---

## Supported Measurement Modes

| Mode      | Description                         |
|-----------|-------------------------------------|
| `VOLT:DC` | DC voltage                          |
| `VOLT:AC` | AC voltage                          |
| `CURR:DC` | DC current                          |
| `CURR:AC` | AC current                          |
| `FREQ`    | Frequency                           |
| `PER`     | Period                              |
| `RES`     | 2-wire resistance                   |
| `FRES`    | 4-wire resistance                   |
| `TEMP`    | Temperature (thermocouple/RTD/THER) |
| `TOT`     | Totalize (event counter)            |

---

## Features

- Continuous streaming scan to CSV with configurable interval and sample count
- Live terminal display using [Rich](https://github.com/Textualize/rich), showing the last 10 readings in scrolling tables, with formatted engineering units
- Optional live display of a selected channel value on the instrument front panel
- Serial (RS-232) and TCP/IP connectivity (Work in Progress)
- Automatic device detection with a flexible port filter string
- Device clock synchronisation with the host system
- In-scan TCP command server for external SCPI access while a scan is running (Work in Progress)
- Resume an interrupted scan already active on the device (e.g. after a computer crash, or cable interruption)
- Abort a running scan

---

## Getting Started

### Requirements

- Python 3.13 or newer
- A Keysight/Agilent 34970A connected via serial or TCP/IP

### Installation — Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

The `daq` command is now available in your activated environment and linked to this repository.

### Installation — Windows

On Windows 11, `pip install -e .` may not produce a working executable entry point. Install the packages directly and run the script instead:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install pyserial PyYAML rich Pint
```

Then run the tool as:

```bat
python daq.py [options] configfile
```

All examples in this document use `daq` — substitute `python daq.py` where needed on Windows.

### Verifying the connection

To list all detected serial ports and their properties:

```bash
daq -S
```

This prints a table showing the device path, full hardware ID string, description, VID, PID, serial number, location, manufacturer, product name, and interface. Use this output to determine the correct filter string for the `-d` option described below.

---

## Usage

```
daq [options] configfile
daq -S
daq -a  [options]
daq -t [local|utc]  [options]
```

### Command-line options

| Option | Long form | Description |
|--------|-----------|-------------|
| `-S` | `--show` | Print a table of all detected serial ports and exit |
| `-d FILTER` | `--hwid FILTER` | Serial device filter string (see below). Default: `.*` |
| `-b RATE` | `--baudrate RATE` | Serial baud rate. Default: `19200` |
| `-s FMT` | `--serial FMT` | Serial format string, e.g. `8N1`. Default: `8N1` |
| `-H HOST` | `--host HOST` | TCP host to connect to (use instead of serial) |
| `-P PORT` | `--port PORT` | TCP port to connect to (required with `-H`) |
| `-t [MODE]` | `--time [MODE]` | Sync device clock with host. `local` or `utc`. Default: `local` |
| `-a` | `--abort` | Send abort to the device and exit, useful when a detached scan is running |
| `-r` | `--resume` | Resume a scan already active on the device |
| `-L [PORT]` | `--listen [PORT]` | Start TCP command server on specified port. Default: `5025` |
| `-n` | `--no-abort` | Do not send abort when Ctrl-C is pressed |
| `-A` | `--all-abort` | Send abort on any error |
| `-v` | `--verbose` | Increase verbosity. Repeat for higher levels (e.g. `-vvv`) |
| `-C CMD` | `--command CMD` | *(Work in progress)* Direct SCPI command injection |

Arguments can also be read from a file by prefixing the filename with `@` on the command line, e.g. `daq @myargs.txt config.yml`.

---

### Selecting the right serial device with `-d`

The `-d` filter is a regular expression matched against the full hardware ID string of each detected port. This is the same string shown in the `hwid` column of `daq -S` output, and it includes the device path, VID, PID, serial number, location, and more — all in one string.

On Linux, a single instrument is usually easy to identify by its device path. On Windows, COM port numbers are assigned arbitrarily and can change whenever the device is plugged into a different USB port or after a reboot, making them an unreliable identifier. It is strongly recommended to filter by the **device serial number** when one is available — it is stable across reconnects and USB ports.

```bash
# Recommended: match by serial number (stable on both Linux and Windows)
daq -d MY1234567 config.yml

# Match by COM port (fragile on Windows — may change unexpectedly)
daq -d COM4 config.yml

# Match by USB VID, only useful if you're not using multiple similar dongles
daq -d 0x2A8D config.yml
```

When using new interface hardware, always run `daq -S` first to identify the serial number and other fields for your specific device.

---

### Connecting via TCP/IP (Work in Progress)

If the 34970A is connected over a network or GPIB-to-Ethernet interface instead of a serial port, use `-H` and `-P`:

```bash
daq -H 192.168.1.100 -P 5025 config.yml
```

---

## Configuration File

The measurement configuration is a YAML (`.yml`) or JSON (`.json`) file passed as the positional argument. Example configurations are provided in the `configs/` directory.

### Top-level structure

```yaml
init:
  - '*IDN?'

setup:
  - '*IDN?'

config:
  output: data.{starttime}.csv
  timestamp: offset
  with_unit: separate
  delimiter: ;
  clock_sync: local
  headers:
    - "Project: My Test; Operator: Jane; Revision: 1.2"

channels:
  300:
    1:
      name: Volt DUT
      mode: VOLT:DC
      range: AUTO
      nplc: 1
    2:
      name: Temp Ambient
      mode: TEMP
      probe: TCouple
      type: J
      resolution: 0.1

scan:
  interval: '1'
  count: 500
```

---

### `init` and `setup`

Both are lists of SCPI command strings.

- `init` commands are sent to the instrument before channel configuration takes place.
- `setup` commands are sent after channel configuration, immediately before the scan starts.

These sections are useful for instrument-specific preparation steps or to query the device state at startup. Note that SCPI command support varies between instrument models, module types, and firmware versions. Unsupported commands may produce errors depending on the device. By default *IDN? is set in init and setup as some 34970A models/generations have a much higher chance of successfully syncing when that's there as a "touch base" interaction.

---

### `config` section

| Key | Values | Description |
|-----|--------|-------------|
| `output` | filename template | Output CSV path. `{starttime}` is replaced with the scan start timestamp. |
| `timestamp` | `single`, `offset`, `full` | Timestamp columns included in output (see below) |
| `with_unit` | `none`, `inline`, `separate` | How measurement units appear in the output (see below) |
| `delimiter` | any string | CSV column delimiter. Default: `;` |
| `clock_sync` | `local` or `utc` | Sync device clock with host before the scan starts. Omit to skip. |
| `headers` | list of strings | Extra lines written into the CSV header block after the column header row |

#### `timestamp` modes

| Value | Behaviour |
|-------|-----------|
| `single` | One Unix timestamp column per row, taken from the time of the first channel reading |
| `offset` | Row timestamp plus one additional per-channel column showing the intra-scan time offset of each reading relative to the first channel in that row |
| `full` | Row timestamp plus one additional per-channel column showing the absolute Unix timestamp of each individual channel reading |

#### `with_unit` modes

| Value | Behaviour |
|-------|-----------|
| `none` | Raw numeric value only |
| `inline` | Value and unit in the same column, space-separated (e.g. `28.813 degC`) |
| `separate` | Value in its own column, unit in a separate `<name>.unit` column |

---

### `channels` section

Channels are grouped by slot number (`100`, `200`, or `300`), then by channel number within that slot. The full channel ID used internally is `slot + channel` (e.g. slot `300`, channel `2` → channel ID `302`).

#### Common channel keys

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `name` | No | channel ID | Human-readable label used in CSV column headers |
| `mode` | Yes | — | Measurement mode (see Supported Measurement Modes) |
| `enabled` | No | `true` | Set to `false` to skip this channel without removing its config block |
| `display` | No | — | If `true`, this channel's live value is formatted and shown on the instrument front panel. Only one channel may have this set. |

#### Mode-specific keys

**`VOLT:DC`, `VOLT:AC`, `CURR:DC`, `CURR:AC`**

| Key | Default | Description |
|-----|---------|-------------|
| `range` | `AUTO` | Measurement range value, or `AUTO` for autorange |
| `resolution` | `DEF` | Measurement resolution. Must be `DEF` when `range` is `AUTO` |
| `nplc` | — | Number of power line cycles (DC modes only). Higher values give better accuracy at the cost of speed |

**`FREQ`, `PER`, `RES`, `FRES`**

| Key | Default | Description |
|-----|---------|-------------|
| `range` | `AUTO` | Measurement range value, or `AUTO` |
| `resolution` | `DEF` | Measurement resolution |

**`TEMP`**

| Key | Required | Description |
|-----|----------|-------------|
| `probe` | Yes | Probe type: `TCouple`, `RTD`, `FRTD`, `THER`, or `DEF` |
| `type` | Yes | Sensor sub-type (see table below) |
| `resolution` | No | Measurement resolution |

Sensor sub-types by probe type:

| Probe | Valid types |
|-------|-------------|
| `TCouple` | `B`, `E`, `J`, `K`, `N`, `R`, `S`, `T` |
| `RTD` | `85`, `91` |
| `FRTD` | `85`, `91` |
| `THER` | `2252`, `5000`, `10000` |

**`TOT`**

| Key | Default | Description |
|-----|---------|-------------|
| `tmode` | `READ` | Totalize mode: `READ` to read the count, or `RRES` to read and reset |

---

### `scan` section

| Key | Required | Description |
|-----|----------|-------------|
| `interval` | Yes | Scan interval in seconds |
| `count` | No | Number of scan rows to collect before stopping. Default: `0` (run indefinitely) |

---

## CSV Output

The output file is named by the `output` template in the config, with `{starttime}` replaced by the scan start date and time (e.g. `data.2025.06.21 23.37.33.472985.csv`).

The first line of the file is the CSV column header row. Any strings listed in `headers` are written as additional lines immediately after, before the first data row — useful for embedding test metadata, setup descriptions, or revision information directly in the file.

Each data row begins with a Unix timestamp column, followed by one or more columns per channel depending on the `timestamp` and `with_unit` settings.

---

## Command Server (`-L`)

Starting PyDAQ with `-L` (optionally followed by a port number) opens a TCP server on `localhost` at the specified port (default `5025`) that stays active for the duration of the scan. External tools or scripts can connect to this socket and send raw SCPI command strings, one per line. Each command receives its response on the same connection.

This is useful for inspecting instrument state or making ad-hoc adjustments from another process while data collection is running.

```bash
daq -L 5025 config.yml
```

---

## Resuming a Scan (`-r`)

If the 34970A is already actively scanning — for example after a software crash, a lost connection, or a deliberate detach — PyDAQ can attach to the running scan and continue writing data without reconfiguring the instrument:

```bash
daq -r config.yml
```

PyDAQ checks whether the device reports an active scan before proceeding. If no active scan is found, it exits with an error rather than starting a new scan.

---

## Clock Synchronisation (`-t`)

PyDAQ can synchronise the instrument's internal clock with the host system before a scan begins, either via the config file (`clock_sync` key) or directly from the command line:

```bash
# Sync to host local time
daq -t config.yml

# Sync to host UTC time
daq -t utc config.yml
```

The time difference between host and device is printed before the sync is applied.

---

## GUI (coming soon)

A graphical interface (`gui-daq.py`) is under development in a separate branch. It will wrap the command-line system, ensuring that `daq.py` remains fully functional on its own — including on headless systems and servers with no desktop environment.

---

## License

Copyright &copy; ChipFX  
Authors: PlasmaHH, ChipFX
