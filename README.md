<!-- mcp-name: io.github.KoyoYeager/pystub -->

# mcp-pystub

English | [日本語](https://github.com/KoyoYeager/mcp-pystub/blob/master/README.ja.md)

**An MCP server that removes heavy packages from your exe: the ones your dependencies import but your own code never uses.**

For example, an app that only reads and writes MDF files with asammdf still ends up as a 240 MB PyInstaller exe, because asammdf's GUI code imports PySide6 (Qt). mcp-pystub follows usage function by function, even inside your dependencies, replaces the removable parts with minimal stubs, and runs the program to check that the output does not change. The replacement can be undone.

![Demo recorded from a real Claude Code session on the asammdf scenario: add mcp-pystub once, ask Claude, Claude runs analyze and verify and generates the stubs, then pyinstaller --onefile main.py gives a 38.9 MB exe instead of 252.0 MB with the same output; touching a removed part makes the stub print the reason and exit with code 70](https://raw.githubusercontent.com/KoyoYeager/mcp-pystub/master/docs/images/demo.gif)

| Library (example use) | exe (as is) | exe (after) | Removed |
|---|---|---|---|
| asammdf (read/write MDF) | 240.1 MB | **36.9 MB** | asammdf.gui -> PySide6 |
| networkx | 76.7 MB | **40.8 MB** | scipy, fontTools, pandas |
| tqdm | 47.7 MB | **33.3 MB** | pandas, fontTools, PIL |
| matplotlib | 44.6 MB | **36.0 MB** | pandas, PIL.ImageQt |
| openpyxl | 26.2 MB | **20.3 MB** | PIL |
| Pillow | 25.7 MB | **14.4 MB** | numpy |

PyInstaller (onefile), Windows 11, Python 3.13. In every case the exe's output was confirmed identical to the original (conditions: [Verified results](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/results.md)).

## Usage

Add it to Claude Code once, then ask Claude. The GIF above shows the whole flow.

```bash
claude mcp add pystub -- uvx mcp-pystub
```

Then ask in your project folder:

> Use mcp-pystub to make the exe of main.py smaller. The venv is .venv. You can run it to check.

Build as usual (`pyinstaller --onefile main.py`). Afterwards, ask Claude to restore the venv. No uv, or using another client (Claude Desktop etc.)? See [Tools and setup](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/tools.md).

## What is different

- **Function-level tracing**: tells apart packages used only in type annotations, `isinstance` against plain classes, or `except`, and packages used only inside functions you never call.
- **Run to verify**: `verify` plugs in the stubs, actually runs the entry point, and checks that stdout and the exit code are unchanged (the entry point is run only when you explicitly ask).
- **Stops if wrong**: if the code touches a removed part, the stub does not silently change the result; it prints the reason and exits with code 70.
- **Reversible**: original files are backed up and the restore commands are printed.

Static analysis alone cannot promise that nothing breaks for every input, so proposals are candidates and the final check is to run the program. For the scope of the guarantee and the limits, see [Safety and limitations](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/safety.md).

## Documentation

Detailed documents are currently in Japanese.

- [How it works](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/how-it-works.md) (verdicts, confidence, the three layers, execution trace)
- [Safety and limitations](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/safety.md) (scope of the guarantee, differences from a normal run, limitations, safety policy)
- [Tools and setup](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/tools.md) (installation, MCP configuration, tool inputs and outputs)
- [Verified results and performance](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/results.md) (measurement conditions, analysis time and memory)
- [Verdict specification](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/STUB_SAFETY_SPEC.md)
- [Changelog](https://github.com/KoyoYeager/mcp-pystub/blob/master/CHANGELOG.md)
- [Development](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/development.md) (tests, how the README GIF is made)

## License

MIT
