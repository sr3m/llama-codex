# Local development

Open `llama-codex.code-workspace` in VS Code. It includes the entire project and search settings that keep generated files out of everyday navigation.

## Layout

```text
llama-codex.code-workspace
llama-codex.py            # Portable Codex script
tests/                    # Switch and gateway regression tests
vscode-extension/         # llama.cpp Inline source and tests
docs/                     # Project documentation
.vscode/                  # Development tasks and F5 configuration
dist/                     # Optional local VSIX packages and verification reports
local-archive/            # Optional original session records; ignored by Git
```

## Test the Codex script

```bash
python3 tests/run.py
```

Manage an existing installation with `python3 ~/.codex/llama-codex.py`. The script in the repository is a development copy; editing it does not automatically update the service installed on your Mac.

## Test the extension

From the repository root:

```bash
npm --prefix vscode-extension ci --ignore-scripts
npm --prefix vscode-extension test
npm --prefix vscode-extension run test:editor
```

The editor test starts a temporary VS Code profile and checks that an inline suggestion can be accepted in a real editor. It does not change settings in your usual profile.

For debugging, select **Run llama.cpp Inline (F5)** in Run and Debug. This opens an Extension Development Host using `vscode-extension/`. Test suggestions in a trusted project; the extension stays disabled in Restricted Mode.

The **Tasks: Run Task** command provides the same tests and a packaging task.

## Build and install a VSIX

```bash
npm --prefix vscode-extension run package
```

The packaging script writes `llama-cpp-inline-0.1.1.vsix` to the repository root. A local working copy may also keep prebuilt packages in `dist/`. Install the package from **Extensions → … → Install from VSIX…**.

## Git and local records

The repository retains its published history, and `origin` points to `https://github.com/sr3m/llama-codex.git`. Creating a local commit does not publish it automatically.

Local archives, dependencies, and generated packages are ignored by Git. Review `git status` and the diff before publishing. Session records and screenshots can contain personal paths or private information; keep them in `local-archive/` and never force-add them to Git.
